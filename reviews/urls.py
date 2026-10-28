from django.urls import path

from . import views
from .views import SafeLogoutView, ThrottledLoginView

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("healthz", views.healthz, name="healthz"),
    path("rules/", views.rules, name="rules"),
    path("about/", views.about, name="about"),
    path("professors/add/", views.add_professor, name="add_professor"),
    path("schools/<str:slug>/", views.school_detail, name="school_detail"),
    path("professors/<str:slug>/", views.professor_detail, name="professor"),
    path("professors/<str:slug>/rate/", views.review_new, name="review_new"),
    path("reviews/<str:review_id>/vote/", views.review_vote, name="review_vote"),
    path("reviews/<str:review_id>/report/", views.review_report, name="review_report"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/profile/", views.profile, name="profile"),
    path("reviews/<str:review_id>/edit/", views.review_edit, name="review_edit"),
    path("reviews/<str:review_id>/delete/", views.review_delete, name="review_delete"),
    path("accounts/login/", ThrottledLoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", SafeLogoutView.as_view(), name="logout"),
]
