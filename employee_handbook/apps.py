"""
employee_handbook/apps.py

App configuration for the Employee Handbook module. On startup it registers the
module's URL patterns with the project router, mirroring the other Horilla apps.
"""

from django.apps import AppConfig


class EmployeeHandbookConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "employee_handbook"
    verbose_name = "Employee Handbook"

    def ready(self):
        from django.urls import include, path

        from horilla.horilla_settings import APPS
        from horilla.urls import urlpatterns

        APPS.append("employee_handbook")
        urlpatterns.append(
            path("employee-handbook/", include("employee_handbook.urls")),
        )
        super().ready()
