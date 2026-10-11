from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model, in place from the start so it can be extended later."""
