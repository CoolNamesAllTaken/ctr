"""Live delays: each train runs every day, and an ArrivalInstance records one
day's occurrence of a scheduled arrival with its current delay. Instances are
only created once a delay is set, so a stop without one is running on time.
"""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import ArrivalInstance, Train


class DelayError(Exception):
    pass


def scheduled_at(date, time):
    return timezone.make_aware(datetime.combine(date, time))


def train_runs(date, trains=None):
    """Each train's run on this date (default: every train's), ordered by
    departure, with each stop's scheduled and expected times."""
    if trains is None:
        trains = Train.objects.all()
    trains = trains.prefetch_related("arrivals__station")
    instances = {
        instance.arrival_id: instance
        for instance in ArrivalInstance.objects.filter(
            arrival__train__in=trains, scheduled_at__date=date
        ).select_related("alert")
    }

    runs = []
    for train in trains:
        stops = []
        for arrival in sorted(train.arrivals.all(), key=lambda a: a.time):
            instance = instances.get(arrival.id)
            delay = instance.current_delay_minutes if instance else 0
            at = scheduled_at(date, arrival.time)
            stops.append(
                {
                    "station_id": arrival.station_id,
                    "station_name": arrival.station.name,
                    "scheduled_at": at,
                    "expected_at": at + timedelta(minutes=delay),
                    "delay_minutes": delay,
                    "alert_id": instance.alert_id if instance else None,
                    "alert_message": (
                        instance.alert.message if instance and instance.alert else None
                    ),
                }
            )
        runs.append(
            {
                "train_id": train.id,
                "train_name": train.name,
                "direction": train.direction,
                "stops": stops,
            }
        )
    # Trains without stops (only possible via the admin) go last.
    runs.sort(
        key=lambda run: (0, run["stops"][0]["scheduled_at"]) if run["stops"] else (1,)
    )
    return runs


@transaction.atomic
def set_delay(train, date, minutes, from_station_id=None, alert=None):
    """Delay this train's run from the given stop (default: its first) to the
    end of the line. Earlier stops keep the delay they had."""
    arrivals = list(train.arrivals.order_by("time"))
    station_ids = [arrival.station_id for arrival in arrivals]
    if from_station_id is None:
        start = 0
    elif from_station_id in station_ids:
        start = station_ids.index(from_station_id)
    else:
        raise DelayError("That train doesn't stop at that station.")
    if alert is not None and alert.train_id not in (None, train.id):
        raise DelayError("That alert is about a different train.")

    for arrival in arrivals[start:]:
        ArrivalInstance.objects.update_or_create(
            arrival=arrival,
            scheduled_at=scheduled_at(date, arrival.time),
            defaults={"current_delay_minutes": minutes, "alert": alert},
        )
