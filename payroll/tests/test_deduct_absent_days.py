"""
"Deduct absent days in payslips" setting.

When it is on, Generate Payslip uses the attendance summary, so every working
day with no attendance counts as loss of pay. When it is off, the employee is
left out of the summary and only approved unpaid leave is deducted.
"""

from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from employee.models import Employee
from horilla.testkit import make_company, make_employee, make_user
from payroll.methods.methods import attendance_summaries, deducts_absent_days
from payroll.models.tax_models import PayrollSettings

SUMMARY = "attendance.views.summary.build_monthly_summary"
START, END = date(2026, 10, 1), date(2026, 10, 31)


def _rows(start_date, end_date, employees):
    return [{"employee": e, "absent": 5} for e in employees], 22, {}


class DeductsAbsentDaysTests(TestCase):
    def setUp(self):
        PayrollSettings.objects.entire().delete()
        self.company = make_company("Absent Co")
        self.employee = make_employee(company=self.company, email="a@test.horilla")

    def test_on_when_no_setting_exists(self):
        self.assertTrue(deducts_absent_days(self.employee))

    def test_company_setting_off(self):
        PayrollSettings.objects.entire().create(
            company_id=self.company, deduct_absent_days=False
        )
        self.assertFalse(deducts_absent_days(self.employee))

    def test_falls_back_to_all_companies_setting(self):
        PayrollSettings.objects.entire().create(
            company_id=None, deduct_absent_days=False
        )
        self.assertFalse(deducts_absent_days(self.employee))

    def test_company_setting_wins_over_all_companies(self):
        PayrollSettings.objects.entire().create(
            company_id=None, deduct_absent_days=False
        )
        PayrollSettings.objects.entire().create(
            company_id=self.company, deduct_absent_days=True
        )
        self.assertTrue(deducts_absent_days(self.employee))


class AttendanceSummariesTests(TestCase):
    def setUp(self):
        PayrollSettings.objects.entire().delete()
        self.on_company = make_company("On Co")
        self.off_company = make_company("Off Co")
        PayrollSettings.objects.entire().create(
            company_id=self.off_company, deduct_absent_days=False
        )
        self.on_emp = make_employee(company=self.on_company, email="on@test.horilla")
        self.off_emp = make_employee(
            company=self.off_company, email="off@test.horilla"
        )

    @patch(SUMMARY, side_effect=_rows)
    def test_only_employees_with_setting_on_get_a_summary(self, summary):
        employees = Employee.objects.entire().filter(
            pk__in=[self.on_emp.pk, self.off_emp.pk]
        )
        result = attendance_summaries(employees, START, END)

        self.assertEqual(set(result), {self.on_emp.pk})
        passed = list(summary.call_args.args[2])
        self.assertEqual(passed, [self.on_emp])

    @patch(SUMMARY, side_effect=_rows)
    def test_summary_skipped_when_everyone_is_off(self, summary):
        employees = Employee.objects.entire().filter(pk=self.off_emp.pk)
        self.assertEqual(attendance_summaries(employees, START, END), {})
        summary.assert_not_called()


class ToggleViewTests(TestCase):
    def setUp(self):
        PayrollSettings.objects.entire().delete()
        self.company = make_company("Toggle Co")
        self.client = Client()
        admin = make_user("toggle_admin", is_superuser=True)
        make_employee(company=self.company, email="admin@test.horilla", user=admin)
        self.client.force_login(admin)
        session = self.client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        self.url = reverse("toggle-deduct-absent-days")

    def _setting(self):
        return PayrollSettings.objects.entire().get(company_id=self.company)

    def test_turn_off_then_on(self):
        response = self.client.post(self.url, {}, HTTP_HX_REQUEST="true")
        self.assertIn(response.status_code, (200, 204))
        self.assertFalse(self._setting().deduct_absent_days)

        self.client.post(self.url, {"deduct_absent_days": "on"}, HTTP_HX_REQUEST="true")
        self.assertTrue(self._setting().deduct_absent_days)
        self.assertEqual(PayrollSettings.objects.entire().count(), 1)

    def test_needs_permission(self):
        client = Client()
        user = make_user("no_perm_user")
        make_employee(company=self.company, email="noperm@test.horilla", user=user)
        client.force_login(user)
        client.post(self.url, {}, HTTP_HX_REQUEST="true")
        # Any page load may create the default row (setting on); the denied
        # request must not have turned it off.
        self.assertFalse(
            PayrollSettings.objects.entire()
            .filter(company_id=self.company, deduct_absent_days=False)
            .exists()
        )
