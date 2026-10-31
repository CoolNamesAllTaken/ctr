import pytest
from django.test import Client


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/riders/"),
        ("post", "/api/riders/"),
        ("get", "/api/taps/"),
        ("post", "/api/alerts/"),
        ("delete", "/api/train-statuses/1/"),
    ],
)
def test_employee_endpoints_need_the_passphrase(anonymous_client, method, path):
    response = getattr(anonymous_client, method)(path, content_type="application/json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_wrong_passphrase(client):
    wrong = Client(headers={"X-Employee-Passphrase": "trick or treat"})

    assert wrong.get("/api/riders/").status_code == 401
    assert client.get("/api/riders/").status_code == 200


@pytest.mark.django_db
def test_no_passphrase_set_locks_everyone_out(settings):
    settings.EMPLOYEE_PASSPHRASE = ""
    empty = Client(headers={"X-Employee-Passphrase": ""})

    assert empty.get("/api/riders/").status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/health/", "/api/timetable/", "/api/live-map/"])
def test_rider_endpoints_are_public(anonymous_client, path):
    assert anonymous_client.get(path).status_code == 200


@pytest.mark.django_db
def test_kiosks_can_tap_without_the_passphrase(anonymous_client):
    response = anonymous_client.post(
        "/api/taps/", {"card_uid": "beef", "kiosk_id": "k1"}, "application/json"
    )

    # Past auth to the fare check: the card isn't set up.
    assert response.status_code == 422
