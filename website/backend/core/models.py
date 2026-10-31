from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model, in place from the start so it can be extended later."""


class TrainCar(models.Model):
    number = models.CharField(max_length=20, unique=True)
    capacity = models.PositiveIntegerField()

    def __str__(self):
        return f"Car {self.number}"


class Train(models.Model):
    """A scheduled run, like a Caltrain train number. A car makes many runs."""

    class Direction(models.TextChoices):
        NORTHBOUND = "northbound", "Northbound (towards San Francisco)"
        SOUTHBOUND = "southbound", "Southbound (towards San Jose)"

    name = models.CharField(max_length=100)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    traincar = models.ForeignKey(
        TrainCar, on_delete=models.PROTECT, related_name="trains"
    )

    def __str__(self):
        return self.name


class Station(models.Model):
    name = models.CharField(max_length=100, unique=True)
    zone = models.PositiveSmallIntegerField()
    # Order along the line, counting up from San Jose (south) to
    # San Francisco (north).
    position = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.name


class Alert(models.Model):
    """A service alert. With no train, it applies to the whole line."""

    message = models.TextField()
    train = models.ForeignKey(
        Train,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alerts",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.message[:50]


class Arrival(models.Model):
    """A scheduled stop: this train is due at this station at this time."""

    train = models.ForeignKey(Train, on_delete=models.CASCADE, related_name="arrivals")
    station = models.ForeignKey(
        Station, on_delete=models.PROTECT, related_name="arrivals"
    )
    time = models.TimeField()

    def __str__(self):
        return f"{self.train} at {self.station} {self.time:%H:%M}"


class ArrivalInstance(models.Model):
    """One real-world occurrence of a scheduled arrival, with its live delay."""

    arrival = models.ForeignKey(
        Arrival, on_delete=models.CASCADE, related_name="instances"
    )
    scheduled_at = models.DateTimeField()
    current_delay_minutes = models.IntegerField(default=0)
    # The alert explaining the delay, if any.
    alert = models.ForeignKey(
        Alert,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arrival_instances",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["arrival", "scheduled_at"],
                name="unique_arrival_instance_per_time",
            )
        ]

    def __str__(self):
        return f"{self.arrival} on {self.scheduled_at:%Y-%m-%d}"


class TrainStatus(models.Model):
    """The conductor's word on where one day's run of a train is. Overrides
    the timetable on the live map; with none, the map follows the timetable."""

    class State(models.TextChoices):
        STOPPED = "stopped", "Stopped at a station"
        MOVING = "moving", "Moving to the next stop"
        OUT_OF_SERVICE = "out_of_service", "Out of service"
        CANCELED = "canceled", "Canceled"

    # States with no station, which leave the train off the live map.
    OFF_THE_LINE = (State.OUT_OF_SERVICE, State.CANCELED)

    train = models.ForeignKey(Train, on_delete=models.CASCADE, related_name="statuses")
    date = models.DateField()
    state = models.CharField(max_length=20, choices=State.choices)
    # Where it's stopped, or the stop it last left. Unset when out of service
    # or canceled.
    station = models.ForeignKey(
        Station,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField()

    class Meta:
        verbose_name_plural = "train statuses"
        constraints = [
            models.UniqueConstraint(
                fields=["train", "date"], name="unique_train_status_per_day"
            )
        ]

    def __str__(self):
        return f"{self.train} on {self.date}: {self.get_state_display()}"


class ChipperCard(models.Model):
    """An NFC fare card, tapped on and off at kiosks like a Clipper card."""

    uid = models.CharField(max_length=32, unique=True)
    # Kept in step with the latest tap's balance_after.
    balance = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    def __str__(self):
        return f"Card {self.uid}"


class Rider(models.Model):
    name = models.CharField(max_length=100)
    # What the trick-or-treater is dressed as, to tell same-named riders apart.
    costume = models.CharField(max_length=100, blank=True)
    card = models.OneToOneField(
        ChipperCard,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rider",
    )
    # The train the rider is currently on, if any.
    train = models.ForeignKey(
        Train,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="riders",
    )

    def __str__(self):
        return self.name


class Kiosk(models.Model):
    """A card-tap kiosk; a station can have several. A kiosk is registered the
    first time it reports a tap, with no station until an employee assigns
    one."""

    # The kiosk's own ID, which it sends with every tap.
    id = models.CharField(primary_key=True, max_length=64)
    name = models.CharField(max_length=100)
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="kiosks",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["station", "name"], name="unique_kiosk_name_per_station"
            )
        ]

    def __str__(self):
        return f"{self.name} at {self.station or 'no station'}"


class KioskTap(models.Model):
    """A ledger entry: a card tapped at a kiosk, what it was charged, and the
    card's balance afterwards."""

    card = models.ForeignKey(ChipperCard, on_delete=models.PROTECT, related_name="taps")
    kiosk = models.ForeignKey(Kiosk, on_delete=models.PROTECT, related_name="taps")
    tap_time = models.DateTimeField()
    card_charge = models.DecimalField(max_digits=8, decimal_places=2)
    balance_after = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.card} at {self.kiosk} {self.tap_time:%Y-%m-%d %H:%M}"
