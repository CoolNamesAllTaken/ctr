import re
import secrets
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja import Field, Router, Schema, Status
from ninja.errors import HttpError
from ninja.security import APIKeyHeader

from core import delays, fares, live_map, train_status
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
from core.starter_data import load_starter_data


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


@router.get("/stations/", response=list[StationOut])
def list_stations(request):
    return Station.objects.all()


@router.get("/trains/", response=list[TrainOut])
def list_trains(request):
    return Train.objects.order_by("name")


@router.post("/trains/", response={201: TrainOut})
def create_train(request, payload: TrainIn):
    name = payload.name.strip()
    if not name:
        raise HttpError(422, "Train name is required.")

    station_ids = [a.station_id for a in payload.arrivals]
    if len(station_ids) < 2:
        raise HttpError(422, "A train needs at least two stops.")
    if len(set(station_ids)) != len(station_ids):
        raise HttpError(422, "Each station can only appear once.")
    stations = Station.objects.in_bulk(station_ids)
    if len(stations) != len(station_ids):
        raise HttpError(422, "A station on this train no longer exists.")

    traincar = TrainCar.objects.filter(id=payload.traincar_id).first()
    if traincar is None:
        raise HttpError(422, "That train car no longer exists.")

    with transaction.atomic():
        train = Train.objects.create(
            name=name, direction=payload.direction, traincar=traincar
        )
        Arrival.objects.bulk_create(
            Arrival(train=train, station=stations[a.station_id], time=a.time)
            for a in payload.arrivals
        )
    return Status(201, train)


class StarterDataOut(Schema):
    traincars: int
    stations: int
    kiosks: int


@router.post("/starter-data/", response=StarterDataOut)
def create_starter_data(request):
    """Add the starter train cars, stations and kiosks that are missing."""
    try:
        return load_starter_data()
    except IntegrityError as error:
        raise HttpError(
            409,
            "Starter data clashes with existing records (e.g. another station "
            "at the same position). Nothing was loaded.",
        ) from error


class RiderIn(Schema):
    name: str
    costume: str = ""
    card_uid: str
    balance: Decimal = Field(ge=0, max_digits=8, decimal_places=2)


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
def list_riders(request):
    return Rider.objects.select_related("card").order_by("name")


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
    id: int
    name: str
    station_id: int
    station_name: str = Field(alias="station.name")


@router.get("/kiosks/", response=list[KioskOut])
def list_kiosks(request):
    return Kiosk.objects.select_related("station").order_by("station__position", "name")


class TapIn(Schema):
    card_uid: str
    kiosk_id: int


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


@router.post("/taps/", response={201: TapResultOut})
def create_tap(request, payload: TapIn):
    """Simulate a card tap at a kiosk, charging or refunding the fare."""
    try:
        tap, tap_on = fares.tap(normalize_uid(payload.card_uid), payload.kiosk_id)
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
    # service.
    station_id: int | None = None


class TrainStatusOut(TrainRunOut):
    # All null while the train is following the timetable.
    state: TrainStatus.State | None
    station_id: int | None
    station_name: str | None
    updated_at: datetime | None


def get_train(train_id):
    train = Train.objects.filter(id=train_id).first()
    if train is None:
        raise HttpError(422, "That train no longer exists.")
    return train


@router.get("/train-statuses/", response=list[TrainStatusOut])
def list_train_statuses(request):
    """Today's run of every train, with what the conductor last marked it."""
    return train_status.todays_statuses(timezone.now())


@router.post("/train-statuses/", response=TrainStatusOut)
def set_train_status(request, payload: TrainStatusIn):
    """Mark today's run of a train stopped, moving or out of service. The live
    map follows this instead of the timetable."""
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


# Rider-facing, so public on purpose, unlike the employee endpoints above.


class TimetableOut(Schema):
    date: date
    stations: list[StationOut]
    trains: list[TrainRunOut]
    alerts: list[AlertOut]


@router.get("/timetable/", response=TimetableOut, auth=None)
def timetable(request):
    """Today's timetable with live delays, and the active service alerts."""
    today = timezone.localdate()
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
