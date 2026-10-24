import re
from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from django.db import IntegrityError, transaction
from ninja import Field, Router, Schema, Status
from ninja.errors import HttpError

from core import fares
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
)
from core.starter_data import load_starter_data

router = Router()


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


# TODO: the employee endpoints below are open (auth=None) until employee login
# exists. Put them back behind auth before this goes anywhere public.


@router.get("/traincars/", response=list[TrainCarOut], auth=None)
def list_traincars(request):
    return TrainCar.objects.order_by("number")


@router.get("/stations/", response=list[StationOut], auth=None)
def list_stations(request):
    return Station.objects.all()


@router.get("/trains/", response=list[TrainOut], auth=None)
def list_trains(request):
    return Train.objects.order_by("name")


@router.post("/trains/", response={201: TrainOut}, auth=None)
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


@router.post("/starter-data/", response=StarterDataOut, auth=None)
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
    card_uid: str
    balance: Decimal = Field(ge=0, max_digits=8, decimal_places=2)


class RiderOut(Schema):
    id: int
    name: str
    card_uid: str | None = Field(None, alias="card.uid")
    balance: Decimal | None = Field(None, alias="card.balance")


def normalize_uid(uid):
    """Card UIDs are stored as lowercase hex; readers often add separators."""
    uid = re.sub(r"[\s:-]", "", uid).lower()
    if not re.fullmatch(r"[0-9a-f]{1,32}", uid):
        raise HttpError(422, "Card UID must be up to 32 hex digits.")
    return uid


@router.get("/riders/", response=list[RiderOut], auth=None)
def list_riders(request):
    return Rider.objects.select_related("card").order_by("name")


@router.post("/riders/", response={201: RiderOut}, auth=None)
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
        rider = Rider.objects.create(name=name, card=card)
    return Status(201, rider)


class KioskOut(Schema):
    id: int
    name: str
    station_id: int
    station_name: str = Field(alias="station.name")


@router.get("/kiosks/", response=list[KioskOut], auth=None)
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


@router.get("/taps/", response=list[TapOut], auth=None)
def list_taps(request, limit: int = 20):
    return KioskTap.objects.select_related("card__rider", "kiosk__station").order_by(
        "-tap_time"
    )[:limit]


@router.post("/taps/", response={201: TapResultOut}, auth=None)
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


@router.get("/alerts/", response=list[AlertOut], auth=None)
def list_alerts(request, limit: int = 20):
    return Alert.objects.select_related("train").order_by("-created_at")[:limit]


@router.post("/alerts/", response={201: AlertOut}, auth=None)
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
