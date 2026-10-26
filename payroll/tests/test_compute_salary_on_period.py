"""compute_salary_on_period against Main's payroll.methods (no month_summary kwarg)."""

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from horilla.testkit import make_company, make_employee
from payroll.methods.methods import compute_salary_on_period
from payroll.models.models import Contract

EMPTY_LEAVES = {
    "paid_leave": 0,
    "unpaid_leaves": 0,
    "partial_pay_days": 0,
    "total_leaves": 0,
    "paid_leave_dates": [],
    "unpaid_leave_dates": [],
    "custom_leave_dates": [],
    "custom_leave_breakdown": [],
    "leave_dates": [],
}


class ComputeSalaryOnPeriodTests(TestCase):
    def setUp(self):
        company = make_company("Salary Co")
        self.employee = make_employee(company=company, email="salary@test.horilla")
        Contract.objects.filter(employee_id=self.employee).delete()
        self.start = date(2024, 1, 1)
        self.end = date(2024, 1, 31)

    def _activate(self, **kw):
        defaults = dict(
            contract_name="Active Monthly",
            employee_id=self.employee,
            contract_start_date=date(2024, 1, 1),
            wage_type="monthly",
            wage=30000.0,
            contract_status="active",
            deduct_leave_from_basic_pay=True,
            calculate_daily_leave_amount=True,
            deduction_for_one_leave_amount=0,
        )
        defaults.update(kw)
        return Contract.objects.create(**defaults)

    def test_no_active_contract_returns_none(self):
        self.assertIsNone(compute_salary_on_period(self.employee, self.start, self.end))

    def test_draft_contract_ignored(self):
        self._activate(contract_status="draft", wage=25000.0)
        self.assertIsNone(compute_salary_on_period(self.employee, self.start, self.end))

    @patch("payroll.methods.methods.get_leaves", return_value=EMPTY_LEAVES)
    def test_monthly_with_no_unpaid_leaves(self, _leaves):
        self._activate(wage=36500.0)
        data = compute_salary_on_period(self.employee, self.start, self.end)
        self.assertIsNotNone(data)
        self.assertEqual(data["unpaid_days"], 0)
        self.assertEqual(data["paid_days"], 31)
        self.assertEqual(data["contract_wage"], 36500.0)
        self.assertAlmostEqual(data["basic_pay"], 36500.0)
        self.assertEqual(data["loss_of_pay"], 0)

    @patch("payroll.methods.methods.get_leaves")
    def test_monthly_deducts_unpaid_leaves(self, mock_leaves):
        # Daily rate = 36,500 x 12 / 365 = 1,200
        self._activate(wage=36500.0)
        leaves = dict(EMPTY_LEAVES)
        leaves["unpaid_leaves"] = 2
        mock_leaves.return_value = leaves
        data = compute_salary_on_period(self.employee, self.start, self.end)
        self.assertEqual(data["unpaid_days"], 2)
        self.assertEqual(data["paid_days"], 29)
        self.assertAlmostEqual(data["loss_of_pay"], 2400.0)
        self.assertAlmostEqual(data["basic_pay"], 34100.0)

    @patch("payroll.methods.methods.months_between_range", return_value=[])
    @patch("payroll.methods.methods.get_working_days")
    @patch("payroll.methods.methods.get_leaves", return_value=EMPTY_LEAVES)
    def test_daily_wage_uses_working_days(self, _leaves, mock_wd, _months):
        self._activate(wage_type="daily", wage=500.0)
        mock_wd.return_value = {"total_working_days": 20}
        data = compute_salary_on_period(self.employee, self.start, self.end)
        self.assertEqual(data["paid_days"], 20)
        self.assertEqual(data["unpaid_days"], 0)
        self.assertEqual(data["basic_pay"], 10000.0)
        self.assertEqual(data["contract_wage"], 500.0)
