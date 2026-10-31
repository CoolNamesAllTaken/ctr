from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.models import ChipperCard, Kiosk, KioskTap, Rider, Station

MAX_FARE = Decimal("6.25")


def at(hour, minute, day=0):
    date = timezone.localdate() + timedelta(days=day)
    return timezone.make_aware(datetime.combine(date, time(hour, minute)))


@pytest.fixture
def kiosks():
    mv = Station.objects.create(name="Mountain View", zone=2, position=0)
    sf = Station.objects.create(name="San Francisco", zone=1, position=1)
    return {
        "mv_nb": Kiosk.objects.create(
            id="mv-nb", name="Northbound platform", station=mv
        ),
        "mv_sb": Kiosk.objects.create(
            id="mv-sb", name="Southbound platform", station=mv
        ),
        "sf_nb": Kiosk.objects.create(
            id="sf-nb", name="Northbound platform", station=sf
        ),
        "sf_sb": Kiosk.objects.create(
            id="sf-sb", name="Southbound platform", station=sf
        ),
    }


def rider(name, costume=""):
    card = ChipperCard.objects.create(uid=name.encode().hex(), balance=Decimal(20))
    Rider.objects.create(name=name, costume=costume, card=card)
    return card


def tap(card, kiosk, when, charge=None):
    """Write a tap straight to the ledger at a given time. Leave out the
    charge for a tap-on at the maximum fare."""
    charge = MAX_FARE if charge is None else Decimal(charge)
    card.balance -= charge
    card.save()
    return KioskTap.objects.create(
        card=card,
        kiosk=kiosk,
        tap_time=when,
        card_charge=charge,
        balance_after=card.balance,
    )


def dashboard(client, when):
    response = client.get("/api/dashboard/", {"at": when.isoformat()})
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db
def test_dashboard_needs_passphrase(anonymous_client):
    assert anonymous_client.get("/api/dashboard/").status_code == 401


@pytest.mark.django_db
def test_night_totals(client, kiosks):
    ada = rider("Ada", "Dinosaur")
    bo = rider("Bo", " dinosaur ")
    cy = rider("Cy", "Witch")
    rider("Never rode", "Ghost")
    # Last night's trips don't count tonight.
    tap(ada, kiosks["mv_nb"], at(20, 0, day=-1))
    tap(ada, kiosks["sf_sb"], at(20, 30, day=-1), "0")

    tap(ada, kiosks["mv_nb"], at(7, 55))
    tap(ada, kiosks["sf_sb"], at(8, 22), "0")
    tap(ada, kiosks["sf_sb"], at(8, 25))
    tap(ada, kiosks["mv_nb"], at(8, 51), "0")
    tap(bo, kiosks["mv_nb"], at(7, 58))
    tap(bo, kiosks["sf_sb"], at(8, 22), "0")
    # Tapped off where they tapped on.
    tap(cy, kiosks["mv_nb"], at(8, 1))
    tap(cy, kiosks["mv_sb"], at(8, 2), "-6.25")
    tap(cy, kiosks["mv_nb"], at(8, 10))

    body = dashboard(client, at(8, 55))

    assert body["taps"] == 9
    assert body["trips_completed"] == 3
    assert body["trips_cancelled"] == 1
    assert body["riders"] == 3
    assert body["riding_now"] == 1
    assert body["cards"] == 4
    assert [(r["name"], r["trips"]) for r in body["frequent_riders"]] == [
        ("Ada", 2),
        ("Bo", 1),
    ]
    assert body["costumes"] == [
        {"costume": "Dinosaur", "riders": 2},
        {"costume": "Witch", "riders": 1},
    ]


@pytest.mark.django_db
def test_taps_per_kiosk_in_five_minute_buckets(client, kiosks):
    ada = rider("Ada")
    bo = rider("Bo")
    tap(ada, kiosks["mv_nb"], at(8, 1))
    tap(bo, kiosks["mv_nb"], at(8, 4))
    tap(ada, kiosks["sf_sb"], at(8, 12), "0")
    tap(bo, kiosks["sf_sb"], at(8, 14), "0")

    body = dashboard(client, at(8, 16))

    assert body["bucket_minutes"] == 5
    assert [
        (k["station_name"], k["kiosk_name"], k["tap_ons"], k["tap_offs"])
        for k in body["kiosks"]
    ] == [
        ("Mountain View", "Northbound platform", 2, 0),
        ("Mountain View", "Southbound platform", 0, 0),
        ("San Francisco", "Northbound platform", 0, 0),
        ("San Francisco", "Southbound platform", 0, 2),
    ]
    by_kiosk = {k["kiosk_id"]: k["buckets"] for k in body["kiosks"]}
    # From the first tap's bucket to now's, the same for every kiosk.
    for buckets in by_kiosk.values():
        assert [b["start"][11:16] for b in buckets] == [
            "08:00",
            "08:05",
            "08:10",
            "08:15",
        ]
    assert [(b["tap_ons"], b["tap_offs"]) for b in by_kiosk[kiosks["mv_nb"].id]] == [
        (2, 0),
        (0, 0),
        (0, 0),
        (0, 0),
    ]
    assert [(b["tap_ons"], b["tap_offs"]) for b in by_kiosk[kiosks["sf_sb"].id]] == [
        (0, 0),
        (0, 0),
        (0, 2),
        (0, 0),
    ]


@pytest.mark.django_db
def test_tap_off_after_midnight_pairs_with_last_nights_tap_on(client, kiosks):
    ada = rider("Ada")
    tap(ada, kiosks["mv_nb"], at(23, 50, day=-1))
    tap(ada, kiosks["sf_sb"], at(0, 10), "0")

    body = dashboard(client, at(0, 15))

    assert body["trips_completed"] == 1
    assert body["taps"] == 1
    assert body["riding_now"] == 0


@pytest.mark.django_db
def test_empty_night(client, kiosks):
    body = dashboard(client, at(8, 0))

    assert body["taps"] == 0
    assert len(body["kiosks"]) == 4
    assert all(k["buckets"] == [] for k in body["kiosks"])
    assert body["frequent_riders"] == []


@pytest.mark.django_db
def test_kiosk_without_a_station(client, kiosks):
    Kiosk.objects.create(id="new-kiosk", name="Unregistered")

    body = dashboard(client, at(8, 0))

    assert len(body["kiosks"]) == 5
    # Out of service kiosks sort last.
    assert body["kiosks"][-1]["kiosk_id"] == "new-kiosk"
    assert body["kiosks"][-1]["station_name"] is None
