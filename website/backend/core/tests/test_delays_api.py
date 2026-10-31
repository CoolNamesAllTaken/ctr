from datetime import datetime, time

import pytest
from django.utils import timezone

from core.models import Alert, Arrival, ArrivalInstance, Station, Train, TrainCar


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
    for station, hour in zip(stations, [8, 9, 10], strict=True):
        Arrival.objects.create(train=train, station=station, time=time(hour))
    return train


def post(client, data):
    return client.post("/api/delays/", data, content_type="application/json")


def delays(run):
    return [(stop["station_name"], stop["delay_minutes"]) for stop in run["stops"]]


@pytest.mark.django_db
def test_list_delays_on_time(client, train):
    response = client.get("/api/delays/")

    assert response.status_code == 200
    [run] = response.json()
    assert run["train_name"] == "101"
    assert delays(run) == [
        ("San Jose", 0),
        ("Palo Alto", 0),
        ("San Francisco", 0),
    ]
    stop = run["stops"][0]
    assert stop["expected_at"] == stop["scheduled_at"]
    assert stop["alert_id"] is None


@pytest.mark.django_db
def test_list_delays_orders_by_departure(client, train, stations):
    early = Train.objects.create(
        name="99", direction="southbound", traincar=train.traincar
    )
    Arrival.objects.create(train=early, station=stations[2], time=time(6))

    response = client.get("/api/delays/")

    assert [run["train_name"] for run in response.json()] == ["99", "101"]


@pytest.mark.django_db
def test_list_delays_upcoming(client, train, stations, monkeypatch):
    now = timezone.make_aware(datetime(2026, 10, 31, 10, 30))
    monkeypatch.setattr(timezone, "now", lambda: now)
    car = train.traincar

    def run(name, ends_at):
        run = Train.objects.create(name=name, direction="southbound", traincar=car)
        Arrival.objects.create(train=run, station=stations[2], time=time(6))
        Arrival.objects.create(train=run, station=stations[0], time=ends_at)
        return run

    # 101 finished at 10:00.
    run("99", time(10, 45))
    Train.objects.create(name="no stops", direction="northbound", traincar=car)
    # Due to finish at 10:00 too, but running late puts it at 10:30.
    late = run("103", time(10))
    post(client, {"train_id": late.id, "delay_minutes": 30})

    response = client.get("/api/delays/?upcoming=true")

    assert [run["train_name"] for run in response.json()] == ["99", "103"]


@pytest.mark.django_db
def test_set_delay_from_a_stop(client, train, stations):
    alert = Alert.objects.create(message="Signal problem", train=train)

    response = post(
        client,
        {
            "train_id": train.id,
            "delay_minutes": 15,
            "from_station_id": stations[1].id,
            "alert_id": alert.id,
        },
    )

    assert response.status_code == 200
    run = response.json()
    assert delays(run) == [
        ("San Jose", 0),
        ("Palo Alto", 15),
        ("San Francisco", 15),
    ]
    stop = run["stops"][1]
    assert stop["alert_message"] == "Signal problem"
    assert ArrivalInstance.objects.count() == 2
    assert client.get("/api/delays/").json() == [run]


@pytest.mark.django_db
def test_set_delay_updates_and_clears(client, train):
    post(client, {"train_id": train.id, "delay_minutes": 5})
    post(client, {"train_id": train.id, "delay_minutes": 0})

    [run] = client.get("/api/delays/").json()
    assert delays(run) == [
        ("San Jose", 0),
        ("Palo Alto", 0),
        ("San Francisco", 0),
    ]
    # Updated in place rather than duplicated.
    assert ArrivalInstance.objects.count() == 3


@pytest.mark.django_db
def test_set_delay_with_line_wide_alert(client, train):
    alert = Alert.objects.create(message="Track work")

    response = post(
        client, {"train_id": train.id, "delay_minutes": 5, "alert_id": alert.id}
    )

    assert response.status_code == 200
    assert response.json()["stops"][0]["alert_id"] == alert.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"delay_minutes": -1},
        {"train_id": 999},
        {"from_station_id": 999},
        {"alert_id": 999},
        {"other_train_alert": True},
    ],
)
def test_set_delay_rejects_invalid(client, train, overrides):
    payload = {"train_id": train.id, "delay_minutes": 5}
    if overrides.pop("other_train_alert", False):
        other = Train.objects.create(
            name="102", direction="southbound", traincar=train.traincar
        )
        overrides["alert_id"] = Alert.objects.create(message="x", train=other).id

    response = post(client, {**payload, **overrides})

    assert response.status_code == 422
    assert not ArrivalInstance.objects.exists()


@pytest.mark.django_db
def test_list_alerts_filters_active(client):
    Alert.objects.create(message="Old", is_active=False)
    Alert.objects.create(message="Current")

    response = client.get("/api/alerts/?active=true")

    assert [alert["message"] for alert in response.json()] == ["Current"]
