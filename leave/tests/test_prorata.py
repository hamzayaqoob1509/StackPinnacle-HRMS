"""Pro-rated leave allocation on probation / contract confirmation."""

from datetime import date

from django.test import TestCase

from horilla.testkit import make_company, make_employee
from leave.models import AvailableLeave, LeaveType, ProRataLeaveAllocation
from leave.prorata import calculate_prorata_days, grant_prorata_leaves


def yearly_type(**overrides):
    fields = dict(
        name="Annual",
        total_days=12,
        is_prorated=True,
        reset=True,
        reset_based="yearly",
        reset_month="1",
        reset_day="1",
    )
    fields.update(overrides)
    return LeaveType(**fields)


class CalculateProrataDaysTests(TestCase):
    def test_counts_full_months_after_the_effective_month(self):
        # Confirmed mid-March: April to December is 9 months of 12.
        self.assertEqual(
            calculate_prorata_days(yearly_type(), date(2024, 3, 15), date(2024, 6, 1)),
            (9, 2025),
        )

    def test_rounds_to_whole_days(self):
        # 20 / 12 * 9 = 15.
        leave_type = yearly_type(total_days=20)
        self.assertEqual(
            calculate_prorata_days(leave_type, date(2024, 3, 15), date(2024, 6, 1)),
            (15, 2025),
        )

    def test_type_not_marked_prorated(self):
        self.assertIsNone(
            calculate_prorata_days(
                yearly_type(is_prorated=False), date(2024, 3, 15), date(2024, 6, 1)
            )
        )

    def test_type_without_yearly_reset(self):
        self.assertIsNone(
            calculate_prorata_days(
                yearly_type(reset_based="monthly"), date(2024, 3, 15), date(2024, 6, 1)
            )
        )

    def test_confirmation_in_an_earlier_cycle_is_left_to_the_annual_reset(self):
        self.assertIsNone(
            calculate_prorata_days(yearly_type(), date(2023, 12, 20), date(2024, 6, 1))
        )

    def test_future_confirmation(self):
        self.assertIsNone(
            calculate_prorata_days(yearly_type(), date(2024, 7, 1), date(2024, 6, 1))
        )


class GrantProrataLeavesTests(TestCase):
    def setUp(self):
        company = make_company("Prorata Co")
        self.employee = make_employee(company=company, email="prorata@test.horilla")
        work_info = self.employee.employee_work_info
        work_info.probation_end_date = date(2024, 3, 15)
        work_info.save()
        self.leave_type = yearly_type()
        self.leave_type.save()
        self.today = date(2024, 6, 1)

    def test_grants_once_per_cycle(self):
        granted = grant_prorata_leaves(
            self.employee, today=self.today, notify_recipients=False
        )
        self.assertEqual([(lt.pk, days) for lt, days in granted], [(self.leave_type.pk, 9)])
        available = AvailableLeave.objects.get(
            employee_id=self.employee, leave_type_id=self.leave_type
        )
        self.assertEqual(available.available_days, 9)
        ledger = ProRataLeaveAllocation.objects.get(employee_id=self.employee)
        self.assertEqual((ledger.cycle_year, ledger.source), (2025, "probation"))

        again = grant_prorata_leaves(
            self.employee, today=self.today, notify_recipients=False
        )
        self.assertEqual(again, [])
        self.assertEqual(ProRataLeaveAllocation.objects.count(), 1)

    def test_existing_balance_is_never_overwritten(self):
        AvailableLeave.objects.create(
            employee_id=self.employee, leave_type_id=self.leave_type, available_days=4
        )
        granted = grant_prorata_leaves(
            self.employee, today=self.today, notify_recipients=False
        )
        self.assertEqual(granted, [])
        self.assertEqual(
            AvailableLeave.objects.get(
                employee_id=self.employee, leave_type_id=self.leave_type
            ).available_days,
            4,
        )
