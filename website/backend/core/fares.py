"""Clipper-style fares: tapping on charges the maximum fare, and tapping off
refunds the difference down to the fare for the zones actually travelled.
Tapping off at the station where the trip started cancels it: the whole
tap-on charge is refunded.

A tap-on is recorded as a positive charge and a tap-off as a charge of zero or
less (a refund), which is how a card's open trip is found from its ledger.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from core.models import ChipperCard, Kiosk, KioskTap, Station

BASE_FARE = Decimal("3.75")
PER_ZONE_FARE = Decimal("2.50")
# A tap-on with no tap-off this long afterwards is treated as abandoned: the
# rider keeps paying the maximum fare and their next tap starts a new trip.
TAP_ON_EXPIRY = timedelta(hours=4)


class FareError(Exception):
    pass


def fare_for_zones(zones):
    """The fare for a trip touching this many zones (at least one)."""
    return BASE_FARE + PER_ZONE_FARE * (zones - 1)


def max_fare():
    zones = Station.objects.aggregate(low=Min("zone"), high=Max("zone"))
    if zones["low"] is None:
        return BASE_FARE
    return fare_for_zones(zones["high"] - zones["low"] + 1)


def open_tap_on(card, now):
    """The card's tap-on still waiting for a tap-off, if any."""
    last_tap = card.taps.select_related("kiosk__station").order_by("-tap_time").first()
    if (
        last_tap
        and last_tap.card_charge > 0
        and now - last_tap.tap_time < TAP_ON_EXPIRY
    ):
        return last_tap
    return None


@transaction.atomic
def tap(card_uid, kiosk_id):
    """Record a tap of this card at this kiosk. Returns (tap, tap_on), where
    tap_on is the tap this one closed, or None if this tap starts a trip."""
    card = ChipperCard.objects.select_for_update().filter(uid=card_uid).first()
    if card is None:
        raise FareError("No Chipper card with that UID.")
    kiosk = Kiosk.objects.select_related("station").filter(id=kiosk_id).first()
    if kiosk is None:
        raise FareError("That kiosk no longer exists.")

    now = timezone.now()
    tap_on = open_tap_on(card, now)
    if tap_on is None:
        charge = max_fare()
        if card.balance < charge:
            raise FareError(
                f"Balance ${card.balance} is below the ${charge} needed to tap on."
            )
    elif tap_on.kiosk.station_id == kiosk.station_id:
        charge = -tap_on.card_charge
    else:
        zones = abs(tap_on.kiosk.station.zone - kiosk.station.zone) + 1
        # Never charge more on tap-off (say, if zones were added mid-trip), so
        # a tap-off always reads as one in the ledger.
        charge = min(fare_for_zones(zones) - tap_on.card_charge, Decimal(0))

    card.balance -= charge
    card.save(update_fields=["balance"])
    new_tap = KioskTap.objects.create(
        card=card,
        kiosk=kiosk,
        tap_time=now,
        card_charge=charge,
        balance_after=card.balance,
    )
    return new_tap, tap_on
