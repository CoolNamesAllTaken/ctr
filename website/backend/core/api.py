import re
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from ninja import Field, Router, Schema, Status
from ninja.errors import HttpError
from ninja.security import APIKeyHeader

from core import dashboard, delays, fares, live_map, train_status
from core.models import (
    Alert,
    Arrival,
    ChipperCard,
    Kiosk,
    KioskTap,
    Rider,
    Station,
    Train,
    TrainCar,
    TrainStatus,
)


class EmployeePassphrase(APIKeyHeader):
    """The employee portal's shared passphrase, which the Next.js server sends
    on every request once an employee has logged in to the portal with it."""

    param_name = "X-Employee-Passphrase"

    def authenticate(self, request, key):
        passphrase = settings.EMPLOYEE_PASSPHRASE
        if (
            passphrase
            and key
            and secrets.compare_digest(key.encode(), passphrase.encode())
        ):
            return True
        return None


# Employee-only unless an endpoint says otherwise with auth=None.
router = Router(auth=EmployeePassphrase())


class HealthOut(Schema):
    status: str


@router.get("/health/", response=HealthOut, auth=None)
def health(request):
    return {"status": "ok"}


class TrainCarOut(Schema):
    id: int
    number: str
    capacity: int


class StationOut(Schema):
    id: int
    name: str
    zone: int
    position: int


class ArrivalIn(Schema):
    station_id: int
    time: time


class ArrivalOut(Schema):
    station_id: int
    station_name: str
    time: time

    @staticmethod
    def resolve_station_name(obj):
        return obj.station.name


class TrainIn(Schema):
    name: str
    direction: Train.Direction
    traincar_id: int
    arrivals: list[ArrivalIn]


class TrainSeriesIn(Schema):
    """Several trains running the same stops, evenly spaced."""

    # The first train's number; the rest count up from it.
    first_number: int = Field(ge=0)
    count: int = Field(ge=1, le=100)
    direction: Train.Direction
    traincar_id: int
    # The stations it stops at, in any order; trains call at them in the order
    # they reach them.
    station_ids: list[int]
    # When the first train leaves its first stop.
    first_departure: time
    minutes_between_trains: int = Field(ge=1, le=24 * 60)
    minutes_between_stops: int = Field(ge=1, le=24 * 60)


class TrainPatch(Schema):
    name: str


class TrainOut(Schema):
    id: int
    name: str
    direction: Train.Direction
    traincar_id: int
    arrivals: list[ArrivalOut]

    @staticmethod
    def resolve_arrivals(obj):
        return obj.arrivals.select_related("station").order_by("time")


@router.get("/traincars/", response=list[TrainCarOut])
def list_traincars(request):
    return TrainCar.objects.order_by("number")


class StationIn(Schema):
    name: str
    zone: int = Field(ge=1, le=99)
    # How many stations along the line from the south end it goes; leave out
    # to add it at the north end.
    position: int | None = Field(None, ge=0)


class StationPatch(Schema):
    name: str | None = None
    zone: int | None = Field(None, ge=1, le=99)
    # Where to move it, counted like StationIn.position.
    position: int | None = Field(None, ge=0)


def get_station(station_id):
    station = Station.objects.filter(id=station_id).first()
    if station is None:
        raise HttpError(422, "That station no longer exists.")
    return station


def clean_station_name(name, station=None):
    name = name.strip()
    if not name:
        raise HttpError(422, "Station name is required.")
    others = Station.objects.exclude(id=station.id) if station else Station.objects
    if others.filter(name__iexact=name).exists():
        raise HttpError(422, f"There's already a station called {name}.")
    return name


def place_station(station, index):
    """Move a saved station to this index along the line, counting from the
    south end, and renumber the whole line from 0 so there are no gaps."""
    line = list(
        Station.objects.select_for_update().exclude(id=station.id).order_by("position")
    )
    line.insert(min(index, len(line)), station)
    # Positions are unique and checked row by row, so first move every station
    # clear of the numbers they're about to take.
    clear = max(s.position for s in line) + len(line) + 1
    for i, s in enumerate(line):
        s.position = clear + i
    Station.objects.bulk_update(line, ["position"])
    for i, s in enumerate(line):
        s.position = i
    Station.objects.bulk_update(line, ["position"])


@router.get("/stations/", response=list[StationOut])
def list_stations(request):
    return Station.objects.all()


@router.post("/stations/", response={201: StationOut})
def create_station(request, payload: StationIn):
    name = clean_station_name(payload.name)
    with transaction.atomic():
        top = Station.objects.aggregate(top=Max("position"))["top"]
        station = Station.objects.create(
            name=name, zone=payload.zone, position=0 if top is None else top + 1
        )
        if payload.position is not None:
            place_station(station, payload.position)
    return Status(201, station)


@router.patch("/stations/{station_id}/", response=StationOut)
def update_station(request, station_id: int, payload: StationPatch):
    """Rename a station, change its zone, or move it along the line."""
    station = get_station(station_id)
    with transaction.atomic():
        if payload.name is not None:
            station.name = clean_station_name(payload.name, station)
        if payload.zone is not None:
            station.zone = payload.zone
        station.save(update_fields=["name", "zone"])
        if payload.position is not None:
            place_station(station, payload.position)
    station.refresh_from_db()
    return station


@router.delete("/stations/{station_id}/", response={204: None})
def delete_station(request, station_id: int):
    """Delete a station that no train stops at and no kiosk is assigned to."""
    station = get_station(station_id)
    trains = Train.objects.filter(arrivals__station=station).distinct().order_by("name")
    if trains:
        names = ", ".join(train.name for train in trains)
        raise HttpError(422, f"{station.name} is a stop on trains {names}.")
    if station.kiosks.exists():
        raise HttpError(
            422, f"{station.name} still has kiosks. Move or remove them first."
        )
    with transaction.atomic():
        station.delete()
        # Close the gap it leaves in the line.
        first = Station.objects.select_for_update().order_by("position").first()
        if first:
            place_station(first, 0)
    return Status(204, None)


@router.get("/trains/", response=list[TrainOut])
def list_trains(request):
    return Train.objects.order_by("name")


def get_train(train_id):
    train = Train.objects.filter(id=train_id).first()
    if train is None:
        raise HttpError(422, "That train no longer exists.")
    return train


def check_train_names_free(names, train=None):
    others = Train.objects.exclude(id=train.id) if train else Train.objects
    taken = sorted(others.filter(name__in=names).values_list("name", flat=True))
    if len(taken) == 1:
        raise HttpError(422, f"There's already a train {taken[0]}.")
    if taken:
        raise HttpError(422, f"There are already trains {', '.join(taken)}.")


def get_traincar(traincar_id):
    traincar = TrainCar.objects.filter(id=traincar_id).first()
    if traincar is None:
        raise HttpError(422, "That train car no longer exists.")
    return traincar


def get_stops(station_ids):
    """The stations a train stops at, by ID."""
    if len(station_ids) < 2:
        raise HttpError(422, "A train needs at least two stops.")
    if len(set(station_ids)) != len(station_ids):
        raise HttpError(422, "Each station can only appear once.")
    stations = Station.objects.in_bulk(station_ids)
    if len(stations) != len(station_ids):
        raise HttpError(422, "A station on this train no longer exists.")
    return stations


@router.post("/trains/", response={201: TrainOut})
def create_train(request, payload: TrainIn):
    name = payload.name.strip()
    if not name:
        raise HttpError(422, "Train name is required.")
    stations = get_stops([a.station_id for a in payload.arrivals])
    traincar = get_traincar(payload.traincar_id)

    with transaction.atomic():
        train = Train.objects.create(
            name=name, direction=payload.direction, traincar=traincar
        )
        Arrival.objects.bulk_create(
            Arrival(train=train, station=stations[a.station_id], time=a.time)
            for a in payload.arrivals
        )
    return Status(201, train)


@router.post("/trains/series/", response={201: list[TrainOut]})
def create_train_series(request, payload: TrainSeriesIn):
    """Add evenly spaced trains, numbered up from the first, each taking the
    same time between stops."""
    stops = sorted(
        get_stops(payload.station_ids).values(), key=lambda station: station.position
    )
    if payload.direction == Train.Direction.SOUTHBOUND:
        stops.reverse()
    traincar = get_traincar(payload.traincar_id)

    # Times are of day, so every train has to finish before midnight.
    start = datetime.combine(date.min, payload.first_departure)
    last_arrival = start + timedelta(
        minutes=payload.minutes_between_trains * (payload.count - 1)
        + payload.minutes_between_stops * (len(stops) - 1)
    )
    if last_arrival.date() != start.date():
        raise HttpError(422, "The last train would run past midnight.")

    names = [str(payload.first_number + i) for i in range(payload.count)]
    check_train_names_free(names)

    with transaction.atomic():
        trains = []
        for i, name in enumerate(names):
            train = Train.objects.create(
                name=name, direction=payload.direction, traincar=traincar
            )
            departure = start + timedelta(minutes=payload.minutes_between_trains * i)
            Arrival.objects.bulk_create(
                Arrival(
                    train=train,
                    station=station,
                    time=(
                        departure + timedelta(minutes=payload.minutes_between_stops * j)
                    ).time(),
                )
                for j, station in enumerate(stops)
            )
            trains.append(train)
    return Status(201, trains)


@router.patch("/trains/{train_id}/", response=TrainOut)
def update_train(request, train_id: int, payload: TrainPatch):
    """Change a train's number."""
    train = get_train(train_id)
    name = payload.name.strip()
    if not name:
        raise HttpError(422, "Train number is required.")
    check_train_names_free([name], train)
    train.name = name
    train.save(update_fields=["name"])
    return train


# What a new Chipper card is loaded with.
STARTING_BALANCE = Decimal("20.00")


class RiderIn(Schema):
    name: str
    costume: str = ""
    card_uid: str
    balance: Decimal = Field(STARTING_BALANCE, ge=0, max_digits=8, decimal_places=2)


class RiderOut(Schema):
    id: int
    name: str
    costume: str
    card_uid: str | None = Field(None, alias="card.uid")
    balance: Decimal | None = Field(None, alias="card.balance")


def normalize_uid(uid):
    """Card UIDs are stored as lowercase hex; readers often add separators."""
    uid = re.sub(r"[\s:-]", "", uid).lower()
    if not re.fullmatch(r"[0-9a-f]{1,32}", uid):
        raise HttpError(422, "Card UID must be up to 32 hex digits.")
    return uid


@router.get("/riders/", response=list[RiderOut])
def list_riders(request, limit: int = 20):
    """The most recently set up riders, newest first."""
    return Rider.objects.select_related("card").order_by("-id")[:limit]


@router.post("/riders/", response={201: RiderOut})
def create_rider(request, payload: RiderIn):
    """Set up a new rider with a new Chipper card."""
    name = payload.name.strip()
    if not name:
        raise HttpError(422, "Rider name is required.")
    uid = normalize_uid(payload.card_uid)
    if ChipperCard.objects.filter(uid=uid).exists():
        raise HttpError(422, "A card with that UID is already set up.")

    with transaction.atomic():
        card = ChipperCard.objects.create(uid=uid, balance=payload.balance)
        rider = Rider.objects.create(
            name=name, costume=payload.costume.strip(), card=card
        )
    return Status(201, rider)


class KioskOut(Schema):
    id: str
    name: str
    # Unset for a kiosk that has tapped but not been assigned a station yet.
    station_id: int | None
    station_name: str | None = Field(None, alias="station.name")


class KioskIn(Schema):
    name: str
    # Leave out to take the kiosk out of service.
    station_id: int | None = None


def normalize_kiosk_id(kiosk_id):
    """Kiosk IDs go in URL paths, so they're kept to URL-safe characters."""
    kiosk_id = kiosk_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", kiosk_id):
        raise HttpError(
            422,
            "Kiosk ID must be up to 64 letters, digits, dots, colons, dashes "
            "and underscores, starting with a letter or digit.",
        )
    return kiosk_id


def get_kiosk(kiosk_id):
    kiosk = Kiosk.objects.filter(id=kiosk_id).first()
    if kiosk is None:
        raise HttpError(422, "That kiosk no longer exists.")
    return kiosk


@router.get("/kiosks/", response=list[KioskOut])
def list_kiosks(request):
    return Kiosk.objects.select_related("station").order_by("station__position", "name")


@router.put("/kiosks/{kiosk_id}/", response=KioskOut)
def update_kiosk(request, kiosk_id: str, payload: KioskIn):
    """Name a kiosk and assign it to a station."""
    kiosk = get_kiosk(kiosk_id)
    name = payload.name.strip()
    if not name:
        raise HttpError(422, "Kiosk name is required.")
    station = None
    if payload.station_id is not None:
        station = get_station(payload.station_id)
        if station.kiosks.exclude(id=kiosk.id).filter(name=name).exists():
            raise HttpError(422, f"{station.name} already has a kiosk called {name}.")
    kiosk.name = name
    kiosk.station = station
    kiosk.save(update_fields=["name", "station"])
    return kiosk


@router.delete("/kiosks/{kiosk_id}/", response={204: None})
def delete_kiosk(request, kiosk_id: str):
    """Delete a kiosk with no taps, e.g. one registered by mistake. A kiosk
    that taps again afterwards is registered again."""
    kiosk = get_kiosk(kiosk_id)
    if kiosk.taps.exists():
        raise HttpError(
            422,
            f"Kiosk {kiosk.id} has taps on record, so it can't be deleted. "
            "Take it out of service instead.",
        )
    kiosk.delete()
    return Status(204, None)


class TapIn(Schema):
    card_uid: str
    # The kiosk's own ID. A kiosk not seen before is registered on its first tap.
    kiosk_id: str


class TapOut(Schema):
    id: int
    kind: Literal["tap_on", "tap_off"]
    card_uid: str = Field(alias="card.uid")
    rider_name: str | None
    station_name: str = Field(alias="kiosk.station.name")
    kiosk_name: str = Field(alias="kiosk.name")
    tap_time: datetime
    card_charge: Decimal
    balance_after: Decimal

    @staticmethod
    def resolve_kind(obj):
        return "tap_on" if obj.card_charge > 0 else "tap_off"

    @staticmethod
    def resolve_rider_name(obj):
        # A missing reverse one-to-one raises an AttributeError subclass.
        rider = getattr(obj.card, "rider", None)
        return rider.name if rider else None


class TapResultOut(Schema):
    tap: TapOut
    # Set on tap-off: where the trip started and what it cost in the end.
    from_station: str | None
    trip_fare: Decimal | None


@router.get("/taps/", response=list[TapOut])
def list_taps(request, limit: int = 20):
    return KioskTap.objects.select_related("card__rider", "kiosk__station").order_by(
        "-tap_time"
    )[:limit]


# Public so kiosks can post taps without the employee passphrase.
@router.post("/taps/", response={201: TapResultOut}, auth=None)
def create_tap(request, payload: TapIn):
    """Record a card tap at a kiosk, charging or refunding the fare."""
    card_uid = normalize_uid(payload.card_uid)
    kiosk_id = normalize_kiosk_id(payload.kiosk_id)
    # Registered outside the tap's transaction, so a new kiosk stays registered
    # even if its first tap is refused.
    kiosk, _ = Kiosk.objects.select_related("station").get_or_create(
        id=kiosk_id, defaults={"name": kiosk_id}
    )
    try:
        tap, tap_on = fares.tap(card_uid, kiosk)
    except fares.FareError as error:
        raise HttpError(422, str(error)) from error
    return Status(
        201,
        {
            "tap": tap,
            "from_station": tap_on.kiosk.station.name if tap_on else None,
            "trip_fare": tap_on.card_charge + tap.card_charge if tap_on else None,
        },
    )


class AlertIn(Schema):
    message: str
    # Leave out for an alert about the whole line.
    train_id: int | None = None


class AlertPatch(Schema):
    # False cancels the alert, taking it down for riders.
    is_active: bool


class AlertOut(Schema):
    id: int
    message: str
    train_id: int | None
    train_name: str | None = Field(None, alias="train.name")
    is_active: bool
    created_at: datetime


@router.get("/alerts/", response=list[AlertOut])
def list_alerts(request, limit: int = 20, active: bool | None = None):
    alerts = Alert.objects.select_related("train").order_by("-created_at")
    if active is not None:
        alerts = alerts.filter(is_active=active)
    return alerts[:limit]


@router.post("/alerts/", response={201: AlertOut})
def create_alert(request, payload: AlertIn):
    """Issue a service alert for one train, or the whole line."""
    message = payload.message.strip()
    if not message:
        raise HttpError(422, "Alert message is required.")

    train = None
    if payload.train_id is not None:
        train = Train.objects.filter(id=payload.train_id).first()
        if train is None:
            raise HttpError(422, "That train no longer exists.")

    return Status(201, Alert.objects.create(message=message, train=train))


@router.patch("/alerts/{alert_id}/", response=AlertOut)
def update_alert(request, alert_id: int, payload: AlertPatch):
    """Cancel an alert, or put a canceled one back up."""
    alert = Alert.objects.select_related("train").filter(id=alert_id).first()
    if alert is None:
        raise HttpError(422, "That alert no longer exists.")
    alert.is_active = payload.is_active
    alert.save(update_fields=["is_active"])
    return alert


class StopDelayOut(Schema):
    station_id: int
    station_name: str
    scheduled_at: datetime
    expected_at: datetime
    delay_minutes: int
    alert_id: int | None
    alert_message: str | None


class TrainRunOut(Schema):
    train_id: int
    train_name: str
    direction: Train.Direction
    stops: list[StopDelayOut]


class DelayIn(Schema):
    train_id: int
    # Zero clears the delay.
    delay_minutes: int = Field(ge=0, le=24 * 60)
    # Where the delay starts; leave out to delay the whole run.
    from_station_id: int | None = None
    # The alert explaining the delay, if any.
    alert_id: int | None = None


@router.get("/delays/", response=list[TrainRunOut])
def list_delays(request):
    """Today's run of every train, with live delays."""
    return delays.train_runs(timezone.localdate())


@router.post("/delays/", response=TrainRunOut)
def set_delay(request, payload: DelayIn):
    """Set the delay on today's run of a train, from a stop onwards."""
    train = Train.objects.filter(id=payload.train_id).first()
    if train is None:
        raise HttpError(422, "That train no longer exists.")
    alert = None
    if payload.alert_id is not None:
        alert = Alert.objects.filter(id=payload.alert_id).first()
        if alert is None:
            raise HttpError(422, "That alert no longer exists.")

    today = timezone.localdate()
    try:
        delays.set_delay(
            train,
            today,
            payload.delay_minutes,
            from_station_id=payload.from_station_id,
            alert=alert,
        )
    except delays.DelayError as error:
        raise HttpError(422, str(error)) from error
    [run] = delays.train_runs(today, Train.objects.filter(id=train.id))
    return run


class TrainStatusIn(Schema):
    train_id: int
    state: TrainStatus.State
    # Where it's stopped, or the stop it's just left. Not needed when out of
    # service or canceled.
    station_id: int | None = None


class TrainStatusOut(TrainRunOut):
    # All null while the train is following the timetable.
    state: TrainStatus.State | None
    station_id: int | None
    station_name: str | None
    updated_at: datetime | None


@router.get("/train-statuses/", response=list[TrainStatusOut])
def list_train_statuses(request):
    """Today's run of every train, with what the conductor last marked it."""
    return train_status.todays_statuses(timezone.now())


@router.post("/train-statuses/", response=TrainStatusOut)
def set_train_status(request, payload: TrainStatusIn):
    """Mark today's run of a train stopped, moving, out of service or canceled.
    The live map follows this instead of the timetable."""
    train = get_train(payload.train_id)
    now = timezone.now()
    try:
        train_status.set_status(train, payload.state, now, payload.station_id)
    except train_status.StatusError as error:
        raise HttpError(422, str(error)) from error
    [run] = train_status.todays_statuses(now, Train.objects.filter(id=train.id))
    return run


@router.delete("/train-statuses/{train_id}/", response=TrainStatusOut)
def clear_train_status(request, train_id: int):
    """Put today's run of a train back on the timetable on the live map."""
    train = get_train(train_id)
    now = timezone.now()
    train_status.clear_status(train, now)
    [run] = train_status.todays_statuses(now, Train.objects.filter(id=train.id))
    return run


class RiderRefOut(Schema):
    card_uid: str
    # None for a card with no rider.
    name: str | None
    costume: str


class TapBucketOut(Schema):
    start: datetime
    tap_ons: int
    tap_offs: int


class KioskActivityOut(Schema):
    kiosk_id: str
    kiosk_name: str
    station_name: str | None
    tap_ons: int
    tap_offs: int
    buckets: list[TapBucketOut]


class FrequentRiderOut(RiderRefOut):
    trips: int


class CostumeCountOut(Schema):
    costume: str
    riders: int


class DashboardOut(Schema):
    as_of: datetime
    taps: int
    trips_completed: int
    # Tapped off where they tapped on.
    trips_cancelled: int
    # Cards tapped tonight.
    riders: int
    riding_now: int
    cards: int
    # How long each kiosk bucket is.
    bucket_minutes: int
    kiosks: list[KioskActivityOut]
    frequent_riders: list[FrequentRiderOut]
    costumes: list[CostumeCountOut]


@router.get("/dashboard/", response=DashboardOut)
def dashboard_view(request, at: datetime | None = None):
    """How the night is going, for the Live tab to poll. Pass `at` to see it
    at another time today."""
    now = at or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now)
    return dashboard.snapshot(now)


# Rider-facing, so public on purpose, unlike the employee endpoints above.


class TimetableOut(Schema):
    date: date
    stations: list[StationOut]
    trains: list[TrainRunOut]
    alerts: list[AlertOut]


# How many upcoming trains a kiosk's sign has room for.
KIOSK_TRAIN_LIMIT = 3


class KioskTrainOut(Schema):
    train_name: str
    # When the train is scheduled to leave the kiosk's station.
    time: time
    delay_minutes: int


@router.get("/timetable/", response=TimetableOut | list[KioskTrainOut], auth=None)
def timetable(request, kiosk_id: str | None = None):
    """Today's timetable with live delays, and the active service alerts.
    Pass a kiosk's ID to get just the next few trains departing from its
    station, soonest first counting delays, in a short form for the kiosk's
    sign."""
    now = timezone.now()
    today = timezone.localdate(now)
    if kiosk_id is not None:
        kiosk = Kiosk.objects.filter(id=kiosk_id.strip()).first()
        if kiosk is None:
            raise HttpError(404, "There's no kiosk with that ID.")
        if kiosk.station_id is None:
            return []
        runs = delays.train_runs(
            today, Train.objects.filter(arrivals__station=kiosk.station_id).distinct()
        )
        # Trains still to leave, by when they're actually expected. A train's
        # last stop is where it ends, so it doesn't depart from there.
        upcoming = sorted(
            (
                (stop["expected_at"], run["train_name"], stop)
                for run in runs
                for stop in run["stops"][:-1]
                if stop["station_id"] == kiosk.station_id and stop["expected_at"] >= now
            ),
            key=lambda item: item[:2],
        )
        return [
            {
                "train_name": train_name,
                "time": timezone.localtime(stop["scheduled_at"]).time(),
                "delay_minutes": stop["delay_minutes"],
            }
            for _, train_name, stop in upcoming[:KIOSK_TRAIN_LIMIT]
        ]
    return {
        "date": today,
        "stations": Station.objects.all(),
        "trains": delays.train_runs(today),
        "alerts": Alert.objects.filter(is_active=True)
        .select_related("train")
        .order_by("-created_at"),
    }


class LiveTrainOut(Schema):
    train_id: int
    train_name: str
    direction: Train.Direction
    # Between from_station and to_station, progress of the way along. At a
    # station, both are that station and at_station_id is set.
    from_station_id: int
    to_station_id: int
    progress: float
    at_station_id: int | None
    delay_minutes: int
    upcoming_stops: list[StopDelayOut]


class LiveMapOut(Schema):
    as_of: datetime
    stations: list[StationOut]
    trains: list[LiveTrainOut]


@router.get("/live-map/", response=LiveMapOut, auth=None)
def live_map_view(request, at: datetime | None = None):
    """Where each train is right now, for the live map to poll. Pass `at` to
    see the map at another time today, e.g. for a demo."""
    now = at or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now)
    return {
        "as_of": now,
        "stations": Station.objects.all(),
        "trains": live_map.train_locations(now),
    }
