from django.core.cache import cache
from django.test import TestCase

from .django_repo import DjangoRepository
from .models import Professor, Review, School
from .caching import stats_cache_key


class ProfessorStatsCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.repo = DjangoRepository()
        school = School.objects.create(name="Test University", slug="test-university", city="Test")
        self.professor = Professor.objects.create(
            school=school, first_name="Test", last_name="Teacher",
            slug="test-teacher", department="Test", title="Professor",
            status="approved",
        )

    def tearDown(self):
        cache.clear()

    def make_review(self, overall):
        return Review.objects.create(
            professor=self.professor, course="Course", overall=overall,
            difficulty=2, would_take_again=True, comment="Test review",
            status="approved",
        )

    def test_stats_are_cached(self):
        self.make_review(4)
        professor_id = str(self.professor.pk)
        stats = self.repo.stats(professor_id)
        self.assertEqual(stats["overall"], 4)
        self.assertEqual(cache.get(stats_cache_key(professor_id)), stats)

    def test_review_create_and_update_invalidate_stats(self):
        professor_id = str(self.professor.pk)
        self.assertEqual(self.repo.stats(professor_id)["count"], 0)
        review = self.make_review(3)
        self.assertEqual(self.repo.stats(professor_id)["overall"], 3)
        review.overall = 5
        review.save()
        self.assertEqual(self.repo.stats(professor_id)["overall"], 5)
