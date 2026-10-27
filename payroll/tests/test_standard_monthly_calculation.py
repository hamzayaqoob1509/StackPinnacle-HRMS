"""
Standard monthly salary calculation.

Daily rate = salary x 12 / 365. Weekly offs and company holidays are paid
days. Unpaid days are approved unpaid leave, plus absent days when the
attendance summary is used. A full month with no unpaid days pays exactly
the salary.

The salary below is 36,500 so the daily rate is exactly 1,200.
"""

from datetime import date

from django.test import TestCase

from base.models import CompanyLeaves, Holidays
from horilla.testkit import make_company, make_employee
from horilla.testkit.factories import make_leave_request, make_leave_type
from payroll.methods.methods import compute_salary_on_period, monthly_daily_rate
from payroll.models.models import Contract

SALARY = 36500.0
RATE = 1200.0
OCT_START, OCT_END = date(2026, 10, 1), date(2026, 10, 31)


class StandardMonthlyCalculationTests(TestCase):
    def setUp(self):
        company = make_company("Standard Co")
        self.employee = make_employee(company=company, email="std@test.horilla")
        Contract.objects.filter(employee_id=self.employee).delete()
        self.contract = Contract.objects.create(
            contract_name="Monthly",
            employee_id=self.employee,
            contract_start_date=date(2026, 1, 1),
            wage_type="monthly",
            wage=SALARY,
            contract_status="active",
            deduct_leave_from_basic_pay=True,
            calculate_daily_leave_amount=True,
            deduction_for_one_leave_amount=0,
        )
        self.unpaid = make_leave_type("Unpaid")
        self.unpaid.payment = "unpaid"
        self.unpaid.save()

    def _compute(self, start=OCT_START, end=OCT_END, **kw):
        return compute_salary_on_period(self.employee, start, end, **kw)

    def _holiday(self, day):
        return Holidays.objects.create(name="Holiday", start_date=day, end_date=day)

    def _weekly_offs(self):
        # Saturday and Sunday
        CompanyLeaves.objects.create(based_on_week=None, based_on_week_day="5")
        CompanyLeaves.objects.create(based_on_week=None, based_on_week_day="6")

    def _unpaid_leave(self, start, end=None, leave_type=None, **kw):
        leave = make_leave_request(
            employee=self.employee,
            leave_type=leave_type or self.unpaid,
            start_date=start,
            end_date=end or start,
        )
        if kw:
            type(leave).objects.filter(pk=leave.pk).update(**kw)
        return leave

    def test_daily_rate_is_salary_times_12_over_365(self):
        self.assertAlmostEqual(monthly_daily_rate(SALARY), RATE)
        self.assertEqual(monthly_daily_rate(None), 0)

    def test_holiday_is_a_paid_day(self):
        # Payslip 539: October, holiday Oct 12, one unpaid leave on Oct 14.
        self._holiday(date(2026, 10, 12))
        self._unpaid_leave(date(2026, 10, 14))
        data = self._compute()
        self.assertEqual(data["paid_days"], 30)
        self.assertEqual(data["unpaid_days"], 1)
        self.assertAlmostEqual(data["loss_of_pay"], RATE)
        self.assertAlmostEqual(data["basic_pay"], SALARY - RATE)
        self.assertAlmostEqual(data["daily_rate"], RATE)

    def test_weekly_offs_do_not_change_pay(self):
        self._holiday(date(2026, 10, 12))
        self._unpaid_leave(date(2026, 10, 14))
        without = self._compute()
        self._weekly_offs()
        with_offs = self._compute()
        for key in ("paid_days", "unpaid_days", "loss_of_pay", "basic_pay"):
            self.assertAlmostEqual(with_offs[key], without[key], msg=key)

    def test_full_month_without_unpaid_days_pays_the_salary(self):
        self._weekly_offs()
        for start, end in (
            (date(2026, 2, 1), date(2026, 2, 28)),
            (date(2026, 4, 1), date(2026, 4, 30)),
            (date(2026, 10, 1), date(2026, 10, 31)),
        ):
            data = self._compute(start, end)
            self.assertAlmostEqual(data["basic_pay"], SALARY, msg=str(start))
            self.assertEqual(data["paid_days"], (end - start).days + 1)
            self.assertEqual(data["loss_of_pay"], 0)

    def test_contract_starting_mid_month(self):
        # Oct 20-31: 12 days in the period, 19 days of October outside it.
        data = self._compute(date(2026, 10, 20), OCT_END)
        self.assertEqual(data["paid_days"], 12)
        self.assertAlmostEqual(data["basic_pay"], SALARY - 19 * RATE)

    def test_period_spanning_two_months(self):
        # Sep 16-30 (15 of 30 days) and Oct 1-15 (15 of 31 days).
        data = self._compute(date(2026, 9, 16), date(2026, 10, 15))
        self.assertEqual(data["paid_days"], 30)
        expected = (SALARY - 15 * RATE) + (SALARY - 16 * RATE)
        self.assertAlmostEqual(data["basic_pay"], expected)

    def test_basic_pay_never_goes_below_zero(self):
        self._unpaid_leave(OCT_START, OCT_END)
        data = self._compute()
        self.assertEqual(data["unpaid_days"], 31)
        self.assertEqual(data["paid_days"], 0)
        self.assertAlmostEqual(data["basic_pay"], 0)
        self.assertAlmostEqual(data["loss_of_pay"], SALARY)

    def test_holiday_and_weekly_off_inside_unpaid_leave_are_paid(self):
        # Fri Oct 9 to Mon Oct 12, Monday is a holiday: only Friday is unpaid.
        self._weekly_offs()
        self._holiday(date(2026, 10, 12))
        self._unpaid_leave(date(2026, 10, 9), date(2026, 10, 12))
        data = self._compute()
        self.assertEqual(data["unpaid_days"], 1)
        self.assertEqual(data["paid_days"], 30)

    def test_half_day_unpaid_leave(self):
        self._unpaid_leave(date(2026, 10, 14), start_date_breakdown="first_half")
        data = self._compute()
        self.assertEqual(data["unpaid_days"], 0.5)
        self.assertAlmostEqual(data["loss_of_pay"], RATE / 2)

    def test_partial_pay_leave_deducts_its_unpaid_share(self):
        half_paid = make_leave_type("Half paid")
        half_paid.payment_type = "custom"
        half_paid.payment_percentage = 50
        half_paid.save()
        self._unpaid_leave(date(2026, 10, 14), leave_type=half_paid)
        data = self._compute()
        self.assertEqual(data["unpaid_days"], 0)
        self.assertEqual(data["paid_days"], 31)
        self.assertAlmostEqual(data["custom_leave_deduction"], RATE / 2)
        self.assertAlmostEqual(data["basic_pay"], SALARY - RATE / 2)

    def test_fixed_amount_per_leave(self):
        self.contract.calculate_daily_leave_amount = False
        self.contract.deduction_for_one_leave_amount = 500
        self.contract.save()
        self._unpaid_leave(date(2026, 10, 14))
        data = self._compute()
        self.assertAlmostEqual(data["loss_of_pay"], 500)
        self.assertAlmostEqual(data["daily_rate"], 500)

    def test_loss_of_pay_kept_apart_from_basic_pay(self):
        self.contract.deduct_leave_from_basic_pay = False
        self.contract.save()
        self._unpaid_leave(date(2026, 10, 14))
        data = self._compute()
        self.assertAlmostEqual(data["basic_pay"], SALARY)
        self.assertAlmostEqual(data["loss_of_pay"], RATE)

    def test_attendance_summary_deducts_absent_days(self):
        summary = {
            "present": 20,
            "paid_leave": 0,
            "unpaid_leave": 1,
            "absent": 2,
            "week_off": 8,
            "holiday": 0,
            "total_working": 23,
            "unresolved_conflicts": 0,
        }
        data = self._compute(month_summary=summary)
        self.assertEqual(data["unpaid_days"], 3)
        self.assertEqual(data["paid_days"], 28)
        self.assertAlmostEqual(data["basic_pay"], SALARY - 3 * RATE)
        self.assertEqual(data["week_off"], 8)

    def test_unresolved_conflicts_make_the_period_unpaid(self):
        summary = {"present": 30, "unpaid_leave": 0, "absent": 0, "unresolved_conflicts": 1}
        data = self._compute(month_summary=summary)
        self.assertEqual(data["unpaid_days"], 31)
        self.assertAlmostEqual(data["basic_pay"], 0)
