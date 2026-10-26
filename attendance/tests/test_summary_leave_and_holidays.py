"""
build_monthly_summary: how leave, holidays and weekly offs are counted.

- A holiday or weekly off inside a leave counts as holiday / weekly off.
- Leave types are classified by payment_type (legacy payment as fallback).
- Holidays specific to other employees don't apply to this one.
"""

from datetime import date

from django.test import TestCase

from attendance.views.summary import build_monthly_summary
from base.models import CompanyLeaves, Holidays
from employee.models import Employee
from horilla.testkit import make_company, make_employee
from horilla.testkit.factories import make_leave_request, make_leave_type

START, END = date(2026, 10, 1), date(2026, 10, 31)


class SummaryLeaveAndHolidayTests(TestCase):
    def setUp(self):
        company = make_company("Summary Co")
        self.employee = make_employee(company=company, email="sum@test.horilla")
        self.other = make_employee(company=company, email="other@test.horilla")
        # Saturday and Sunday
        CompanyLeaves.objects.create(based_on_week=None, based_on_week_day="5")
        CompanyLeaves.objects.create(based_on_week=None, based_on_week_day="6")

    def _row(self):
        rows, _total, _totals = build_monthly_summary(
            START, END, Employee.objects.filter(pk=self.employee.pk)
        )
        return rows[0]

    def _leave_type(self, name, payment="unpaid", payment_type=None):
        leave_type = make_leave_type(name)
        leave_type.payment = payment
        leave_type.payment_type = payment_type
        leave_type.save()
        return leave_type

    def _leave(self, leave_type, start, end):
        make_leave_request(
            employee=self.employee, leave_type=leave_type, start_date=start, end_date=end
        )

    def test_holiday_and_weekly_off_inside_unpaid_leave(self):
        # Fri Oct 9 to Mon Oct 12, Monday a holiday: 1 unpaid, 2 week off, 1 holiday.
        Holidays.objects.create(
            name="Holiday", start_date=date(2026, 10, 12), end_date=date(2026, 10, 12)
        )
        self._leave(self._leave_type("Unpaid"), date(2026, 10, 9), date(2026, 10, 12))
        row = self._row()
        self.assertEqual(row["unpaid_leave"], 1)
        self.assertEqual(row["holiday"], 1)
        self.assertEqual(row["week_off"], 9)

    def test_leave_type_paid_by_payment_type(self):
        # payment_type "paid" with the legacy field left at its "unpaid" default
        paid = self._leave_type("Annual", payment="unpaid", payment_type="paid")
        self._leave(paid, date(2026, 10, 14), date(2026, 10, 14))
        row = self._row()
        self.assertEqual(row["paid_leave"], 1)
        self.assertEqual(row["unpaid_leave"], 0)

    def test_partial_pay_leave_is_not_counted_as_unpaid(self):
        half = self._leave_type("Half paid", payment_type="custom")
        half.payment_percentage = 50
        half.save()
        self._leave(half, date(2026, 10, 14), date(2026, 10, 14))
        row = self._row()
        self.assertEqual(row["unpaid_leave"], 0)
        self.assertEqual(row["paid_leave"], 1)

    def test_holiday_for_another_employee_is_a_working_day(self):
        holiday = Holidays.objects.create(
            name="Their holiday",
            start_date=date(2026, 10, 14),
            end_date=date(2026, 10, 14),
            is_specific=True,
        )
        holiday.employees.add(self.other)
        row = self._row()
        self.assertEqual(row["holiday"], 0)
        # 22 working days in October 2026, none attended
        self.assertEqual(row["absent"], 22)

    def test_own_specific_holiday_counts(self):
        holiday = Holidays.objects.create(
            name="My holiday",
            start_date=date(2026, 10, 14),
            end_date=date(2026, 10, 14),
            is_specific=True,
        )
        holiday.employees.add(self.employee)
        row = self._row()
        self.assertEqual(row["holiday"], 1)
        self.assertEqual(row["absent"], 21)
