from django.core.management.base import BaseCommand

from reviews.models import Professor, Review, School, Tag
from reviews.seed import PROFESSORS, REVIEWS, SCHOOLS


class Command(BaseCommand):
    help = "Заливает демо-данные из reviews/seed.py в БД (удаляет старые демо-данные)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Очистить существующие школы/преподавателей/отзывы перед заливкой",
        )

    def handle(self, *args, **opts):
        if opts["clear"]:
            Review.objects.all().delete()
            Professor.objects.all().delete()
            School.objects.all().delete()
            Tag.objects.all().delete()

        # avoid duplicates if run twice
        if School.objects.exists() and not opts["clear"]:
            self.stdout.write(self.style.WARNING("В БД уже есть данные. Используйте --clear для перезаливки."))
            return

        from django.utils.text import slugify
        schools = []
        for name, city in SCHOOLS:
            slug = slugify(name, allow_unicode=True) or slugify(name)
            base, i = slug, 1
            while School.objects.filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            s = School.objects.create(name=name, city=city, slug=slug)
            schools.append(s)

        profs = []
        for si, first, last, dept, title in PROFESSORS:
            school = schools[si]
            base = slugify(f"{first} {last}", allow_unicode=True) or slugify(f"{first} {last}")
            slug = base
            i = 1
            while Professor.objects.filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            p = Professor.objects.create(
                school=school, first_name=first, last_name=last, slug=slug,
                department=dept, title=title, status="approved",
            )
            profs.append(p)

        for pi, course, term, overall, diff, wta, grade, comment, tags in REVIEWS:
            r = Review.objects.create(
                professor=profs[pi], course=course, term=term, overall=overall, difficulty=diff,
                would_take_again=wta, grade=grade, comment=comment, status="approved",
            )
            for t in tags:
                tag, _ = Tag.objects.get_or_create(name=t)
                r.tags.add(tag)

        self.stdout.write(self.style.SUCCESS(
            f"Готово: {len(schools)} вузов, {len(profs)} преподавателей, {len(REVIEWS)} отзывов."
        ))
