from datetime import time

import pytest

from core.models import Alert, Arrival, Kiosk, Station, Train, TrainCar


@pytest.mark.django_db
def test_timetable(client):
    sj = Station.objects.create(name="San Jose", zone=2, position=0)
    sf = Station.objects.create(name="San Francisco", zone=1, position=1)
    car = TrainCar.objects.create(number="1", capacity=20)
    train = Train.objects.create(name="101", direction="northbound", traincar=car)
    Arrival.objects.create(train=train, station=sj, time=time(8))
    Arrival.objects.create(train=train, station=sf, time=time(9))
    Alert.objects.create(message="Old", is_active=False)
    Alert.objects.create(message="Track work")
    client.post(
        "/api/delays/",
        {"train_id": train.id, "delay_minutes": 4, "from_station_id": sf.id},
        content_type="application/json",
    )

    response = client.get("/api/timetable/")

    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["stations"]] == ["San Jose", "San Francisco"]
    [run] = body["trains"]
    assert [stop["delay_minutes"] for stop in run["stops"]] == [0, 4]
    assert [alert["message"] for alert in body["alerts"]] == ["Track work"]
    assert body["date"] == run["stops"][0]["scheduled_at"][:10]


@pytest.mark.django_db
def test_timetable_for_kiosk(anonymous_client, client):
    sj = Station.objects.create(name="San Jose", zone=2, position=0)
    sf = Station.objects.create(name="San Francisco", zone=1, position=1)
    car = TrainCar.objects.create(number="1", capacity=20)
    late = Train.objects.create(name="103", direction="northbound", traincar=car)
    Arrival.objects.create(train=late, station=sj, time=time(10))
    Arrival.objects.create(train=late, station=sf, time=time(11))
    early = Train.objects.create(name="101", direction="northbound", traincar=car)
    Arrival.objects.create(train=early, station=sj, time=time(8))
    Arrival.objects.create(train=early, station=sf, time=time(9))
    elsewhere = Train.objects.create(name="201", direction="southbound", traincar=car)
    Arrival.objects.create(train=elsewhere, station=sj, time=time(12))
    Kiosk.objects.create(id="kiosk-sf", name="Platform", station=sf)
    client.post(
        "/api/delays/",
        {"train_id": late.id, "delay_minutes": 4, "from_station_id": sf.id},
        content_type="application/json",
    )

    response = anonymous_client.get("/api/timetable/?kiosk_id=kiosk-sf")

    assert response.status_code == 200
    assert response.json() == [
        {"train_name": "101", "time": "09:00:00", "delay_minutes": 0},
        {"train_name": "103", "time": "11:00:00", "delay_minutes": 4},
    ]


@pytest.mark.django_db
def test_timetable_for_kiosk_without_station(anonymous_client):
    Kiosk.objects.create(id="kiosk-new", name="New")

    response = anonymous_client.get("/api/timetable/?kiosk_id=kiosk-new")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_timetable_for_unknown_kiosk(anonymous_client):
    response = anonymous_client.get("/api/timetable/?kiosk_id=nope")

    assert response.status_code == 404
