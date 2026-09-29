"""
leave/prorata.py

Pro-rated leave allocation.

When an employee is confirmed - probation completed or contract activated - any
leave type flagged ``is_prorated`` grants a balance for the *remaining full
calendar months* of the current reset cycle instead of the whole year.

    entitlement / 12  x  months_after_the_effective_month  (rounded to whole days)

The grant runs once per employee / leave type / cycle (see
``ProRataLeaveAllocation``). If the employee already has a balance for the type
(e.g. it was assigned during probation) it is reset to the pro-rated figure,
minus any leave already approved.
"""

import calendar
import contextlib
import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.urls import reverse

from notifications.signals import notify

logger = logging.getLogger(__name__)


def _reset_day(leave_type, year, month):
    raw = leave_type.reset_day or "1"
    if raw == "last day":
        return calendar.monthrange(year, month)[1]
    return int(raw)


def cycle_reset_after(leave_type, reference_date):
    """First yearly reset date strictly after ``reference_date``."""
    reset_month = int(leave_type.reset_month or "1")
    year = reference_date.year
    day = min(
        _reset_day(leave_type, year, reset_month),
        calendar.monthrange(year, reset_month)[1],
    )
    candidate = date(year, reset_month, day)
    if candidate <= reference_date:
        day = min(
            _reset_day(leave_type, year + 1, reset_month),
            calendar.monthrange(year + 1, reset_month)[1],
        )
        candidate = date(year + 1, reset_month, day)
    return candidate


def current_cycle(leave_type, today):
    """``(cycle_start, cycle_end)`` - the reset cycle that contains ``today``."""
    cycle_end = cycle_reset_after(leave_type, today)
    cycle_start = cycle_end - relativedelta(years=1)
    return cycle_start, cycle_end


def calculate_prorata_days(leave_type, effective_date, today=None):
    """
    Pro-rated entitlement for ``leave_type`` for an employee confirmed on
    ``effective_date``.

    Returns ``(days, cycle_year)`` or ``None`` when the leave type is not
    eligible, or the confirmation did not happen inside the current reset cycle
    (employees confirmed in an earlier year are left to the normal annual reset).
    """
    if not (
        leave_type.is_prorated
        and leave_type.reset
        and leave_type.reset_based == "yearly"
        and leave_type.total_days
    ):
        return None

    today = today or date.today()
    cycle_start, cycle_end = current_cycle(leave_type, today)

    # Only pro-rate confirmations that fall inside the current cycle.
    if not (cycle_start <= effective_date <= today):
        return None

    # First day of the month after the effective month.
    first_counted = date(effective_date.year, effective_date.month, 1) + relativedelta(
        months=1
    )
    # First day of the reset month (start of the next cycle).
    cycle_boundary = date(cycle_end.year, cycle_end.month, 1)

    months = (cycle_boundary.year - first_counted.year) * 12 + (
        cycle_boundary.month - first_counted.month
    )
    months = max(0, min(12, months))

    return round(leave_type.total_days / 12 * months), cycle_end.year


def confirmation_effective_date(employee):
    """
    ``(date, source)`` the pro-ration is based on, in priority order:

    1. the probation / internship end date, when one is set;
    2. otherwise the joining date;
    3. otherwise the active contract's start date (fallback for missing data).
    """
    probation_end = employee.get_probation_end_date()
    if probation_end:
        return probation_end, "probation"

    work_info = getattr(employee, "employee_work_info", None)
    if work_info and work_info.date_joining:
        return work_info.date_joining, "joining"

    if apps.is_installed("payroll"):
        contract = (
            employee.contract_set.filter(contract_status="active")
            .order_by("contract_start_date")
            .first()
        )
        if contract and contract.contract_start_date:
            return contract.contract_start_date, "contract"

    return None, None


def grant_prorata_leaves(employee, today=None, notify_recipients=True):
    """
    Allocate pro-rated balances for a single (confirmed) employee.

    Only creates a balance for leave types the employee has *no* allocation for
    yet - an existing balance (assigned manually by HR, or during probation) is
    never overwritten. Safe to call repeatedly; the ledger prevents duplicates.
    """
    from django.db.models import Sum

    from leave.models import (
        AvailableLeave,
        LeaveRequest,
        LeaveType,
        ProRataLeaveAllocation,
    )

    today = today or date.today()
    if not getattr(employee, "is_active", True):
        return

    effective_date, source = confirmation_effective_date(employee)
    if not effective_date or effective_date > today:
        return

    granted = []
    for leave_type in LeaveType.objects.filter(
        is_prorated=True, reset=True, reset_based="yearly"
    ):
        result = calculate_prorata_days(leave_type, effective_date, today)
        if result is None:
            continue
        days, cycle_year = result

        if ProRataLeaveAllocation.objects.filter(
            employee_id=employee, leave_type_id=leave_type, cycle_year=cycle_year
        ).exists():
            continue

        # Respect any balance already on record - HR may have set it on purpose.
        if AvailableLeave.objects.filter(
            employee_id=employee, leave_type_id=leave_type
        ).exists():
            continue

        # Leave approved within the current cycle offsets the grant (relevant
        # only if a balance existed earlier and was removed).
        cycle_start, _cycle_end = current_cycle(leave_type, today)
        taken = (
            LeaveRequest.objects.filter(
                leave_type_id=leave_type,
                employee_id=employee,
                status="approved",
                start_date__gte=cycle_start,
            ).aggregate(total=Sum("requested_days"))["total"]
            or 0
        )

        available = AvailableLeave.objects.create(
            employee_id=employee,
            leave_type_id=leave_type,
            available_days=max(0, days - taken),
            assigned_date=effective_date,
        )

        ProRataLeaveAllocation.objects.create(
            employee_id=employee,
            leave_type_id=leave_type,
            cycle_year=cycle_year,
            effective_date=effective_date,
            allocated_days=available.available_days,
            source=source,
        )
        granted.append((leave_type, available.available_days))

    if granted and notify_recipients:
        _notify(employee, granted, effective_date)

    return granted


def _notify(employee, granted, effective_date):
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    from horilla_auth.models import HorillaUser

    summary = ", ".join(f"{days:g} {lt.name}" for lt, days in granted)
    message = (
        f"Pro-rated leave allocated to {employee.get_full_name()} on confirmation "
        f"({effective_date:%d %b %Y}): {summary}."
    )
    actor = (
        HorillaUser.objects.filter(username="Horilla Bot").first()
        or HorillaUser.objects.filter(is_superuser=True, is_active=True).first()
    )
    if actor is None:
        return

    recipients = set()
    employee_user = getattr(employee, "employee_user_id", None)
    if employee_user is not None:
        recipients.add(employee_user)
    try:
        perm = Permission.objects.get(
            content_type__app_label="leave", codename="add_availableleave"
        )
        recipients.update(
            HorillaUser.objects.filter(
                Q(is_superuser=True)
                | Q(user_permissions=perm)
                | Q(groups__permissions=perm),
                is_active=True,
            )
        )
    except Permission.DoesNotExist:
        pass

    with contextlib.suppress(Exception):
        notify.send(
            actor,
            recipient=[u for u in recipients if u is not None],
            verb=message,
            icon="calendar-outline",
            redirect=reverse("user-request-view"),
        )


def grant_all_prorata_leaves():
    """Scheduled sweep: grant pro-rated leaves for every confirmed employee."""
    from employee.models import Employee

    for employee in Employee.objects.filter(is_active=True).select_related(
        "employee_work_info"
    ):
        try:
            grant_prorata_leaves(employee)
        except Exception as error:
            logger.error(
                "Pro-rata leave grant failed for employee %s: %s",
                getattr(employee, "id", None),
                error,
            )
