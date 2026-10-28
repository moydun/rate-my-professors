"""Forms for creating reviews and professors.

All user-supplied free text is passed through ``sanitize.clean_text`` so the
form layer and the repositories agree on what "clean" input looks like.
"""
from django import forms
from django.utils.translation import gettext_lazy as _

from .constants import MAX_COMMENT_LENGTH, MIN_COMMENT_LENGTH
from .models import Report
from .repository import GRADES, TAGS
from .sanitize import clean_text

SCORES = [(5, "5"), (4, "4"), (3, "3"), (2, "2"), (1, "1")]


def _ensure_length(value, minimum, maximum, too_short, too_long):
    """Raise a validation error when ``value`` falls outside the allowed range.

    ``too_short``/``too_long`` are translation templates expecting an ``n``
    placeholder for the bound that was violated.
    """
    if len(value) < minimum:
        raise forms.ValidationError(too_short % {"n": minimum})
    if len(value) > maximum:
        raise forms.ValidationError(too_long % {"n": maximum})


class ReportForm(forms.Form):
    """Validated reason supplied with a review report."""

    reason = forms.ChoiceField(
        choices=Report.REASON_CHOICES,
        required=False,
        initial=Report.REASON_OTHER,
        label=_("Причина жалобы"),
        widget=forms.Select(attrs={"aria-label": _("Причина жалобы")}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["maxlength"] = Report._meta.get_field("reason").max_length

    def clean(self):
        cleaned_data = super().clean()
        reason = self.data.get(self.add_prefix("reason"), "") if self.is_bound else ""
        if len(reason) > Report._meta.get_field("reason").max_length:
            self.add_error("reason", forms.ValidationError(
                _("Отзыв должен содержать не более %(n)d символов."),
                code="max_length",
                params={"n": Report._meta.get_field("reason").max_length},
            ))
        return cleaned_data

    def clean_reason(self):
        """Keep one-click reports compatible by defaulting to ``other``."""
        return self.cleaned_data.get("reason") or Report.REASON_OTHER


class ReviewForm(forms.Form):
    """Student review form with sanitization and length limits."""

    course = forms.CharField(
        max_length=80,
        label=_("Предмет"),
        help_text=_("Например: Алгоритмы, Общая физика, Микроэкономика"),
    )
    term = forms.CharField(
        max_length=20, required=False,
        label=_("Семестр"),
        help_text=_("Например: Осень 2025 или Весна 2025"),
    )
    overall = forms.TypedChoiceField(
        choices=SCORES, coerce=int, widget=forms.RadioSelect,
        label=_("Общее качество (5 — лучше всего)"),
    )
    difficulty = forms.TypedChoiceField(
        choices=SCORES, coerce=int, widget=forms.RadioSelect,
        label=_("Сложность (5 — очень сложно)"),
    )
    would_take_again = forms.ChoiceField(
        choices=[("yes", _("Да")), ("no", _("Нет"))],
        widget=forms.RadioSelect,
        label=_("Пошли бы снова к этому преподавателю?"),
    )
    grade = forms.ChoiceField(
        choices=[("", _("Предпочитаю не указывать"))] + [(str(g), str(g)) for g in GRADES],
        required=False, label=_("Полученная оценка"),
    )
    tags = forms.MultipleChoiceField(
        choices=[(str(t), str(t)) for t in TAGS],
        required=False, widget=forms.CheckboxSelectMultiple,
        label=_("Теги"), help_text=_("Выберите до 3"),
    )
    comment = forms.CharField(
        min_length=MIN_COMMENT_LENGTH, max_length=MAX_COMMENT_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        label=_("Ваш отзыв"),
        help_text=_("Не менее 20 символов. Пишите только об учёбе, без оскорблений и личных данных."),
    )

    def clean_course(self):
        """Trim the free-text course name (no case normalization)."""
        return clean_text(self.cleaned_data["course"])

    def clean_term(self):
        """Trim the optional term string."""
        return clean_text(self.cleaned_data.get("term", ""))

    def clean_comment(self):
        """Sanitize the body and enforce the min/max length on the clean text.

        Control characters are removed first: "junk" characters must not help
        a comment slip past the minimum length requirement.
        """
        comment = clean_text(self.cleaned_data["comment"])
        _ensure_length(
            comment, MIN_COMMENT_LENGTH, MAX_COMMENT_LENGTH,
            too_short=_("Отзыв должен содержать не менее %(n)d символов."),
            too_long=_("Отзыв должен содержать не более %(n)d символов."),
        )
        return comment

    def clean_tags(self):
        """Sanitize tags and cap them at three."""
        tags = [clean_text(t) for t in self.cleaned_data["tags"]]
        if len(tags) > 3:
            raise forms.ValidationError(_("Выберите не более 3 тегов."))
        return tags

    def clean_would_take_again(self):
        """Convert the radio choice into a boolean."""
        return self.cleaned_data["would_take_again"] == "yes"


class ProfessorForm(forms.Form):
    """Submission form for a new professor (goes through moderation)."""

    school = forms.ChoiceField(label=_("Вуз"))
    first_name = forms.CharField(max_length=40, label=_("Имя"))
    last_name = forms.CharField(max_length=40, label=_("Фамилия"))
    department = forms.CharField(max_length=80, label=_("Кафедра"))
    title = forms.ChoiceField(
        label=_("Должность"),
        choices=[
            ("профессор", _("профессор")),
            ("доцент", _("доцент")),
            ("старший преподаватель", _("старший преподаватель")),
            ("преподаватель", _("преподаватель")),
        ],
    )

    def __init__(self, *args, schools=None, **kwargs):
        """Populate the school choices from the provided ``schools`` list."""
        super().__init__(*args, **kwargs)
        if schools is not None:
            self.fields["school"].choices = [(str(s.id), s.name) for s in schools]

    def clean_first_name(self):
        """Sanitize the first name."""
        return clean_text(self.cleaned_data["first_name"])

    def clean_last_name(self):
        """Sanitize the last name."""
        return clean_text(self.cleaned_data["last_name"])

    def clean_department(self):
        """Sanitize the department name."""
        return clean_text(self.cleaned_data["department"])
