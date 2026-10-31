import pytest
from django.test import Client

PASSPHRASE = "test passphrase"


@pytest.fixture(autouse=True)
def employee_passphrase(settings):
    settings.EMPLOYEE_PASSPHRASE = PASSPHRASE


@pytest.fixture
def client():
    """A client that sends the employee passphrase, like the Next.js server."""
    return Client(headers={"X-Employee-Passphrase": PASSPHRASE})


@pytest.fixture
def anonymous_client():
    """A client that calls the API directly, without the passphrase."""
    return Client()
