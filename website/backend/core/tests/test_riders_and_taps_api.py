from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import ChipperCard, Kiosk, KioskTap, Rider, Station


@pytest.fixture
def kiosks():
    """Two zones, so the max fare is $3.75 + $2.50 = $6.25."""
    sj = Station.objects.create(name="San Jose", zone=2, position=0)
    pa = Station.objects.create(name="Palo Alto", zone=2, position=1)
    sf = Station.objects.create(name="San Francisco", zone=1, position=2)
    return {
        "sj": Kiosk.objects.create(id="sj-nb", name="Northbound platform", station=sj),
        "sj2": Kiosk.objects.create(id="sj-sb", name="Southbound platform", station=sj),
        "pa": Kiosk.objects.create(id="pa-nb", name="Northbound platform", station=pa),
        "sf": Kiosk.objects.create(id="sf-nb", name="Northbound platform", station=sf),
    }


@pytest.fixture
def card():
    card = ChipperCard.objects.create(uid="04a1b2", balance=Decimal("20.00"))
    Rider.objects.create(name="Ada", card=card)
    return card


def post(client, path, data):
    return client.post(path, data, content_type="application/json")


def tap(client, card, kiosk):
    return post(client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": kiosk.id})


@pytest.mark.django_db
def test_create_rider_with_card(client):
    response = post(
        client,
        "/api/riders/",
        {
            "name": " Ada ",
            "costume": " Witch ",
            "card_uid": "04:A1:B2",
            "balance": "20.00",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": Rider.objects.get().id,
        "name": "Ada",
        "costume": "Witch",
        "card_uid": "04a1b2",
        "balance": "20.00",
    }
    assert Rider.objects.get().card.uid == "04a1b2"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [{"name": ""}, {"card_uid": "not hex"}, {"card_uid": "04a1b2"}, {"balance": "-1"}],
)
def test_create_rider_rejects_invalid(client, card, overrides):
    payload = {"name": "Bo", "card_uid": "ffff", "balance": "5", **overrides}

    response = post(client, "/api/riders/", payload)

    assert response.status_code == 422
    assert Rider.objects.count() == 1


@pytest.mark.django_db
def test_list_riders(client, card):
    Rider.objects.create(name="Bo")

    response = client.get("/api/riders/")

    assert [(r["name"], r["card_uid"]) for r in response.json()] == [
        ("Ada", "04a1b2"),
        ("Bo", None),
    ]


@pytest.mark.django_db
def test_tap_on_charges_max_fare(client, kiosks, card):
    response = tap(client, card, kiosks["sj"])

    assert response.status_code == 201
    body = response.json()
    assert body["tap"]["kind"] == "tap_on"
    assert body["tap"]["rider_name"] == "Ada"
    assert body["tap"]["card_charge"] == "6.25"
    assert body["trip_fare"] is None
    card.refresh_from_db()
    assert card.balance == Decimal("13.75")


@pytest.mark.django_db
def test_tap_off_across_zones_pays_max_fare(client, kiosks, card):
    tap(client, card, kiosks["sj"])

    body = tap(client, card, kiosks["sf"]).json()

    assert body["tap"]["kind"] == "tap_off"
    assert body["tap"]["card_charge"] == "0.00"
    assert body["from_station"] == "San Jose"
    assert body["trip_fare"] == "6.25"
    card.refresh_from_db()
    assert card.balance == Decimal("13.75")


@pytest.mark.django_db
def test_tap_off_within_a_zone_refunds(client, kiosks, card):
    tap(client, card, kiosks["sj"])

    body = tap(client, card, kiosks["pa"]).json()

    assert body["tap"]["card_charge"] == "-2.50"
    assert body["trip_fare"] == "3.75"
    card.refresh_from_db()
    assert card.balance == Decimal("16.25")
    assert card.taps.order_by("-tap_time").first().balance_after == card.balance


@pytest.mark.django_db
def test_tap_off_at_same_station_cancels_trip(client, kiosks, card):
    tap(client, card, kiosks["sj"])

    body = tap(client, card, kiosks["sj2"]).json()

    assert body["tap"]["kind"] == "tap_off"
    assert body["tap"]["card_charge"] == "-6.25"
    assert body["trip_fare"] == "0.00"
    card.refresh_from_db()
    assert card.balance == Decimal("20.00")


@pytest.mark.django_db
def test_tap_after_tap_off_starts_new_trip(client, kiosks, card):
    tap(client, card, kiosks["sj"])
    tap(client, card, kiosks["sf"])

    assert tap(client, card, kiosks["sf"]).json()["tap"]["kind"] == "tap_on"


@pytest.mark.django_db
def test_expired_tap_on_starts_new_trip(client, kiosks, card):
    tap(client, card, kiosks["sj"])
    KioskTap.objects.update(tap_time=timezone.now() - timedelta(hours=5))

    body = tap(client, card, kiosks["sf"]).json()

    assert body["tap"]["kind"] == "tap_on"
    card.refresh_from_db()
    assert card.balance == Decimal("7.50")


@pytest.mark.django_db
def test_tap_on_needs_max_fare_balance(client, kiosks, card):
    ChipperCard.objects.update(balance=Decimal("6.00"))

    response = tap(client, card, kiosks["sj"])

    assert response.status_code == 422
    assert "below the $6.25" in response.json()["detail"]
    assert not KioskTap.objects.exists()


@pytest.mark.django_db
def test_tap_unknown_card(client, kiosks):
    response = post(
        client, "/api/taps/", {"card_uid": "beef", "kiosk_id": kiosks["sj"].id}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "No Chipper card with that UID."}


@pytest.mark.django_db
def test_tap_at_new_kiosk_registers_it(client, kiosks, card):
    response = post(
        client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": " reader-7 "}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Kiosk reader-7 isn't assigned to a station."}
    kiosk = Kiosk.objects.get(id="reader-7")
    assert (kiosk.name, kiosk.station) == ("reader-7", None)
    assert not KioskTap.objects.exists()
    card.refresh_from_db()
    assert card.balance == Decimal("20.00")


@pytest.mark.django_db
def test_tap_at_registered_kiosk_once_assigned(client, kiosks, card):
    post(client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": "reader-7"})
    Kiosk.objects.filter(id="reader-7").update(station=kiosks["sf"].station)

    response = post(
        client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": "reader-7"}
    )

    assert response.status_code == 201
    assert response.json()["tap"]["station_name"] == "San Francisco"
    assert Kiosk.objects.count() == 5


@pytest.mark.django_db
def test_tap_unknown_card_still_registers_kiosk(client):
    response = post(client, "/api/taps/", {"card_uid": "beef", "kiosk_id": "new"})

    assert response.status_code == 422
    assert Kiosk.objects.filter(id="new").exists()


@pytest.mark.django_db
def test_tap_rejects_blank_kiosk_id(client, card):
    response = post(client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": " "})

    assert response.status_code == 422
    assert not Kiosk.objects.exists()


@pytest.mark.django_db
def test_list_kiosks_includes_unassigned(client, kiosks, card):
    post(client, "/api/taps/", {"card_uid": card.uid, "kiosk_id": "reader-7"})

    response = client.get("/api/kiosks/")

    assert response.json()[0] == {
        "id": "sj-nb",
        "name": "Northbound platform",
        "station_id": kiosks["sj"].station_id,
        "station_name": "San Jose",
    }
    assert response.json()[-1] == {
        "id": "reader-7",
        "name": "reader-7",
        "station_id": None,
        "station_name": None,
    }


@pytest.mark.django_db
def test_list_taps_newest_first(client, kiosks, card):
    tap(client, card, kiosks["sj"])
    tap(client, card, kiosks["sf"])

    response = client.get("/api/taps/")

    assert [(t["kind"], t["station_name"]) for t in response.json()] == [
        ("tap_off", "San Francisco"),
        ("tap_on", "San Jose"),
    ]
