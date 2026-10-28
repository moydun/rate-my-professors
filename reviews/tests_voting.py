"""Edge-case tests for review voting and moderation.

Voting is exercised through the HTTP view against *both* repositories
(in-memory and Django ORM) so the two backends cannot drift apart. Moderation
is only exposed through the Django admin, so those tests assert that a
non-moderator can never approve anything: anonymous and regular users are
redirected to the admin login, staff without model permissions are refused,
and only a permitted moderator can approve a review.
"""
from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .admin import ReviewAdmin
from .repository import InMemoryRepository
from .seed import seed
from .tests import _repo_review


class _VotingViewMixin:
    """Patch views' repository to an isolated one, memory or ORM."""

    use_django = False

    def setUp(self):
        import reviews.views as v
        from reviews import store as store_mod

        if self.use_django:
            from reviews.django_repo import DjangoRepository
            from reviews.management.commands.seed_kg import Command as SeedCmd

            self.repo = DjangoRepository()
            SeedCmd().handle(clear=True)
        else:
            self.repo = InMemoryRepository()
            seed(self.repo)

        self._orig = store_mod.repo
        self._orig_v = v.repo
        store_mod.repo = self.repo
        v.repo = self.repo

    def tearDown(self):
        import reviews.views as v
        from reviews import store as store_mod

        store_mod.repo = self._orig
        v.repo = self._orig_v

    def login(self, email="voter@example.com"):
        user = User.objects.create_user(username=email, email=email, password="pass12345")
        self.client.force_login(user)
        return user

    def prof(self, q="Айбек"):
        return self.repo.search(q)[0]["prof"]

    def seed_review(self):
        """A seeded, author-less approved review (anyone may vote)."""
        return self.repo.reviews_for(self.prof().id)[0]

    def authored_review(self, user):
        """An approved review written by ``user``."""
        review = self.repo.add_review(self.prof().id, _repo_review(course="Свой курс"), user=user)
        self.repo.approve_review(review.id)
        return self.repo.review(review.id)


class VotingEdgeViewTestsMixin:
    # --- unauthorized -----------------------------------------------------
    def test_anonymous_vote_redirects_to_login(self):
        review = self.seed_review()
        url = reverse("review_vote", args=[review.id])
        before = self.repo.review(review.id)
        before_counts = (before.helpful, before.not_helpful)
        r = self.client.post(url, {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r.url)
        self.assertIn(f"next={url}", r.url)
        after = self.repo.review(review.id)
        self.assertEqual((after.helpful, after.not_helpful), before_counts)

    # --- nonexistent id ---------------------------------------------------
    def test_nonexistent_review_id_returns_404(self):
        self.login("missing@example.com")
        r = self.client.post(reverse("review_vote", args=["does-not-exist"]), {"vote": "up"})
        self.assertEqual(r.status_code, 404)

    def test_unknown_uuid_returns_404(self):
        self.login("unknown@example.com")
        unknown = "00000000-0000-0000-0000-000000000000"
        r = self.client.post(reverse("review_vote", args=[unknown]), {"vote": "up"})
        self.assertEqual(r.status_code, 404)

    # --- one vote per user ------------------------------------------------
    def test_vote_increments_once(self):
        self.login("once@example.com")
        review = self.seed_review()
        before = self.repo.review(review.id).helpful
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.repo.review(review.id).helpful, before + 1)

    def test_down_vote_increments_not_helpful(self):
        self.login("down-vote@example.com")
        review = self.seed_review()
        before = self.repo.review(review.id)
        before_counts = (before.helpful, before.not_helpful)
        r = self.client.post(
            reverse("review_vote", args=[review.id]), {"vote": "down"}
        )
        self.assertEqual(r.status_code, 302)
        after = self.repo.review(review.id)
        self.assertEqual(after.helpful, before_counts[0])
        self.assertEqual(after.not_helpful, before_counts[1] + 1)

    def test_vote_buttons_submit_their_value(self):
        self.login("buttons@example.com")
        r = self.client.get(reverse("professor", args=[self.prof().slug]))
        self.assertContains(r, 'type="submit" name="vote" value="up"')
        self.assertContains(r, 'type="submit" name="vote" value="down"')

    def test_repeat_vote_shows_clear_message(self):
        self.login("repeat-message@example.com")
        review = self.seed_review()
        url = reverse("review_vote", args=[review.id])
        self.client.post(url, {"vote": "up"})
        r = self.client.post(url, {"vote": "up"}, follow=True)
        self.assertContains(r, "Вы уже голосовали за этот отзыв.")

    def test_repeat_same_vote_is_not_counted_twice(self):
        self.login("repeat@example.com")
        review = self.seed_review()
        url = reverse("review_vote", args=[review.id])
        self.client.post(url, {"vote": "up"})
        self.client.post(url, {"vote": "up"})
        after = self.repo.review(review.id)
        self.assertEqual(after.helpful, 1)
        self.assertEqual(after.not_helpful, 0)

    def test_repeat_opposite_vote_is_rejected(self):
        self.login("flip@example.com")
        review = self.seed_review()
        url = reverse("review_vote", args=[review.id])
        self.client.post(url, {"vote": "up"})
        self.client.post(url, {"vote": "down"})
        after = self.repo.review(review.id)
        self.assertEqual(after.helpful, 1)
        self.assertEqual(after.not_helpful, 0)

    def test_missing_vote_value_counts_as_down(self):
        self.login("down@example.com")
        review = self.seed_review()
        self.client.post(reverse("review_vote", args=[review.id]), {})
        after = self.repo.review(review.id)
        self.assertEqual(after.helpful, 0)
        self.assertEqual(after.not_helpful, 1)

    # --- voting on one's own review --------------------------------------
    def test_own_review_vote_is_ignored(self):
        author = self.login("author@example.com")
        review = self.authored_review(author)
        before = self.repo.review(review.id)
        r = self.client.post(
            reverse("review_vote", args=[review.id]), {"vote": "up"}, follow=True
        )
        self.assertEqual(r.status_code, 200)
        after = self.repo.review(review.id)
        self.assertEqual((after.helpful, after.not_helpful), (before.helpful, before.not_helpful))
        self.assertContains(r, "Нельзя голосовать за свой отзыв")

    def test_other_user_may_vote_on_authored_review(self):
        author = self.login("author2@example.com")
        review = self.authored_review(author)
        other = User.objects.create_user(
            username="other2@example.com", email="other2@example.com", password="pass12345"
        )
        self.client.force_login(other)
        before = self.repo.review(review.id).helpful
        r = self.client.post(reverse("review_vote", args=[review.id]), {"vote": "up"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.repo.review(review.id).helpful, before + 1)


class MemoryVotingEdgeViewTests(VotingEdgeViewTestsMixin, _VotingViewMixin, TestCase):
    use_django = False


class DjangoVotingEdgeViewTests(VotingEdgeViewTestsMixin, _VotingViewMixin, TestCase):
    use_django = True


# ---------------------------------------------------------------------------
# Moderation: only a permitted moderator may approve
# ---------------------------------------------------------------------------
class ModerationAccessTests(TestCase):
    def setUp(self):
        from reviews.management.commands.seed_kg import Command as SeedCmd

        SeedCmd().handle(clear=True)

    def _user(self, email, **flags):
        return User.objects.create_user(username=email, email=email, password="pass12345", **flags)

    def test_anonymous_is_redirected_to_admin_login(self):
        r = self.client.get(reverse("admin:reviews_review_changelist"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r.url)

    def test_non_moderator_is_redirected_to_admin_login(self):
        self.client.force_login(self._user("plain@example.com"))
        r = self.client.get(reverse("admin:reviews_review_changelist"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r.url)

    def test_staff_without_permissions_is_forbidden(self):
        self.client.force_login(self._user("staff@example.com", is_staff=True))
        r = self.client.get(reverse("admin:reviews_review_changelist"))
        self.assertEqual(r.status_code, 403)

    def test_permitted_moderator_can_open_changelist(self):
        moderator = self._user("mod@example.com", is_staff=True)
        moderator.user_permissions.add(Permission.objects.get(codename="change_review"))
        self.client.force_login(moderator)
        r = self.client.get(reverse("admin:reviews_review_changelist"))
        self.assertEqual(r.status_code, 200)

    def test_non_moderator_cannot_submit_admin_action(self):
        from reviews.django_repo import DjangoRepository
        from reviews.models import Review

        repo = DjangoRepository()
        prof = repo.search("Айбек")[0]["prof"]
        author = self._user("sneaky_author@example.com")
        pending = repo.add_review(prof.id, _repo_review(course="Секретный курс"), user=author)
        self.client.force_login(self._user("sneaky@example.com"))
        r = self.client.post(
            reverse("admin:reviews_review_changelist"),
            {"action": "approve_selected", "_selected_action": [str(pending.id)]},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Review.objects.get(pk=pending.id).status, "pending")

    def test_moderator_action_approves_and_rejects(self):
        from reviews.django_repo import DjangoRepository
        from reviews.models import Review

        repo = DjangoRepository()
        prof = repo.search("Айбек")[0]["prof"]
        author = self._user("action_author@example.com")
        pending = repo.add_review(prof.id, _repo_review(course="На модерации"), user=author)
        self.assertEqual(Review.objects.get(pk=pending.id).status, "pending")

        review_admin = ReviewAdmin(Review, admin.site)
        review_admin.approve_selected(None, Review.objects.filter(pk=pending.id))
        self.assertEqual(Review.objects.get(pk=pending.id).status, "approved")

        review_admin.reject_selected(None, Review.objects.filter(pk=pending.id))
        self.assertEqual(Review.objects.get(pk=pending.id).status, "rejected")

    def test_approving_unknown_review_is_a_noop(self):
        from reviews.django_repo import DjangoRepository

        repo = DjangoRepository()
        unknown = "00000000-0000-0000-0000-0000000000ff"
        repo.approve_review(unknown)
        repo.approve_professor(unknown)
