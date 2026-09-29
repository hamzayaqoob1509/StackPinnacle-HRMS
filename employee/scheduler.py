import logging
from datetime import date, datetime, time, timedelta

from django.urls import reverse

from horilla.scheduling import register_job

logger = logging.getLogger(__name__)


def update_experience():
    from employee.models import EmployeeWorkInformation

    """
    This scheduled task to trigger the experience calculator
    to update the employee work experience
    """
    queryset = EmployeeWorkInformation.objects.filter(employee_id__is_active=True)
    for instance in queryset:
        instance.experience_calculator()
    return


def block_unblock_disciplinary():
    """
    Scheduled task to apply disciplinary actions and block/unblock employee accounts.
    """
    from base.models import EmployeeShiftSchedule
    from employee.models import DisciplinaryAction
    from horilla_auth.models import HorillaUser

    today = date.today()
    now = datetime.now().time()

    dis_actions = DisciplinaryAction.objects.select_related("action").prefetch_related(
        "employee_id"
    )

    for dis in dis_actions:
        if not dis.action.block_option:
            continue

        employees = dis.employee_id.exclude(employee_user_id__isnull=True)
        user_ids = list(employees.values_list("employee_user_id", flat=True))
        if not user_ids:
            continue

        if dis.action.action_type == "suspension":
            active = None

            if dis.days:
                start_date = dis.start_date
                end_date = start_date + timedelta(days=dis.days)
                if today >= end_date:
                    active = True
                elif today >= start_date:
                    active = False

            if dis.hours:
                if today != dis.start_date:
                    continue
                hour_str = dis.hours + ":00"
                hour_time = datetime.strptime(hour_str, "%H:%M:%S").time()

                for emp in employees:
                    if not emp.employee_work_info:
                        continue

                    shift = emp.employee_work_info.shift_id
                    shift_today = EmployeeShiftSchedule.objects.filter(
                        shift_id=shift, day__day=datetime.today().strftime("%A").lower()
                    ).first()
                    if not shift_today:
                        continue

                    st_time = shift_today.start_time
                    suspension_end_datetime = datetime.combine(
                        today, st_time
                    ) + timedelta(
                        hours=hour_time.hour,
                        minutes=hour_time.minute,
                        seconds=hour_time.second,
                    )
                    suspension_end_time = suspension_end_datetime.time()

                    if now >= suspension_end_time:
                        active = True
                    elif now >= st_time:
                        active = False

                    user = emp.employee_user_id
                    if user:
                        user.is_active = active
                        user.save()

            if dis.days and active is not None:
                HorillaUser.objects.filter(id__in=user_ids).update(is_active=active)

        elif dis.action.action_type == "dismissal":
            if today >= dis.start_date:
                active = False
                HorillaUser.objects.filter(id__in=user_ids).update(is_active=active)



# --- Probation / internship completion notifications -------------------------
#
# Milestones fire at 10 days before, 3 days before, on the end date, and once at
# 3 days overdue. Each (employee, end date, milestone) is recorded in
# ``ProbationNotification`` so a milestone is never sent twice, and extending
# the probation (a new end date) starts a fresh cycle.

# code, days offset from the end date, message template
PROBATION_MILESTONES = [
    ("t-10", 10, "{name}'s {kind} ends in 10 days (on {end})."),
    ("t-3", 3, "{name}'s {kind} ends in 3 days (on {end})."),
    (
        "t-0",
        0,
        "{name}'s {kind} ends today ({end}). Please confirm, extend or close it.",
    ),
    ("t+3", -3, "{name}'s {kind} ended on {end}. A decision is overdue."),
]

# How long after a milestone's trigger date it may still fire (covers downtime).
PROBATION_CATCH_UP_DAYS = 3


def _probation_actor():
    from horilla_auth.models import HorillaUser

    return (
        HorillaUser.objects.filter(username="Horilla Bot").first()
        or HorillaUser.objects.filter(is_superuser=True, is_active=True).first()
    )


def _probation_hr_recipients():
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    from horilla_auth.models import HorillaUser

    try:
        perm = Permission.objects.get(
            content_type__app_label="employee", codename="change_employee"
        )
    except Permission.DoesNotExist:
        return HorillaUser.objects.filter(is_superuser=True, is_active=True)
    return HorillaUser.objects.filter(
        Q(is_superuser=True) | Q(user_permissions=perm) | Q(groups__permissions=perm),
        is_active=True,
    ).distinct()


def _probation_recipients_for(employee):
    users = set(_probation_hr_recipients())
    manager = employee.get_reporting_manager()
    manager_user = getattr(manager, "employee_user_id", None)
    if manager_user is not None and manager_user.is_active:
        users.add(manager_user)
    return [u for u in users if u is not None]


def notify_probation_milestones():
    """Send any due probation / internship completion notifications."""
    from employee.models import Employee, ProbationNotification
    from notifications.signals import notify

    actor = _probation_actor()
    if actor is None:
        return

    today = date.today()
    employees = Employee.objects.filter(is_active=True).select_related(
        "employee_work_info", "employee_work_info__reporting_manager_id"
    )

    for employee in employees:
        end_date = employee.get_probation_end_date()
        if not end_date:
            continue

        kind = "internship" if employee.is_intern() else "probation period"

        for code, offset, template in PROBATION_MILESTONES:
            trigger_date = end_date - timedelta(days=offset)
            if code == "t+3":
                due = today >= trigger_date
            else:
                due = (
                    trigger_date
                    <= today
                    <= trigger_date + timedelta(days=PROBATION_CATCH_UP_DAYS)
                )
            if not due:
                continue

            already_sent = ProbationNotification.objects.filter(
                employee_id=employee,
                probation_end_date=end_date,
                milestone=code,
            ).exists()
            if already_sent:
                continue

            recipients = _probation_recipients_for(employee)
            if not recipients:
                continue

            message = template.format(
                name=employee.get_full_name(),
                kind=kind,
                end=end_date.strftime("%d %b %Y"),
            )
            try:
                notify.send(
                    actor,
                    recipient=recipients,
                    verb=message,
                    icon="ribbon-outline",
                    redirect=reverse("employee-view-individual", args=[employee.id]),
                )
                ProbationNotification.objects.create(
                    employee_id=employee,
                    probation_end_date=end_date,
                    milestone=code,
                )
            except Exception as error:
                logger.error(
                    "Probation notification failed for employee %s: %s",
                    employee.id,
                    error,
                )


register_job(update_experience, "interval", hours=4)
register_job(block_unblock_disciplinary, "interval", seconds=60)
register_job(
    notify_probation_milestones,
    "cron",
    job_id="employee.scheduler.notify_probation_milestones",
    hour=8,
    minute=0,
    misfire_grace_time=3600 * 12,
)
register_job(
    notify_probation_milestones,
    "interval",
    job_id="employee.scheduler.notify_probation_milestones_interval",
    hours=6,
)
