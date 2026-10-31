from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from core.models import (
    Arrival,
    ChipperCard,
    Kiosk,
    KioskTap,
    Station,
    Train,
    TrainCar,
)


@pytest.fixture
def line():
    return [
        Station.objects.create(name="San Jose", zone=2, position=0),
        Station.objects.create(name="Palo Alto", zone=2, position=1),
        Station.objects.create(name="San Francisco", zone=1, position=2),
    ]


def names_along_line():
    return list(Station.objects.order_by("position").values_list("name", "position"))


def send(client, method, path, data=None):
    return getattr(client, method)(path, data, content_type="application/json")


@pytest.mark.django_db
def test_create_station_at_north_end(client, line):
    response = send(client, "post", "/api/stations/", {"name": " Oakland ", "zone": 1})

    assert response.status_code == 201
    assert response.json() == {
        "id": Station.objects.get(name="Oakland").id,
        "name": "Oakland",
        "zone": 1,
        "position": 3,
    }


@pytest.mark.django_db
def test_create_first_station(client):
    response = send(client, "post", "/api/stations/", {"name": "San Jose", "zone": 1})

    assert response.json()["position"] == 0


@pytest.mark.django_db
def test_create_station_between_others(client, line):
    send(
        client,
        "post",
        "/api/stations/",
        {"name": "Mountain View", "zone": 2, "position": 1},
    )

    assert names_along_line() == [
        ("San Jose", 0),
        ("Mountain View", 1),
        ("Palo Alto", 2),
        ("San Francisco", 3),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        {"name": " ", "zone": 1},
        {"name": "san jose", "zone": 1},
        {"name": "Oakland", "zone": 0},
        {"name": "Oakland", "zone": 1, "position": -1},
    ],
)
def test_create_station_rejects_invalid(client, line, payload):
    response = send(client, "post", "/api/stations/", payload)

    assert response.status_code == 422
    assert Station.objects.count() == 3


@pytest.mark.django_db
def test_update_station_name_and_zone(client, line):
    response = send(
        client,
        "patch",
        f"/api/stations/{line[1].id}/",
        {"name": "Menlo Park", "zone": 3},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": line[1].id,
        "name": "Menlo Park",
        "zone": 3,
        "position": 1,
    }


@pytest.mark.django_db
def test_update_station_keeps_its_own_name(client, line):
    response = send(
        client, "patch", f"/api/stations/{line[1].id}/", {"name": "Palo Alto"}
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_update_station_rejects_taken_name(client, line):
    response = send(
        client, "patch", f"/api/stations/{line[1].id}/", {"name": "San Jose"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "There's already a station called San Jose."}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("station", "position", "expected"),
    [
        (0, 1, ["Palo Alto", "San Jose", "San Francisco"]),
        (2, 0, ["San Francisco", "San Jose", "Palo Alto"]),
        (0, 99, ["Palo Alto", "San Francisco", "San Jose"]),
    ],
)
def test_move_station(client, line, station, position, expected):
    response = send(
        client,
        "patch",
        f"/api/stations/{line[station].id}/",
        {"position": position},
    )

    assert response.status_code == 200
    assert names_along_line() == [(name, i) for i, name in enumerate(expected)]


@pytest.mark.django_db
def test_delete_station_closes_gap(client, line):
    response = client.delete(f"/api/stations/{line[1].id}/")

    assert response.status_code == 204
    assert names_along_line() == [("San Jose", 0), ("San Francisco", 1)]


@pytest.mark.django_db
def test_delete_station_with_stops_is_refused(client, line):
    car = TrainCar.objects.create(number="1", capacity=20)
    for name in ["102", "101"]:
        train = Train.objects.create(name=name, direction="northbound", traincar=car)
        Arrival.objects.create(train=train, station=line[1], time=time(20, 0))

    response = client.delete(f"/api/stations/{line[1].id}/")

    assert response.status_code == 422
    assert response.json() == {"detail": "Palo Alto is a stop on trains 101, 102."}
    assert Station.objects.count() == 3


@pytest.mark.django_db
def test_delete_station_with_kiosks_is_refused(client, line):
    Kiosk.objects.create(id="pa-nb", name="Northbound", station=line[1])

    response = client.delete(f"/api/stations/{line[1].id}/")

    assert response.status_code == 422
    assert Station.objects.count() == 3


@pytest.mark.django_db
def test_assign_kiosk_to_station(client, line):
    Kiosk.objects.create(id="reader-7", name="reader-7")

    response = send(
        client,
        "put",
        "/api/kiosks/reader-7/",
        {"name": " Northbound platform ", "station_id": line[1].id},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "reader-7",
        "name": "Northbound platform",
        "station_id": line[1].id,
        "station_name": "Palo Alto",
    }


@pytest.mark.django_db
def test_take_kiosk_out_of_service(client, line):
    Kiosk.objects.create(id="pa-nb", name="Northbound", station=line[1])

    response = send(client, "put", "/api/kiosks/pa-nb/", {"name": "Northbound"})

    assert response.json()["station_id"] is None
    assert Kiosk.objects.get().station is None


@pytest.mark.django_db
def test_assign_kiosk_rejects_name_taken_at_station(client, line):
    Kiosk.objects.create(id="pa-nb", name="Northbound", station=line[1])
    Kiosk.objects.create(id="reader-7", name="reader-7")

    response = send(
        client,
        "put",
        "/api/kiosks/reader-7/",
        {"name": "Northbound", "station_id": line[1].id},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Palo Alto already has a kiosk called Northbound."
    }


@pytest.mark.django_db
def test_update_unknown_kiosk(client, line):
    response = send(client, "put", "/api/kiosks/nope/", {"name": "x"})

    assert response.status_code == 422
    assert response.json() == {"detail": "That kiosk no longer exists."}


@pytest.mark.django_db
def test_delete_kiosk(client):
    Kiosk.objects.create(id="reader-7", name="reader-7")

    response = client.delete("/api/kiosks/reader-7/")

    assert response.status_code == 204
    assert not Kiosk.objects.exists()


@pytest.mark.django_db
def test_delete_kiosk_with_taps_is_refused(client, line):
    kiosk = Kiosk.objects.create(id="sj-nb", name="Northbound", station=line[0])
    KioskTap.objects.create(
        card=ChipperCard.objects.create(uid="04a1b2"),
        kiosk=kiosk,
        tap_time=datetime(2026, 10, 31, 20, 0, tzinfo=UTC),
        card_charge=Decimal("6.25"),
        balance_after=Decimal("0"),
    )

    response = client.delete("/api/kiosks/sj-nb/")

    assert response.status_code == 422
    assert Kiosk.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("kiosk_id", ["..", "has space", "a/b", "x" * 65])
def test_tap_rejects_unsafe_kiosk_id(client, kiosk_id):
    response = send(
        client, "post", "/api/taps/", {"card_uid": "04a1b2", "kiosk_id": kiosk_id}
    )

    assert response.status_code == 422
    assert not Kiosk.objects.exists()


@pytest.mark.django_db
def test_tap_accepts_mac_address_kiosk_id(client):
    send(
        client,
        "post",
        "/api/taps/",
        {"card_uid": "04a1b2", "kiosk_id": "AC:67:B2:01:02:03"},
    )

    assert Kiosk.objects.filter(id="AC:67:B2:01:02:03").exists()
