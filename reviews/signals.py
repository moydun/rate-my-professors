import logging

from django.contrib.auth.signals import user_login_failed
from django.dispatch import receiver

logger = logging.getLogger("security")


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request=None, **kwargs):
    """Log a failed login without exposing the submitted password."""
    username = credentials.get("username") or "<unknown>"
    logger.warning("Failed login attempt for username=%s", username)
