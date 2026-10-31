"""The employee portal's Live tab: how the night is going. It's all worked out
from the tap ledger each time it's asked for; nothing here is stored."""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from django.utils import timezone

from core.fares import TAP_ON_EXPIRY
from core.models import ChipperCard, Kiosk, KioskTap

TOP_RIDERS = 5
TOP_COSTUMES = 8
BUCKET = timedelta(minutes=5)


def rider_ref(card):
    # A missing reverse one-to-one raises an AttributeError subclass.
    rider = getattr(card, "rider", None)
    return {
        "card_uid": card.uid,
        "name": rider.name if rider else None,
        "costume": rider.costume if rider else "",
    }


def costume_key(costume):
    """So "Dinosaur", "dinosaur" and " dinosaur " count as one costume."""
    return " ".join(costume.split()).casefold()


def is_tap_on(tap):
    # Tapping on holds the maximum fare; tapping off refunds some or all of it.
    return tap.card_charge > 0


def snapshot(now):
    """Tonight (since local midnight) up to `now`."""
    day_start = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min))
    kiosks = list(
        Kiosk.objects.select_related("station").order_by("station__position", "name")
    )
    # Back far enough to find the tap-on behind any tap-off tonight.
    taps = list(
        KioskTap.objects.filter(
            tap_time__gte=day_start - TAP_ON_EXPIRY, tap_time__lte=now
        )
        .select_related("card__rider", "kiosk")
        .order_by("tap_time", "id")
    )

    # Each tap-off closes the card's tap before it (see fares.tap).
    trips = Counter()
    cancelled = 0
    last_tap = {}
    for tap in taps:
        start = last_tap.get(tap.card_id)
        last_tap[tap.card_id] = tap
        if tap.tap_time < day_start or is_tap_on(tap) or start is None:
            continue
        if start.kiosk.station_id == tap.kiosk.station_id:
            cancelled += 1
        else:
            trips[tap.card_id] += 1
    riding_now = sum(
        1
        for tap in last_tap.values()
        if is_tap_on(tap) and now - tap.tap_time < TAP_ON_EXPIRY
    )
    tonight = [tap for tap in taps if tap.tap_time >= day_start]

    cards = {tap.card_id: tap.card for tap in tonight}
    frequent = sorted(
        (
            {**rider_ref(cards[card_id]), "trips": count}
            for card_id, count in trips.items()
        ),
        key=lambda rider: (-rider["trips"], rider["name"] or rider["card_uid"]),
    )[:TOP_RIDERS]

    # Shown as whichever spelling most riders typed.
    spellings = defaultdict(Counter)
    for card in cards.values():
        rider = getattr(card, "rider", None)
        if rider and costume_key(rider.costume):
            spellings[costume_key(rider.costume)][" ".join(rider.costume.split())] += 1
    costumes = sorted(
        (
            {"costume": counts.most_common(1)[0][0], "riders": counts.total()}
            for counts in spellings.values()
        ),
        key=lambda costume: (-costume["riders"], costume["costume"].casefold()),
    )[:TOP_COSTUMES]

    return {
        "as_of": now,
        "taps": len(tonight),
        "trips_completed": trips.total(),
        "trips_cancelled": cancelled,
        "riders": len(cards),
        "riding_now": riding_now,
        "cards": ChipperCard.objects.count(),
        "bucket_minutes": BUCKET // timedelta(minutes=1),
        "kiosks": kiosk_activity(now, day_start, kiosks, tonight),
        "frequent_riders": frequent,
        "costumes": costumes,
    }


def kiosk_activity(now, day_start, kiosks, tonight):
    """Each kiosk's tap-ons and tap-offs tonight, in total and per bucket,
    from the bucket of the first tap tonight to the current one. Every kiosk
    gets the same buckets, so they line up."""

    def bucket_of(moment):
        return (moment - day_start) // BUCKET

    counts = defaultdict(lambda: defaultdict(lambda: {"tap_ons": 0, "tap_offs": 0}))
    for tap in tonight:
        kind = "tap_ons" if is_tap_on(tap) else "tap_offs"
        counts[tap.kiosk_id][bucket_of(tap.tap_time)][kind] += 1
    span = range(bucket_of(tonight[0].tap_time), bucket_of(now) + 1) if tonight else []

    activity = []
    for kiosk in kiosks:
        buckets = [
            {"start": day_start + n * BUCKET, **counts[kiosk.id][n]} for n in span
        ]
        activity.append(
            {
                "kiosk_id": kiosk.id,
                "kiosk_name": kiosk.name,
                # Null for a kiosk not assigned to a station (out of service).
                "station_name": kiosk.station.name if kiosk.station else None,
                "tap_ons": sum(b["tap_ons"] for b in buckets),
                "tap_offs": sum(b["tap_offs"] for b in buckets),
                "buckets": buckets,
            }
        )
    return activity
