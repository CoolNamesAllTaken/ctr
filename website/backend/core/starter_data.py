"""Starter data for a fresh database: train cars, stations and their kiosks.

Loading only adds records that are missing, matched by their unique names, so
it's safe to run more than once and never changes existing records.
"""

import json
from pathlib import Path

from django.db import transaction

from core.models import Kiosk, Station, TrainCar

STARTER_DATA_PATH = Path(__file__).with_name("starter_data.json")


@transaction.atomic
def load_starter_data(path=STARTER_DATA_PATH):
    """Returns how many of each record were created."""
    data = json.loads(path.read_text())
    created = {"traincars": 0, "stations": 0, "kiosks": 0}

    for car in data["traincars"]:
        _, was_created = TrainCar.objects.get_or_create(
            number=car["number"], defaults={"capacity": car["capacity"]}
        )
        created["traincars"] += was_created

    for entry in data["stations"]:
        station, was_created = Station.objects.get_or_create(
            name=entry["name"],
            defaults={"zone": entry["zone"], "position": entry["position"]},
        )
        created["stations"] += was_created
        for kiosk_name in entry["kiosks"]:
            _, was_created = Kiosk.objects.get_or_create(
                station=station, name=kiosk_name
            )
            created["kiosks"] += was_created

    return created
