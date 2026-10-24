from django.core.management.base import BaseCommand

from core.starter_data import load_starter_data


class Command(BaseCommand):
    help = "Add the starter train cars, stations and kiosks if they're missing."

    def handle(self, *args, **options):
        created = load_starter_data()
        self.stdout.write(
            ", ".join(f"{count} {name}" for name, count in created.items())
            + " created."
        )
