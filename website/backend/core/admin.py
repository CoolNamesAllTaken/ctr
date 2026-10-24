from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.models import (
    Alert,
    Arrival,
    ArrivalInstance,
    ChipperCard,
    Kiosk,
    KioskTap,
    Rider,
    Station,
    Train,
    TrainCar,
    User,
)

admin.site.register(User, UserAdmin)
admin.site.register(
    [
        TrainCar,
        Train,
        Station,
        Alert,
        Kiosk,
        Arrival,
        ArrivalInstance,
        ChipperCard,
        Rider,
        KioskTap,
    ]
)
