import pytest
from django.core.management import call_command

from core.models import Kiosk, Station, TrainCar


@pytest.mark.django_db
def test_load_starter_data(client):
    response = client.post("/api/starter-data/")

    assert response.status_code == 200
    assert response.json() == {"traincars": 2, "stations": 2, "kiosks": 4}
    assert list(Station.objects.values_list("name", flat=True)) == [
        "Mountain View",
        "San Francisco",
    ]
    for station in Station.objects.all():
        assert station.kiosks.count() == 2


@pytest.mark.django_db
def test_load_starter_data_twice_adds_nothing(client):
    client.post("/api/starter-data/")
    response = client.post("/api/starter-data/")

    assert response.json() == {"traincars": 0, "stations": 0, "kiosks": 0}
    assert TrainCar.objects.count() == 2
    assert Kiosk.objects.count() == 4


@pytest.mark.django_db
def test_load_starter_data_keeps_existing_records(client):
    TrainCar.objects.create(number="1", capacity=99)

    response = client.post("/api/starter-data/")

    assert response.json()["traincars"] == 1
    assert TrainCar.objects.get(number="1").capacity == 99


@pytest.mark.django_db
def test_load_starter_data_conflict_loads_nothing(client):
    Station.objects.create(name="Palo Alto", zone=1, position=0)

    response = client.post("/api/starter-data/")

    assert response.status_code == 409
    assert not TrainCar.objects.exists()
    assert Station.objects.count() == 1


@pytest.mark.django_db
def test_load_starter_data_command(capsys):
    call_command("load_starter_data")

    assert capsys.readouterr().out.strip() == (
        "2 traincars, 2 stations, 4 kiosks created."
    )
