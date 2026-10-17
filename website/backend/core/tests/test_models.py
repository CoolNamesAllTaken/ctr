from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from core.models import (
    Alert,
    Arrival,
    ArrivalInstance,
    ChipperCard,
    Kiosk,
    KioskTap,
    Rider,
    Station,
    Train,
    TrainCar,
)


@pytest.mark.django_db
def test_create_user():
    user = get_user_model().objects.create_user(username="ada", password="pw")

    assert user.check_password("pw")
    assert str(user) == "ada"


@pytest.mark.django_db
def test_transit_models():
    car = TrainCar.objects.create(number="1", capacity=20)
    train = Train.objects.create(
        name="101", direction=Train.Direction.NORTHBOUND, traincar=car
    )
    station = Station.objects.create(name="Loyola", zone=1, position=0)
    kiosk = Kiosk.objects.create(name="North platform", station=station)
    alert = Alert.objects.create(message="Ghosts on the tracks", train=train)
    arrival = Arrival.objects.create(train=train, station=station, time=time(20, 15))
    instance = ArrivalInstance.objects.create(
        arrival=arrival,
        scheduled_at=datetime(2026, 10, 31, 20, 15, tzinfo=UTC),
        current_delay_minutes=3,
        alert=alert,
    )
    card = ChipperCard.objects.create(uid="04a1b2c3d4e5f6", balance=Decimal("7.50"))
    rider = Rider.objects.create(name="Ada", card=card, train=train)
    tap = KioskTap.objects.create(
        card=card,
        kiosk=kiosk,
        tap_time=datetime(2026, 10, 31, 20, 10, tzinfo=UTC),
        card_charge=Decimal("2.50"),
        balance_after=Decimal("7.50"),
    )

    assert list(train.arrivals.all()) == [arrival]
    assert list(train.alerts.all()) == [alert]
    assert list(alert.arrival_instances.all()) == [instance]
    assert rider.card.taps.get() == tap
    assert card.rider == rider
    assert list(station.kiosks.get().taps.all()) == [tap]
    assert str(arrival) == "101 at Loyola 20:15"
