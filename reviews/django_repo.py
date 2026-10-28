"""ORM-backed implementation of the repository API."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from statistics import mean
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.utils.text import slugify

from .caching import get_or_set, invalidate_catalog_cache, stats_cache_key
from .constants import MAX_COMMENT_LENGTH
from .models import Professor, Report, Review, School, Tag, Vote
from .repository import _search_sort_key, _tone
from .sanitize import clean_text

if TYPE_CHECKING:
    from .repository import Professor as ProfessorDC
    from .repository import Review as ReviewDC
    from .repository import School as SchoolDC


class DjangoRepository:
    """ORM-backed repository with the same API as InMemoryRepository."""

    # writes
    def add_school(self, name: str, city: str) -> SchoolDC:
        """Create a school with a unique slug."""
        slug = slugify(name, allow_unicode=True) or slugify(name) or name.lower().replace(" ", "-")
        # ensure unique
        base, i = slug, 1
        while School.objects.filter(slug=slug).exists():
            slug = f"{base}-{i}"
            i += 1
        s = School.objects.create(name=name, slug=slug, city=city)
        return self._school_to_dc(s)

    def add_professor(
        self,
        school_id: str,
        first: str,
        last: str,
        department: str,
        title: str,
        status: str = "approved",
        user: Any = None,
    ) -> ProfessorDC:
        """Create a professor, rejecting duplicates in the same school."""
        school = School.objects.get(pk=school_id)
        dup_qs = Professor.objects.filter(
            school=school,
            first_name__iexact=first.strip(),
            last_name__iexact=last.strip(),
        ).exclude(status="rejected")
        if dup_qs.exists():
            # fallback case-insensitive python check for SQLite
            for p in Professor.objects.filter(school=school).exclude(status="rejected"):
                if p.full_name.lower() == f"{first} {last}".lower():
                    raise ValueError("duplicate professor")
            raise ValueError("duplicate professor")
        base = slugify(f"{first} {last}", allow_unicode=True) or slugify(f"{first} {last}") or f"{first}-{last}".lower()
        slug = base
        i = 1
        while Professor.objects.filter(slug=slug).exists():
            slug = f"{base}-{i}"
            i += 1
        p = Professor.objects.create(
            school=school, first_name=first.strip(), last_name=last.strip(),
            slug=slug, department=department, title=title, status=status, created_by=user,
        )
        return self._prof_to_dc(p)

    def add_review(
        self,
        professor_id: str,
        data: Mapping[str, Any],
        user: Any = None,
        status: str | None = None,
    ) -> ReviewDC:
        """Create a review, enforcing per-user course uniqueness."""
        prof = Professor.objects.get(pk=professor_id)
        course = clean_text(data.get("course") or "")
        comment = clean_text(data.get("comment") or "")
        if len(comment) > MAX_COMMENT_LENGTH:
            raise ValueError("comment too long")
        if status is None:
            status = "pending" if user is not None else "approved"
        try:
            with transaction.atomic():
                if user is not None and course:
                    exists = Review.objects.filter(professor=prof, course__iexact=course, author=user).exists()
                    if exists:
                        raise ValueError("duplicate review")
                r = Review.objects.create(
                    professor=prof, course=course, term=clean_text(data.get("term") or ""),
                    overall=data["overall"], difficulty=data["difficulty"],
                    would_take_again=data["would_take_again"], grade=data.get("grade", ""),
                    comment=comment, status=status, author=user,
                )
                for t in data.get("tags", []):
                    tag, _ = Tag.objects.get_or_create(name=str(t))
                    r.tags.add(tag)
        except IntegrityError:
            raise ValueError("duplicate review")
        return self._review_to_dc(r)

    def vote(self, review_id: str, helpful: bool, user: Any = None) -> None:
        """Record a helpful/not-helpful vote, rejecting repeat votes by a user."""
        with transaction.atomic():
            r = Review.objects.select_for_update().get(pk=review_id)
            if user is not None:
                _, created = Vote.objects.get_or_create(
                    review=r, user=user, defaults={"helpful": helpful}
                )
                if not created:
                    raise ValueError("already voted")
            if helpful:
                Review.objects.filter(pk=review_id).update(helpful=F("helpful") + 1)
            else:
                Review.objects.filter(pk=review_id).update(not_helpful=F("not_helpful") + 1)

    def report(self, review_id: str, user: Any = None, reason: str = Report.REASON_OTHER) -> None:
        """Record a report for a review, ignoring repeat reports by a user."""
        with transaction.atomic():
            r = Review.objects.select_for_update().get(pk=review_id)
            if user is not None:
                _, created = Report.objects.get_or_create(
                    review=r, user=user, defaults={"reason": reason}
                )
                if not created:
                    return
            Review.objects.filter(pk=review_id).update(reported=F("reported") + 1)

    def approve_review(self, review_id: str) -> None:
        """Mark a review as approved."""
        Review.objects.filter(pk=review_id).update(status="approved")
        invalidate_catalog_cache()

    def update_review(self, review_id: str, data: Mapping[str, Any]) -> None:
        review = Review.objects.get(pk=review_id)
        review.course = clean_text(data.get("course") or "")
        review.term = clean_text(data.get("term") or "")
        review.overall = data["overall"]
        review.difficulty = data["difficulty"]
        review.would_take_again = data["would_take_again"]
        review.grade = data.get("grade", "")
        review.comment = clean_text(data.get("comment") or "")
        review.status = "pending"
        review.save()
        review.tags.set([Tag.objects.get_or_create(name=str(tag))[0] for tag in data.get("tags", [])])
        invalidate_catalog_cache()

    def delete_review(self, review_id: str) -> None:
        Review.objects.filter(pk=review_id).delete()
        invalidate_catalog_cache()

    def approve_professor(self, professor_id: str) -> None:
        """Mark a professor as approved."""
        Professor.objects.filter(pk=professor_id).update(status="approved")
        invalidate_catalog_cache()

    # reads
    def schools(self) -> list[SchoolDC]:
        """Return all schools."""
        return [self._school_to_dc(s) for s in School.objects.all()]

    def cities(self) -> list[str]:
        """Return the sorted set of school cities."""
        # clear model ordering, otherwise DISTINCT silently includes it and the
        # same city shows up once per school.
        return sorted(School.objects.order_by().values_list("city", flat=True).distinct())

    def departments(self) -> list[str]:
        """Return the sorted set of departments for approved professors."""
        return sorted(
            Professor.objects.filter(status="approved")
            .order_by()
            .values_list("department", flat=True)
            .distinct()
        )

    def school(self, school_id: str) -> SchoolDC | None:
        """Return a school by id, or ``None`` if it does not exist."""
        try:
            return self._school_to_dc(School.objects.get(pk=school_id))
        except School.DoesNotExist:
            return None

    def school_by_slug(self, slug: str) -> SchoolDC | None:
        """Return a school by slug, or ``None`` if it does not exist."""
        try:
            return self._school_to_dc(School.objects.get(slug=slug))
        except School.DoesNotExist:
            return None

    def professor(self, slug: str) -> ProfessorDC | None:
        """Return an approved professor by slug, or ``None``."""
        try:
            return self._prof_to_dc(
                Professor.objects.select_related("school").get(slug=slug, status="approved")
            )
        except Professor.DoesNotExist:
            return None

    def professor_any(self, slug: str) -> ProfessorDC | None:
        """Return a professor by slug regardless of moderation status."""
        try:
            return self._prof_to_dc(Professor.objects.select_related("school").get(slug=slug))
        except Professor.DoesNotExist:
            return None

    def professor_by_id(self, professor_id: str) -> ProfessorDC | None:
        """Return a professor by id, or ``None`` if it does not exist."""
        try:
            return self._prof_to_dc(
                Professor.objects.select_related("school").get(pk=professor_id)
            )
        except Professor.DoesNotExist:
            return None

    def professors_for_school(self, school_id: str) -> list[ProfessorDC]:
        """Return approved professors belonging to a school."""
        qs = Professor.objects.filter(school_id=school_id, status="approved").select_related("school")
        return [self._prof_to_dc(p) for p in qs]

    def review(self, review_id: str) -> ReviewDC | None:
        """Return a review by id, or ``None`` for missing or malformed ids."""
        try:
            # Fetch tags eagerly: ``_review_to_dc`` always reads them, so a lazy
            # lookup here would add one query per call and N+1 in any batch use.
            qs = Review.objects.prefetch_related("tags")
            return self._review_to_dc(qs.get(pk=review_id))
        except (Review.DoesNotExist, ValidationError, ValueError):
            # ValidationError: malformed UUID coming straight from the URL.
            return None

    def reviews_for(self, professor_id: str, sort: str = "new", include_pending: bool = False) -> list[ReviewDC]:
        """Return a professor's reviews, optionally including non-approved ones."""
        qs = Review.objects.filter(professor_id=professor_id)
        if not include_pending:
            qs = qs.filter(status="approved")
        qs = qs.prefetch_related("tags")
        if sort in ("helpful", "high", "low", "old"):
            items = list(qs)
            if sort == "helpful":
                items.sort(key=lambda r: r.helpful, reverse=True)
            elif sort == "high":
                items.sort(key=lambda r: r.overall, reverse=True)
            elif sort == "low":
                items.sort(key=lambda r: r.overall)
            else:
                items.sort(key=lambda r: r.created_at)
            return [self._review_to_dc(r) for r in items]
        else:
            return [self._review_to_dc(r) for r in qs.order_by("-created_at")]

    def all_reviews(self, include_pending: bool = False) -> list[ReviewDC]:
        """Return all reviews, optionally including non-approved ones."""
        qs = Review.objects.all().prefetch_related("tags")
        if not include_pending:
            qs = qs.filter(status="approved")
        return [self._review_to_dc(r) for r in qs]

    def reviews_for_user(self, user: Any) -> list[dict[str, Any]]:
        """Return a user's reviews with catalog data, regardless of status."""
        qs = (
            Review.objects.filter(author=user)
            .select_related("professor__school")
            .prefetch_related("tags")
            .order_by("-created_at")
        )
        return [
            {
                "review": self._review_to_dc(review),
                "prof": self._prof_to_dc(review.professor),
                "school": self._school_to_dc(review.professor.school),
            }
            for review in qs
        ]

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        """Return the zero-value stats payload used for unrated professors."""
        return {
            "count": 0, "overall": 0, "difficulty": 0, "wta_pct": None,
            "tone": "none",
            "distribution": [{"score": s, "count": 0, "pct": 0} for s in range(5, 0, -1)],
            "top_tags": [],
        }

    def _stats_map(self, professor_ids: Iterable[Any]) -> dict[str, dict[str, Any]]:
        """Stats for many professors in a constant number of queries (no N+1)."""
        ids = [str(i) for i in professor_ids]
        out = {pid: self._empty_stats() for pid in ids}
        if not ids:
            return out

        # Aggregate rating counts once. Deriving averages and distributions from
        # these grouped counts avoids issuing separate aggregate queries.
        rating_counts = (
            Review.objects.filter(professor_id__in=ids, status="approved")
            .values("professor_id")
            .values("professor_id", "overall")
            .annotate(n=Count("id"), difficulty_sum=Sum("difficulty"),
                      wta=Count("id", filter=Q(would_take_again=True)))
            .order_by()
        )
        dist_map = {}
        for row in rating_counts:
            pid = str(row["professor_id"])
            stats = out[pid]
            n = row["n"]
            stats["count"] += n
            stats["overall"] += row["overall"] * n
            stats["difficulty"] += row["difficulty_sum"]
            stats["wta_pct"] = (stats["wta_pct"] or 0) + row["wta"]
            dist_map.setdefault(pid, {})[row["overall"]] = n
        for pid, counts in dist_map.items():
            stats = out[pid]
            n = stats["count"]
            stats.update({
                "overall": round(stats["overall"] / n, 1),
                "difficulty": round(stats["difficulty"] / n, 1),
                "wta_pct": round(100 * stats["wta_pct"] / n),
            })
            stats["tone"] = _tone(stats["overall"])
            stats["distribution"] = [
                {"score": s, "count": counts.get(s, 0),
                 "pct": round(100 * counts.get(s, 0) / n) if n else 0}
                for s in range(5, 0, -1)
            ]

        # Top tags per professor.
        tag_map = {}
        for row in (
            Review.objects.filter(professor_id__in=ids, status="approved")
            .exclude(tags__isnull=True)
            .values("professor_id", "tags__name")
            .annotate(c=Count("id"))
            .order_by()
        ):
            tag_map.setdefault(str(row["professor_id"]), Counter())[str(row["tags__name"])] += row["c"]
        for pid, counter in tag_map.items():
            out[pid]["top_tags"] = [t for t, _ in counter.most_common(3)]

        return out

    def stats(self, professor_id: str) -> dict[str, Any]:
        """Return aggregate rating statistics for a professor."""
        return get_or_set(
            stats_cache_key(professor_id),
            lambda: self._stats_map([professor_id])[str(professor_id)],
        )

    def school_stats(self, school_id: str) -> dict[str, Any]:
        """Return aggregate rating statistics for a school."""
        prof_ids = list(
            Professor.objects.filter(school_id=school_id, status="approved")
            .values_list("pk", flat=True)
        )
        n = len(prof_ids)
        if not n:
            return {"prof_count": 0, "review_count": 0, "avg_overall": 0}
        stats_map = self._stats_map(prof_ids)
        total = sum(s["count"] for s in stats_map.values())
        if not total:
            return {"prof_count": n, "review_count": 0, "avg_overall": 0}
        rated = [s["overall"] for s in stats_map.values() if s["count"]]
        avg = round(mean(rated), 1) if rated else 0
        return {"prof_count": n, "review_count": total, "avg_overall": avg}

    def search(
        self,
        q: str = "",
        school_slug: str = "",
        city: str = "",
        department: str = "",
        sort: str = "rating",
    ) -> list[dict[str, Any]]:
        """Search approved professors with optional filters and sorting."""
        qs = Professor.objects.filter(status="approved").select_related("school")
        school_slug = school_slug.strip()
        city = city.strip()
        department = department.strip()
        if school_slug:
            qs = qs.filter(school__slug=school_slug)
        if city:
            qs = qs.filter(school__city=city)
        want_dept = department.lower().strip()
        q = q.lower().strip()
        # filter in python for case-insensitive unicode
        profs = [
            p for p in qs
            if (not want_dept or p.department.lower().strip() == want_dept)
            and (not q or q in f"{p.full_name} {p.department}".lower())
        ]
        stats_map = self._stats_map(p.pk for p in profs)
        out = []
        for p in profs:
            out.append({
                "prof": self._prof_to_dc(p),
                "school": self._school_to_dc(p.school),
                "stats": stats_map[str(p.pk)],
            })
        out.sort(key=_search_sort_key(sort))
        return out

    def has_user_reviewed(self, professor_id: str, course: str, user: Any) -> bool:
        """Return whether a user already reviewed this course for a professor."""
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        return Review.objects.filter(professor_id=professor_id, course__iexact=course.strip(), author=user).exists()

    def has_user_voted(self, review_id: str, user: Any) -> bool:
        """Return whether a user already voted on a review."""
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        return Vote.objects.filter(review_id=review_id, user=user).exists()

    # helpers: ORM object -> dataclass-like used by views/templates (duck typing)
    def _school_to_dc(self, s: School) -> SchoolDC:
        """Convert an ORM school into the view-facing dataclass."""
        from .repository import School as DCSchool
        return DCSchool(id=str(s.pk), name=s.name, slug=s.slug, city=s.city)

    def _prof_to_dc(self, p: Professor) -> ProfessorDC:
        """Convert an ORM professor into the view-facing dataclass."""
        from .repository import Professor as DCProf
        return DCProf(
            id=str(p.pk), school_id=str(p.school_id), first_name=p.first_name,
            last_name=p.last_name, slug=p.slug, department=p.department,
            title=p.title, status=p.status,
        )

    def _review_to_dc(self, r: Review) -> ReviewDC:
        """Convert an ORM review into the view-facing dataclass."""
        from .repository import Review as DCReview
        tags = [str(t.name) for t in r.tags.all()] if hasattr(r, 'tags') else []
        return DCReview(
            professor_id=str(r.professor_id), course=r.course, term=r.term,
            overall=r.overall, difficulty=r.difficulty, would_take_again=r.would_take_again,
            grade=r.grade, comment=r.comment, tags=tags,
            id=str(r.pk), helpful=r.helpful, not_helpful=r.not_helpful,
            created_at=r.created_at, status=r.status, author_id=r.author_id,
            reported=r.reported,
        )
