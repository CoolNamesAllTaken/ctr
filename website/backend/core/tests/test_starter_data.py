import pytest
from django.core.management import call_command
from django.db import IntegrityError

from core.models import Kiosk, Station, TrainCar
from core.starter_data import load_starter_data


@pytest.mark.django_db
def test_load_starter_data():
    created = load_starter_data()

    assert created == {"traincars": 2, "stations": 2, "kiosks": 4}
    assert list(Station.objects.values_list("name", flat=True)) == [
        "San Jose",
        "San Francisco",
    ]
    for station in Station.objects.all():
        assert station.kiosks.count() == 2
    assert Kiosk.objects.filter(id="san-jose-northbound-platform").exists()


@pytest.mark.django_db
def test_load_starter_data_twice_adds_nothing():
    load_starter_data()
    created = load_starter_data()

    assert created == {"traincars": 0, "stations": 0, "kiosks": 0}
    assert TrainCar.objects.count() == 2
    assert Kiosk.objects.count() == 4


@pytest.mark.django_db
def test_load_starter_data_keeps_existing_records():
    TrainCar.objects.create(number="1", capacity=99)

    created = load_starter_data()

    assert created["traincars"] == 1
    assert TrainCar.objects.get(number="1").capacity == 99


@pytest.mark.django_db
def test_load_starter_data_conflict_loads_nothing():
    Station.objects.create(name="Palo Alto", zone=1, position=0)

    with pytest.raises(IntegrityError):
        load_starter_data()

    assert not TrainCar.objects.exists()
    assert Station.objects.count() == 1


@pytest.mark.django_db
def test_load_starter_data_command(capsys):
    call_command("load_starter_data")

    assert capsys.readouterr().out.strip() == (
        "2 traincars, 2 stations, 4 kiosks created."
    )
