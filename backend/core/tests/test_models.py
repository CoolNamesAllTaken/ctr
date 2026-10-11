import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_create_user():
    user = get_user_model().objects.create_user(username="ada", password="pw")

    assert user.check_password("pw")
    assert str(user) == "ada"
