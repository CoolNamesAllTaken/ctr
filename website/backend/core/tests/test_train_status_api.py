from datetime import time

import pytest

from core.models import Arrival, Station, Train, TrainCar, TrainStatus


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
    # Late at night, so the timetable alone wouldn't put it on the map.
    for station, minute in zip(stations, [0, 11, 21], strict=True):
        Arrival.objects.create(train=train, station=station, time=time(3, minute))
    return train


def post(client, data):
    return client.post("/api/train-statuses/", data, content_type="application/json")


def live_trains(client):
    response = client.get("/api/live-map/")
    assert response.status_code == 200
    return response.json()["trains"]


@pytest.mark.django_db
def test_list_train_statuses_follow_timetable(client, train):
    response = client.get("/api/train-statuses/")

    assert response.status_code == 200
    [run] = response.json()
    assert run["train_name"] == "101"
    assert len(run["stops"]) == 3
    assert run["state"] is None
    assert run["station_id"] is None
    assert run["updated_at"] is None


@pytest.mark.django_db
def test_mark_train_stopped(client, train, stations):
    response = post(
        client,
        {"train_id": train.id, "state": "stopped", "station_id": stations[1].id},
    )

    assert response.status_code == 200
    run = response.json()
    assert run["state"] == "stopped"
    assert run["station_name"] == "Palo Alto"
    assert run["updated_at"] is not None

    [located] = live_trains(client)
    assert located["at_station_id"] == stations[1].id
    assert [stop["station_name"] for stop in located["upcoming_stops"]] == [
        "Palo Alto",
        "San Francisco",
    ]


@pytest.mark.django_db
def test_mark_train_moving(client, train, stations):
    post(
        client, {"train_id": train.id, "state": "stopped", "station_id": stations[0].id}
    )
    response = post(
        client,
        {"train_id": train.id, "state": "moving", "station_id": stations[0].id},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "moving"
    # Updated in place, not a second status.
    assert TrainStatus.objects.count() == 1

    [located] = live_trains(client)
    assert located["at_station_id"] is None
    assert located["from_station_id"] == stations[0].id
    assert located["to_station_id"] == stations[1].id
    # Only just left, and never reaches the next stop on its own.
    assert 0 <= located["progress"] < 0.1


@pytest.mark.django_db
def test_mark_train_out_of_service(client, train, stations):
    response = post(
        client,
        {"train_id": train.id, "state": "out_of_service", "station_id": stations[0].id},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "out_of_service"
    assert response.json()["station_id"] is None
    assert live_trains(client) == []


@pytest.mark.django_db
def test_cancel_train(client, train, stations):
    response = post(client, {"train_id": train.id, "state": "canceled"})

    assert response.status_code == 200
    assert response.json()["state"] == "canceled"
    assert response.json()["station_id"] is None
    assert live_trains(client) == []


@pytest.mark.django_db
def test_clear_train_status(client, train, stations):
    post(
        client, {"train_id": train.id, "state": "stopped", "station_id": stations[0].id}
    )

    response = client.delete(f"/api/train-statuses/{train.id}/")

    assert response.status_code == 200
    assert response.json()["state"] is None
    assert not TrainStatus.objects.exists()
    assert live_trains(client) == []


@pytest.mark.django_db
def test_mark_train_at_station_it_skips(client, train):
    elsewhere = Station.objects.create(name="Gilroy", zone=4, position=3)

    response = post(
        client, {"train_id": train.id, "state": "stopped", "station_id": elsewhere.id}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "That train doesn't stop at that station."


@pytest.mark.django_db
def test_mark_train_moving_from_last_stop(client, train, stations):
    response = post(
        client,
        {"train_id": train.id, "state": "moving", "station_id": stations[2].id},
    )

    assert response.status_code == 422
    assert "last stop" in response.json()["detail"]


@pytest.mark.django_db
def test_mark_missing_train(client, stations):
    response = post(
        client, {"train_id": 999, "state": "stopped", "station_id": stations[0].id}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "That train no longer exists."
