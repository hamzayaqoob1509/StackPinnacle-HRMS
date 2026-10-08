"""
Saving a role's permissions must never wipe them by accident.

The permission matrix sends one codename per ticked box, including
export_<model>. A codename the database does not have invalidated the form,
and the view then cleared every permission of the group. In production this
emptied the admin role when Export was ticked for an app that had no export
permission (Appearance, Employee Handbook, LDAP, Meet).
"""

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from base.signals import _sync_export_permissions
from base.views import _build_permission_matrix
from horilla.testkit import make_company, make_employee, make_user


class UpdateGroupPermissionTests(TestCase):
    def setUp(self):
        user = make_user("root", is_superuser=True)
        make_employee(
            company=make_company("Role Co"), email="root@test.horilla", user=user
        )
        self.client.force_login(user)
        self.group = Group.objects.create(name="HR Managers")
        self.kept = list(
            Permission.objects.filter(codename__in=["view_employee", "add_employee"])
        )
        self.group.permissions.set(self.kept)
        self.url = reverse("update-group-permission")

    def _post(self, permissions):
        response = self.client.post(
            self.url,
            {"id": self.group.id, "name": self.group.name, "permissions": permissions},
        )
        # A redirect to the login page would leave the group untouched and
        # make every "nothing changed" assertion pass for the wrong reason.
        self.assertEqual(response.status_code, 200)
        return response

    def _codenames(self):
        return set(self.group.permissions.values_list("codename", flat=True))

    def test_unknown_codename_changes_nothing(self):
        self._post(["view_employee", "add_employee", "export_no_such_model"])
        self.assertEqual(self._codenames(), {"view_employee", "add_employee"})

    def test_valid_submission_replaces_the_permissions(self):
        self._post(["view_employee"])
        self.assertEqual(self._codenames(), {"view_employee"})

    def test_unticking_everything_still_clears(self):
        self._post([])
        self.assertEqual(self._codenames(), set())


class ExportPermissionCoverageTests(TestCase):
    def test_every_box_in_the_matrix_has_a_permission(self):
        _sync_export_permissions()
        existing = set(Permission.objects.values_list("codename", flat=True))
        matrix, _no_permission_models = _build_permission_matrix()

        missing = []
        for app in matrix:
            for model in app["app_models"]:
                codenames = [
                    f"{action}_{model['model_name']}"
                    for action in ("add", "view", "change", "delete", "export")
                ] + [custom["codename"] for custom in model["custom_permissions"]]
                missing += [
                    f"{app['app']}: {codename}"
                    for codename in codenames
                    if codename not in existing
                ]

        self.assertEqual(missing, [])
