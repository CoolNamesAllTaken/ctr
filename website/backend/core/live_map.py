"""Where each train is right now, for the rider-facing live map.

There's no GPS. When the conductor has marked a train stopped, moving or out
of service (see train_status), the map shows that; otherwise a train's
location is worked out from today's timetable and its live delays.

A location is a pair of stations and how far the train has got between them,
so the map is free to lay the stations out however it likes.
"""

from datetime import timedelta

from django.utils import timezone

from core import delays
from core.models import TrainStatus

# A train sits at each stop for this long before its timetabled time, and is
# off the map before it reaches its first stop and once it leaves its last.
DWELL = timedelta(minutes=1)
# A moving train is guessed along from the timetable, but stays short of the
# next stop until the conductor says it's there.
MAX_MOVING_PROGRESS = 0.9


def locate(stops, now):
    """(from stop, to stop, progress 0-1) along a run's stops at this moment,
    or None if the train isn't out on the line. At a stop, both are that stop."""
    for i, stop in enumerate(stops):
        if now > stop["expected_at"]:
            continue
        if now >= stop["expected_at"] - DWELL:
            return stop, stop, 0.0
        if i == 0:
            return None
        previous = stops[i - 1]
        leg = stop["expected_at"] - DWELL - previous["expected_at"]
        return previous, stop, (now - previous["expected_at"]) / leg
    return None


def follow_status(stops, status, now):
    """Like locate(), but from the conductor's status."""
    if status.state == TrainStatus.State.OUT_OF_SERVICE:
        return None
    station_ids = [stop["station_id"] for stop in stops]
    # The run may have been changed since the status was set.
    if status.station_id not in station_ids:
        return None
    i = station_ids.index(status.station_id)
    here = stops[i]
    if status.state == TrainStatus.State.STOPPED or i + 1 == len(stops):
        return here, here, 0.0
    there = stops[i + 1]
    leg = there["scheduled_at"] - DWELL - here["scheduled_at"]
    elapsed = max(now - status.updated_at, timedelta(0))
    progress = elapsed / leg if leg > timedelta(0) else 1.0
    return here, there, min(progress, MAX_MOVING_PROGRESS)


def train_locations(now):
    """Every train out on the line at this moment, in departure order."""
    date = timezone.localdate(now)
    statuses = {
        status.train_id: status for status in TrainStatus.objects.filter(date=date)
    }
    located = []
    for run in delays.train_runs(date):
        stops = run["stops"]
        status = statuses.get(run["train_id"])
        location = follow_status(stops, status, now) if status else locate(stops, now)
        if location is None:
            continue
        here, there, progress = location
        located.append(
            {
                "train_id": run["train_id"],
                "train_name": run["train_name"],
                "direction": run["direction"],
                "from_station_id": here["station_id"],
                "to_station_id": there["station_id"],
                "progress": progress,
                "at_station_id": here["station_id"] if here is there else None,
                "delay_minutes": there["delay_minutes"],
                # Stops it hasn't left yet, including the one it's at.
                "upcoming_stops": stops[stops.index(there) :],
            }
        )
    return located
