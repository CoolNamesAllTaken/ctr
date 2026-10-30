from datetime import time

import pytest

from core.models import Alert, Arrival, Station, Train, TrainCar


@pytest.mark.django_db
def test_timetable(client):
    mv = Station.objects.create(name="Mountain View", zone=2, position=0)
    sf = Station.objects.create(name="San Francisco", zone=1, position=1)
    car = TrainCar.objects.create(number="1", capacity=20)
    train = Train.objects.create(name="101", direction="northbound", traincar=car)
    Arrival.objects.create(train=train, station=mv, time=time(8))
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
    assert [s["name"] for s in body["stations"]] == ["Mountain View", "San Francisco"]
    [run] = body["trains"]
    assert [stop["delay_minutes"] for stop in run["stops"]] == [0, 4]
    assert [alert["message"] for alert in body["alerts"]] == ["Track work"]
    assert body["date"] == run["stops"][0]["scheduled_at"][:10]
