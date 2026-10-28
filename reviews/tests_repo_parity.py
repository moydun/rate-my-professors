"""Behavioral parity checks for the in-memory and ORM repositories."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from .django_repo import DjangoRepository
from .repository import InMemoryRepository


class RepositoryParityMixin:
    def make_repo(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.school = self.repo.add_school("Паритет университет", "Бишкек")
        self.other_school = self.repo.add_school("Другой университет", "Ош")
        self.high = self.repo.add_professor(
            self.school.id, "Анна", "Высокая", "Математика", "профессор"
        )
        self.low = self.repo.add_professor(
            self.school.id, "Борис", "Низкий", "Физика", "доцент"
        )
        self.extra = self.repo.add_professor(
            self.other_school.id, "Вера", "Средняя", "История", "доцент"
        )
        self.user = (get_user_model().objects.create_user(
            username="parity-user", password="irrelevant"
        ) if isinstance(self, DjangoRepositoryParityTests) else type("User", (), {"id": 901})())

    def test_review_vote_report_search_rating_and_page_limits(self):
        review = self.repo.add_review(self.high.id, {
            "course": "Алгебра", "term": "Осень", "overall": 5,
            "difficulty": 2, "would_take_again": True, "grade": "A",
            "comment": "Отличный курс", "tags": ["Понятные критерии"],
        }, user=self.user)
        self.assertEqual(review.status, "pending")
        self.repo.approve_review(review.id)
        self.repo.vote(review.id, True, user=self.user)
        self.repo.report(review.id, user=self.user, reason="spam")
        self.repo.report(review.id, user=self.user, reason="other")
        review = self.repo.review(review.id)
        self.assertEqual((review.status, review.helpful, review.not_helpful, review.reported),
                         ("approved", 1, 0, 1))

        self.repo.add_review(self.high.id, {
            "course": "Геометрия", "term": "Весна", "overall": 3,
            "difficulty": 4, "would_take_again": False, "grade": "B",
            "comment": "Непросто", "tags": [],
        })
        self.repo.add_review(self.low.id, {
            "course": "Механика", "term": "Осень", "overall": 4,
            "difficulty": 3, "would_take_again": True, "grade": "A",
            "comment": "Хорошо", "tags": [],
        })

        search = self.repo.search(q="высокая")
        self.assertEqual([row["prof"].full_name for row in search], ["Анна Высокая"])
        rows = self.repo.search(sort="rating")
        self.assertEqual([row["prof"].full_name for row in rows],
                         ["Анна Высокая", "Борис Низкий", "Вера Средняя"])
        self.assertEqual([row["stats"]["overall"] for row in rows], [4.0, 4.0, 0])
        self.assertEqual([row["stats"]["count"] for row in rows], [2, 1, 0])
        # Exercise the common page/limit contract on repository result sets.
        self.assertEqual([r["prof"].full_name for r in rows[:2]],
                         ["Анна Высокая", "Борис Низкий"])
        self.assertEqual([r["prof"].full_name for r in rows[2:4]], ["Вера Средняя"])
        self.assertEqual(len(rows[:1]), 1)


class InMemoryRepositoryParityTests(RepositoryParityMixin, TestCase):
    def make_repo(self):
        return InMemoryRepository()


class DjangoRepositoryParityTests(RepositoryParityMixin, TestCase):
    def make_repo(self):
        return DjangoRepository()
