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


def series_payload(car, stations, **overrides):
    return {
        "first_number": 101,
        "count": 3,
        "direction": "northbound",
        "traincar_id": car.id,
        "station_ids": [s.id for s in stations],
        "first_departure": "18:00",
        "minutes_between_trains": 30,
        "minutes_between_stops": 15,
        **overrides,
    }


def post_series(client, payload):
    return client.post("/api/trains/series/", payload, content_type="application/json")


def timetable(body):
    return [
        (t["name"], [(a["station_name"], a["time"]) for a in t["arrivals"]])
        for t in body
    ]


@pytest.mark.django_db
def test_create_train_series(client, car, stations):
    response = post_series(client, series_payload(car, stations))

    assert response.status_code == 201
    assert timetable(response.json()) == [
        ("101", [("San Jose", "18:00:00"), ("San Francisco", "18:15:00")]),
        ("102", [("San Jose", "18:30:00"), ("San Francisco", "18:45:00")]),
        ("103", [("San Jose", "19:00:00"), ("San Francisco", "19:15:00")]),
    ]
    assert Train.objects.filter(traincar=car, direction="northbound").count() == 3


@pytest.mark.django_db
def test_create_southbound_series_stops_north_first(client, car, stations):
    stations[1].position = 2
    stations[1].save()
    middle = Station.objects.create(name="Palo Alto", zone=1, position=1)

    response = post_series(
        client,
        series_payload(
            car,
            [stations[0], stations[1], middle],
            count=1,
            direction="southbound",
        ),
    )

    assert response.status_code == 201
    assert timetable(response.json()) == [
        (
            "101",
            [
                ("San Francisco", "18:00:00"),
                ("Palo Alto", "18:15:00"),
                ("San Jose", "18:30:00"),
            ],
        ),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"first_departure": "23:00"}, "The last train would run past midnight."),
        ({"station_ids": []}, "A train needs at least two stops."),
        ({"traincar_id": 999}, "That train car no longer exists."),
        ({"count": 0}, None),
        ({"minutes_between_stops": 0}, None),
    ],
)
def test_create_train_series_rejects_invalid(client, car, stations, overrides, detail):
    response = post_series(client, series_payload(car, stations, **overrides))

    assert response.status_code == 422
    if detail:
        assert response.json() == {"detail": detail}
    assert not Train.objects.exists()


@pytest.mark.django_db
def test_create_train_series_rejects_taken_numbers(client, car, stations):
    Train.objects.create(name="102", direction="northbound", traincar=car)
    Train.objects.create(name="103", direction="southbound", traincar=car)

    response = post_series(client, series_payload(car, stations))

    assert response.status_code == 422
    assert response.json() == {"detail": "There are already trains 102, 103."}
    assert Train.objects.count() == 2


@pytest.mark.django_db
def test_rename_train(client, car, stations):
    [train] = post_series(client, series_payload(car, stations, count=1)).json()

    response = client.patch(
        f"/api/trains/{train['id']}/",
        {"name": " 201 "},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["name"] == "201"
    assert len(response.json()["arrivals"]) == 2
    assert Train.objects.get().name == "201"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("102", "There's already a train 102."),
        ("  ", "Train number is required."),
    ],
)
def test_rename_train_rejects_invalid(client, car, stations, name, detail):
    first, _ = post_series(client, series_payload(car, stations, count=2)).json()

    response = client.patch(
        f"/api/trains/{first['id']}/", {"name": name}, content_type="application/json"
    )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert Train.objects.get(id=first["id"]).name == "101"


@pytest.mark.django_db
def test_rename_train_to_its_own_number(client, car, stations):
    [train] = post_series(client, series_payload(car, stations, count=1)).json()

    response = client.patch(
        f"/api/trains/{train['id']}/", {"name": "101"}, content_type="application/json"
    )

    assert response.status_code == 200
