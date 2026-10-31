import pytest

from core.models import Alert, Train, TrainCar


@pytest.fixture
def train():
    car = TrainCar.objects.create(number="101", capacity=50)
    return Train.objects.create(name="Train 1", direction="northbound", traincar=car)


def post(client, data):
    return client.post("/api/alerts/", data, content_type="application/json")


@pytest.mark.django_db
def test_create_alert_for_train(client, train):
    response = post(
        client, {"message": " Running 10 minutes late ", "train_id": train.id}
    )

    assert response.status_code == 201
    alert = Alert.objects.get()
    assert response.json() == {
        "id": alert.id,
        "message": "Running 10 minutes late",
        "train_id": train.id,
        "train_name": "Train 1",
        "is_active": True,
        "created_at": response.json()["created_at"],
    }
    assert alert.train == train


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload", [{"message": "Track work"}, {"message": "Track work", "train_id": None}]
)
def test_create_line_wide_alert(client, payload):
    response = post(client, payload)

    assert response.status_code == 201
    assert response.json()["train_id"] is None
    assert response.json()["train_name"] is None
    assert Alert.objects.get().train is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload", [{"message": "  "}, {"message": "Late", "train_id": 999}]
)
def test_create_alert_rejects_invalid(client, train, payload):
    response = post(client, payload)

    assert response.status_code == 422
    assert not Alert.objects.exists()


@pytest.mark.django_db
def test_list_alerts_newest_first(client, train):
    Alert.objects.create(message="First")
    Alert.objects.create(message="Second", train=train)

    response = client.get("/api/alerts/")

    assert [(a["message"], a["train_name"]) for a in response.json()] == [
        ("Second", "Train 1"),
        ("First", None),
    ]


def patch(client, alert_id, data):
    return client.patch(
        f"/api/alerts/{alert_id}/", data, content_type="application/json"
    )


@pytest.mark.django_db
def test_cancel_alert(client, train):
    alert = Alert.objects.create(message="Running late", train=train)

    response = patch(client, alert.id, {"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["train_name"] == "Train 1"
    alert.refresh_from_db()
    assert not alert.is_active
    assert client.get("/api/alerts/?active=true").json() == []


@pytest.mark.django_db
def test_cancel_missing_alert(client):
    response = patch(client, 999, {"is_active": False})

    assert response.status_code == 422
    assert response.json() == {"detail": "That alert no longer exists."}
