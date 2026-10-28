"""The one place that decides which repository the app uses."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .repository import InMemoryRepository
from .seed import seed

if TYPE_CHECKING:
    from .django_repo import DjangoRepository

_REPOS: dict[str, object] = {}


def _make_repo() -> InMemoryRepository | DjangoRepository:
    """Build the repository selected by ``REPO_BACKEND``/``DATABASE_URL``."""
    backend = os.environ.get("REPO_BACKEND", "")
    db_url = os.environ.get("DATABASE_URL", "")
    # explicit django wins; otherwise if DATABASE_URL is set assume django, else memory
    if backend == "django" or (not backend and db_url):
        from .django_repo import DjangoRepository
        return DjangoRepository()
    repo = InMemoryRepository()
    seed(repo)
    return repo


# tests can override with override_settings or by monkeypatching; for regular runtime use get_repo()
repo: InMemoryRepository | DjangoRepository = _make_repo()


def get_repo() -> InMemoryRepository | DjangoRepository:
    """Return the active repository (respects REPO_BACKEND/DATABASE_URL at call time)."""
    # for views/management commands that want fresh choice; cached repo is used by default
    return repo
