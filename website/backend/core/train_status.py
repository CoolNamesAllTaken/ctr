"""The conductor's start/stop button: employees mark today's run of a train
stopped at a station, moving on from one, out of service or canceled, and the
live map shows that instead of what the timetable says.
"""

from django.utils import timezone

from core import delays
from core.models import TrainStatus


class StatusError(Exception):
    pass


def todays_statuses(now, trains=None):
    """Each train's run today (default: every train's), with its status."""
    date = timezone.localdate(now)
    runs = delays.train_runs(date, trains)
    statuses = {
        status.train_id: status
        for status in TrainStatus.objects.filter(
            date=date, train_id__in=[run["train_id"] for run in runs]
        ).select_related("station")
    }
    for run in runs:
        status = statuses.get(run["train_id"])
        run["state"] = status.state if status else None
        run["station_id"] = status.station_id if status else None
        run["station_name"] = status.station.name if status and status.station else None
        run["updated_at"] = status.updated_at if status else None
    return runs


def set_status(train, state, now, station_id=None):
    """Mark today's run of the train stopped at, or moving on from, a station
    on its run, or out of service or canceled."""
    if state in TrainStatus.OFF_THE_LINE:
        station_id = None
    else:
        station_ids = list(
            train.arrivals.order_by("time").values_list("station_id", flat=True)
        )
        if station_id not in station_ids:
            raise StatusError("That train doesn't stop at that station.")
        if state == TrainStatus.State.MOVING and station_id == station_ids[-1]:
            raise StatusError("That's the train's last stop; there's nowhere to go.")

    TrainStatus.objects.update_or_create(
        train=train,
        date=timezone.localdate(now),
        defaults={"state": state, "station_id": station_id, "updated_at": now},
    )


def clear_status(train, now):
    """Put today's run of the train back on the timetable."""
    TrainStatus.objects.filter(train=train, date=timezone.localdate(now)).delete()
