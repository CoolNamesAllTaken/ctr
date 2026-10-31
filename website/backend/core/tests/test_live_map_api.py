from datetime import datetime, time

import pytest
from django.utils import timezone

from core.models import Arrival, Station, Train, TrainCar


@pytest.fixture
def stations():
    return [
        Station.objects.create(name="San Jose", zone=2, position=0),
        Station.objects.create(name="Palo Alto", zone=2, position=1),
        Station.objects.create(name="San Francisco", zone=1, position=2),
    ]


@pytest.fixture
def train(stations):
    car = TrainCar.objects.create(number="1", capacity=20)
    train = Train.objects.create(name="101", direction="northbound", traincar=car)
    for station, minute in zip(stations, [0, 11, 21], strict=True):
        Arrival.objects.create(train=train, station=station, time=time(8, minute))
    return train


def live_map(client, hour, minute, second=0):
    at = datetime.combine(timezone.localdate(), time(hour, minute, second))
    response = client.get("/api/live-map/", {"at": timezone.make_aware(at).isoformat()})
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db
def test_live_map_lists_stations(client, stations):
    body = live_map(client, 8, 0)

    assert [s["name"] for s in body["stations"]] == [
        "San Jose",
        "Palo Alto",
        "San Francisco",
    ]
    assert body["trains"] == []


@pytest.mark.django_db
@pytest.mark.parametrize(("hour", "minute"), [(7, 58), (8, 22)])
def test_live_map_hides_trains_off_the_line(client, train, hour, minute):
    assert live_map(client, hour, minute)["trains"] == []


@pytest.mark.django_db
def test_live_map_train_boarding_at_first_stop(client, train, stations):
    [located] = live_map(client, 7, 59, 30)["trains"]

    assert located["train_name"] == "101"
    assert located["at_station_id"] == stations[0].id
    assert located["from_station_id"] == located["to_station_id"] == stations[0].id
    assert [s["station_name"] for s in located["upcoming_stops"]] == [
        "San Jose",
        "Palo Alto",
        "San Francisco",
    ]


@pytest.mark.django_db
def test_live_map_train_between_stops(client, train, stations):
    # It leaves San Jose at 8:00 and pulls into Palo Alto a minute before
    # its 8:11 departure, so 8:05 is halfway.
    [located] = live_map(client, 8, 5)["trains"]

    assert located["at_station_id"] is None
    assert located["from_station_id"] == stations[0].id
    assert located["to_station_id"] == stations[1].id
    assert located["progress"] == 0.5
    assert [s["station_name"] for s in located["upcoming_stops"]] == [
        "Palo Alto",
        "San Francisco",
    ]


@pytest.mark.django_db
def test_live_map_follows_delays(client, train, stations):
    client.post(
        "/api/delays/",
        {"train_id": train.id, "delay_minutes": 5},
        content_type="application/json",
    )

    # Now boarding for its 8:05 departure.
    [located] = live_map(client, 8, 4, 30)["trains"]

    assert located["at_station_id"] == stations[0].id
    assert located["delay_minutes"] == 5
