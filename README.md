# ctr

## Software
The data model supports the scenario where N (2, currently) train cars make
scheduled runs northbound (towards San Francisco) and southbound (towards
Mountain View).
Riders tap NFC Chipper Cards on and off at station kiosks, and are charged by zone. Live delays will be computed by on-train GPS, and service alerts will explain them.

The code lives in [`website/`](website/): a Django API (Django Ninja,
Postgres) and a Next.js frontend. See [`website/README.md`](website/README.md)
for setup, and
[`website/backend/core/models.py`](website/backend/core/models.py) for the data
model.
