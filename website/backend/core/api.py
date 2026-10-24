from datetime import time

from django.db import IntegrityError, transaction
from ninja import Router, Schema, Status
from ninja.errors import HttpError

from core.models import Arrival, Station, Train, TrainCar
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
