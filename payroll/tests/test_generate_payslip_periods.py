"""
Generate Payslip: each employee gets their own period. A contract that
starts after the period start shortens only that employee's payslip.
"""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from horilla.testkit import make_company, make_employee, make_user
from payroll.models.models import Contract

VIEWS = "payroll.views.component_views"


def _fake_payslip(employee, start_date, end_date, month_summary=None):
    return {
        "start_date": start_date,
        "end_date": end_date,
        "contract_wage": 0,
        "basic_pay": 0,
        "gross_pay": 0,
        "total_deductions": 0,
        "net_pay": 0,
        "json_data": json.dumps({}),
        "installments": [],
    }


class GeneratePayslipPeriodTests(TestCase):
    def setUp(self):
        self.company = make_company("Batch Co")
        admin = make_user("batch_admin", is_superuser=True)
        make_employee(company=self.company, email="admin@batch.horilla", user=admin)
        self.client = Client()
        self.client.force_login(admin)
        session = self.client.session
        session["selected_company"] = str(self.company.pk)
        session.save()
        # The late joiner is created first so the batch handles them first.
        self.late = self._employee("late@batch.horilla", date(2026, 10, 20))
        self.regular = self._employee("regular@batch.horilla", date(2026, 1, 1))

    def _employee(self, email, contract_start):
        employee = make_employee(company=self.company, email=email)
        Contract.objects.filter(employee_id=employee).delete()
        Contract.objects.create(
            contract_name="Monthly",
            employee_id=employee,
            contract_start_date=contract_start,
            wage_type="monthly",
            wage=36500,
            contract_status="active",
        )
        return employee

    @patch(f"{VIEWS}.notify.send")
    @patch(f"{VIEWS}.save_payslip", return_value=SimpleNamespace(id=1))
    @patch(f"{VIEWS}.calculate_employer_contribution")
    @patch(f"{VIEWS}.apply_previous_period_leave_adjustment")
    @patch(f"{VIEWS}.attendance_summaries", return_value={})
    @patch(f"{VIEWS}.payroll_calculation", side_effect=_fake_payslip)
    def test_late_contract_start_only_shortens_that_employee(self, calc, *_mocks):
        self.client.post(
            reverse("generate-payslip"),
            {
                "employee_id": [self.late.pk, self.regular.pk],
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
                "group_name": "October",
            },
        )
        starts = {call.args[0].pk: call.args[1] for call in calc.call_args_list}
        self.assertEqual(
            starts,
            {self.late.pk: date(2026, 10, 20), self.regular.pk: date(2026, 10, 1)},
        )
