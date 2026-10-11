from ninja import NinjaAPI
from ninja.security import django_auth

from core.api import router as core_router

# Locked down by default (session auth + CSRF); opt individual endpoints out
# with auth=None.
api = NinjaAPI(title="ctr", auth=django_auth)

api.add_router("", core_router)
