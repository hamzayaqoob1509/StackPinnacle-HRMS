"""
employee_handbook/urls.py
"""

from django.urls import path

from employee_handbook import views

urlpatterns = [
    path("", views.handbook_view, name="handbook-view"),
    path("list/", views.handbook_list, name="handbook-list"),
    path("create/", views.handbook_create, name="handbook-create"),
    path("view/<int:pk>/", views.handbook_detail, name="handbook-detail"),
    path("delete/<int:pk>/", views.handbook_delete, name="handbook-delete"),
]
