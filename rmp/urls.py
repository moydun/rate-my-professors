from django.contrib import admin
from django.http import HttpResponse
from django.urls import path


def home(request):
    return HttpResponse('Rate My Professors Kyrgyzstan')


urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
]
