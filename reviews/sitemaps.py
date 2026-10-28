"""Sitemap definitions backed by the active repository.

Using the repository API instead of the ORM directly keeps the sitemap working
with both storage backends (in-memory and Django ORM), just like the views.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .store import get_repo

if TYPE_CHECKING:
    from .repository import Professor, School


class HomeSitemap(Sitemap):
    """Sitemap entry for the home page."""

    changefreq = "daily"
    priority = 1.0

    def items(self) -> list[str]:
        """Return the single home-page pseudo-item."""
        return ["home"]

    def location(self, item: str) -> str:
        """Return the home page URL."""
        return reverse("home")


class SchoolSitemap(Sitemap):
    """Sitemap entries for all schools."""

    changefreq = "weekly"
    priority = 0.8

    def items(self) -> list[School]:
        """Return all schools from the active repository."""
        return get_repo().schools()

    def location(self, school: School) -> str:
        """Return the detail URL for a school."""
        return reverse("school_detail", args=[school.slug])


class ProfessorSitemap(Sitemap):
    """Sitemap entries for all approved professors."""

    changefreq = "weekly"
    priority = 0.6

    def items(self) -> list[Professor]:
        """Return all professors from the active repository."""
        return [row["prof"] for row in get_repo().search("")]

    def location(self, professor: Professor) -> str:
        """Return the detail URL for a professor."""
        return reverse("professor", args=[professor.slug])


sitemaps: dict[str, type[Sitemap]] = {
    "home": HomeSitemap,
    "schools": SchoolSitemap,
    "professors": ProfessorSitemap,
}
