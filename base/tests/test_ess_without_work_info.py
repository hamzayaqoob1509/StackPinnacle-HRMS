"""
ESS pages for an employee with no work-info record and no company.

Production has employees like this (no EmployeeWorkInformation row, no
CompanyGroupAssignment). Under COMPANY_SCOPED_PERMISSIONS their session falls
back to "All my companies" with an empty company list, so every
company-scoped queryset is empty. Incomplete HR data must degrade the page,
not 500 it -- /ess/ crashed on exactly this after the upgrade.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from employee.models import EmployeeWorkInformation
from horilla.horilla_middlewares import set_selected_company
from horilla.testkit import make_company, make_employee, make_user
from payroll.models.tax_models import PayrollSettings

ESS_URLS = [
    "ess-dashboard",
    "ess-kpi-data",
    "ess-leave-balance",
    "ess-leave-requests",
    "ess-attendance-calendar",
    "ess-work-hours-month",
    "ess-payslips",
    "ess-objectives",
    "ess-announcements",
    "ess-upcoming",
    "home-page",
    "employee-profile",
    "user-request-view",
    "view-my-attendance",
]


@override_settings(COMPANY_SCOPED_PERMISSIONS=True)
class EssWithoutWorkInfoTests(TestCase):
    def setUp(self):
        set_selected_company(None)
        company = make_company("Home Co")
        PayrollSettings.objects.entire().create(company_id=company)
        self.user = make_user("no_work_info", password="secret123")
        employee = make_employee(
            company=company, email="nowork@test.horilla", user=self.user
        )
        EmployeeWorkInformation.objects.entire().filter(employee_id=employee).delete()
        self.client.force_login(self.user)

    def tearDown(self):
        set_selected_company(None)

    def test_pages_do_not_500(self):
        for name in ESS_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertLess(response.status_code, 500, name)
