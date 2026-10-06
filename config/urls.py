from django.contrib import admin
from django.urls import path
from inspections import views

urlpatterns = [
    path('', views.index, name='inspection-home'),
    path('api/csrf', views.csrf, name='csrf-token'),
    path('admin/', admin.site.urls),
]
