import pytest

from core.models import Station, Train, TrainCar


@pytest.fixture
def car():
    return TrainCar.objects.create(number="1", capacity=20)


@pytest.fixture
def stations():
    return [
        Station.objects.create(name="San Jose", zone=1, position=0),
        Station.objects.create(name="San Francisco", zone=2, position=1),
    ]


def train_payload(car, stations, **overrides):
    return {
        "name": "101",
        "direction": "northbound",
        "traincar_id": car.id,
        "arrivals": [
            {"station_id": stations[0].id, "time": "20:15"},
            {"station_id": stations[1].id, "time": "20:30"},
        ],
        **overrides,
    }


@pytest.mark.django_db
def test_create_train(client, car, stations):
    response = client.post(
        "/api/trains/", train_payload(car, stations), content_type="application/json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "101"
    assert [(a["station_name"], a["time"]) for a in body["arrivals"]] == [
        ("San Jose", "20:15:00"),
        ("San Francisco", "20:30:00"),
    ]
    train = Train.objects.get()
    assert train.traincar == car
    assert train.arrivals.count() == 2


@pytest.mark.django_db
def test_list_trains(client, car, stations):
    client.post(
        "/api/trains/", train_payload(car, stations), content_type="application/json"
    )

    response = client.get("/api/trains/")

    assert response.status_code == 200
    assert [t["name"] for t in response.json()] == ["101"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "  "},
        {"arrivals": []},
        {"direction": "sideways"},
    ],
)
def test_create_train_rejects_invalid(client, car, stations, overrides):
    response = client.post(
        "/api/trains/",
        train_payload(car, stations, **overrides),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert not Train.objects.exists()


@pytest.mark.django_db
def test_create_train_rejects_duplicate_station(client, car, stations):
    arrivals = [
        {"station_id": stations[0].id, "time": "20:15"},
        {"station_id": stations[0].id, "time": "20:30"},
    ]
    response = client.post(
        "/api/trains/",
        train_payload(car, stations, arrivals=arrivals),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert not Train.objects.exists()


@pytest.mark.django_db
def test_create_train_rejects_deleted_station(client, car, stations):
    payload = train_payload(car, stations)
    stations[1].delete()

    response = client.post("/api/trains/", payload, content_type="application/json")

    assert response.status_code == 422
    assert response.json() == {"detail": "A station on this train no longer exists."}
    assert not Train.objects.exists()
