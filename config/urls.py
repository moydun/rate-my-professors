from django.contrib import admin
from django.contrib.sitemaps import views as sitemap_views
from django.http import HttpResponse
from django.urls import include, path, reverse

from reviews import views as reviews_views
from reviews.sitemaps import sitemaps

handler404 = reviews_views.handler404
handler500 = reviews_views.handler500


def robots_txt(request):
    sitemap_url = request.build_absolute_uri(reverse("sitemap_index"))
    content = f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}\n"
    return HttpResponse(content, content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_views.index, {"sitemaps": sitemaps}, name="sitemap_index"),
    path(
        "sitemap-<section>.xml",
        sitemap_views.sitemap,
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("", include("reviews.urls")),
]
