import pytest
from django.test import Client


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/riders/"),
        ("post", "/api/riders/"),
        ("post", "/api/taps/"),
        ("post", "/api/alerts/"),
        ("post", "/api/starter-data/"),
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
