"""Carry-forward of unpaid leave approved after the previous payslip was generated."""

import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from horilla.testkit import make_company, make_employee
from horilla.testkit.factories import make_payslip
from payroll.methods.methods import (
    apply_previous_period_leave_adjustment,
    get_previous_period_leave_adjustment,
)

COMPUTE = "payroll.methods.methods.compute_salary_on_period"


class PreviousPeriodLeaveAdjustmentTests(TestCase):
    def setUp(self):
        company = make_company("Adjustment Co")
        self.employee = make_employee(company=company, email="adjust@test.horilla")
        self.current_start = date(2024, 2, 1)

    def _previous_payslip(self, **pay_head_data):
        payslip = make_payslip(
            employee=self.employee,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        payslip.pay_head_data = pay_head_data
        payslip.save()
        return payslip

    def test_no_previous_payslip(self):
        self.assertIsNone(
            get_previous_period_leave_adjustment(self.employee, self.current_start)
        )

    @patch(COMPUTE)
    def test_payslip_from_before_the_upgrade_is_skipped(self, compute):
        # v1 payslips carry no attendance_summary_used key; recomputing them
        # with v2's calculation would charge the difference as unpaid leave.
        self._previous_payslip(loss_of_pay=0, unpaid_days=0)
        self.assertIsNone(
            get_previous_period_leave_adjustment(self.employee, self.current_start)
        )
        compute.assert_not_called()

    @patch(COMPUTE, return_value={"loss_of_pay": 1500.0, "unpaid_days": 1.5})
    def test_late_unpaid_leave_is_charged(self, compute):
        self._previous_payslip(
            loss_of_pay=500.0, unpaid_days=0.5, attendance_summary_used=False
        )
        adjustment = get_previous_period_leave_adjustment(
            self.employee, self.current_start
        )
        self.assertEqual(
            adjustment,
            {"title": "Leave Adjustment (Jan 2024) - 1.0 day(s)", "amount": 1000.0},
        )
        self.assertIsNone(compute.call_args.kwargs["month_summary"])

    @patch(COMPUTE, return_value={"loss_of_pay": 500.0, "unpaid_days": 0.5})
    def test_no_change_means_no_adjustment(self, compute):
        self._previous_payslip(
            loss_of_pay=500.0, unpaid_days=0.5, attendance_summary_used=False
        )
        self.assertIsNone(
            get_previous_period_leave_adjustment(self.employee, self.current_start)
        )

    @patch(COMPUTE, return_value={"loss_of_pay": 0.0, "unpaid_days": 0})
    def test_refund_is_not_issued(self, compute):
        self._previous_payslip(
            loss_of_pay=500.0, unpaid_days=0.5, attendance_summary_used=False
        )
        self.assertIsNone(
            get_previous_period_leave_adjustment(self.employee, self.current_start)
        )

    @patch(COMPUTE, return_value={"loss_of_pay": 800.0, "unpaid_days": 2})
    def test_bulk_payslip_is_recomputed_with_the_attendance_summary(self, compute):
        self._previous_payslip(
            loss_of_pay=400.0, unpaid_days=1, attendance_summary_used=True
        )
        row = {"employee": self.employee, "absent": 1, "unpaid_leave": 1}
        with patch(
            "attendance.views.summary.build_monthly_summary",
            return_value=([row], 22, {}),
        ) as summary:
            adjustment = get_previous_period_leave_adjustment(
                self.employee, self.current_start
            )
        summary.assert_called_once()
        self.assertIs(compute.call_args.kwargs["month_summary"], row)
        self.assertEqual(adjustment["amount"], 400.0)


class ApplyPreviousPeriodLeaveAdjustmentTests(TestCase):
    def _payslip_dict(self):
        pay_data = {"post_tax_deductions": [], "total_deductions": 100.0, "net_pay": 900.0}
        return {
            "json_data": json.dumps(pay_data),
            "post_tax_deductions": [],
            "total_deductions": 100.0,
            "net_pay": 900.0,
        }

    @patch(
        "payroll.methods.methods.get_previous_period_leave_adjustment",
        return_value={"title": "Leave Adjustment (Jan 2024)", "amount": 50.0},
    )
    def test_adjustment_is_added_as_a_post_tax_deduction(self, _adjustment):
        payslip = self._payslip_dict()
        apply_previous_period_leave_adjustment(None, date(2024, 2, 1), payslip)

        stored = json.loads(payslip["json_data"])
        self.assertEqual(
            stored["post_tax_deductions"],
            [{"title": "Leave Adjustment (Jan 2024)", "amount": 50.0}],
        )
        self.assertEqual(stored["total_deductions"], 150.0)
        self.assertEqual(stored["net_pay"], 850.0)
        self.assertEqual(payslip["total_deductions"], 150.0)
        self.assertEqual(payslip["net_pay"], 850.0)
        self.assertEqual(len(payslip["post_tax_deductions"]), 1)

    @patch(
        "payroll.methods.methods.get_previous_period_leave_adjustment",
        return_value=None,
    )
    def test_nothing_changes_without_an_adjustment(self, _adjustment):
        payslip = self._payslip_dict()
        before = dict(payslip)
        apply_previous_period_leave_adjustment(None, date(2024, 2, 1), payslip)
        self.assertEqual(payslip, before)
