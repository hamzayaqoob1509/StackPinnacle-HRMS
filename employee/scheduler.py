import datetime
import logging
import sys
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from django.urls import reverse

from notifications.signals import notify

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
    This scheduled task to trigger the Disciplinary action and take the suspens
    """
    from base.models import EmployeeShiftSchedule
    from employee.models import DisciplinaryAction
    from employee.policies import employee_account_block_unblock

    dis_action = DisciplinaryAction.objects.all()
    for dis in dis_action:

        if dis.action.block_option:
            if dis.action.action_type == "suspension":
                if dis.days:
                    day = dis.days
                    end_date = dis.start_date + timedelta(days=day)
                    if (
                        datetime.date.today() >= dis.start_date
                        or datetime.date.today() >= end_date
                    ):
                        if datetime.date.today() >= dis.start_date:
                            r = False
                        if datetime.date.today() >= end_date:
                            r = True

                        employees = dis.employee_id.all()
                        for emp in employees:
                            employee_account_block_unblock(emp_id=emp.id, result=r)

                if dis.hours:
                    hour_str = dis.hours + ":00"
                    if hour_str > "00:00:00":

                        # Checking the date of action date.
                        if datetime.date.today() >= dis.start_date:

                            employees = dis.employee_id.all()
                            for emp in employees:

                                # Taking the shift of employee for taking the work start time
                                shift = emp.employee_work_info.shift_id
                                shift_detail = EmployeeShiftSchedule.objects.filter(
                                    shift_id=shift
                                )
                                for shi in shift_detail:
                                    today = datetime.datetime.today()
                                    day_of_week = today.weekday()

                                    # List of weekday names
                                    weekday_names = [
                                        "monday",
                                        "tuesday",
                                        "wednesday",
                                        "thursday",
                                        "friday",
                                        "saturday",
                                        "sunday",
                                    ]
                                    if weekday_names[day_of_week] == shi.day.day:

                                        st_time = shi.start_time

                                        hour_time = datetime.datetime.strptime(
                                            hour_str, "%H:%M:%S"
                                        ).time()

                                        time1 = st_time
                                        time2 = hour_time

                                        # Convert them to datetime objects
                                        datetime1 = datetime.datetime.combine(
                                            datetime.date.today(), time1
                                        )
                                        datetime2 = datetime.datetime.combine(
                                            datetime.date.today(), time2
                                        )

                                        # Add the datetime objects
                                        result_datetime = (
                                            datetime1
                                            + datetime.timedelta(
                                                hours=datetime2.hour,
                                                minutes=datetime2.minute,
                                                seconds=datetime2.second,
                                            )
                                        )

                                        # Extract the time component from the result
                                        result_time = result_datetime.time()

                                        # Get the current time
                                        current_time = datetime.datetime.now().time()

                                        # Check if the current time matches st_time
                                        if current_time >= st_time:
                                            r = False
                                        if current_time >= result_time:
                                            r = True

                                    employee_account_block_unblock(
                                        emp_id=emp.id, result=r
                                    )

            if dis.action.action_type == "dismissal":
                if datetime.date.today() >= dis.start_date:
                    if datetime.date.today() >= dis.start_date:
                        r = False
                    employees = dis.employee_id.all()
                    for emp in employees:
                        employee_account_block_unblock(emp_id=emp.id, result=r)

    return


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
    from django.contrib.auth.models import User

    return (
        User.objects.filter(username="Horilla Bot").first()
        or User.objects.filter(is_superuser=True, is_active=True).first()
    )


def _probation_hr_recipients():
    from django.contrib.auth.models import Permission, User
    from django.db.models import Q

    try:
        perm = Permission.objects.get(
            content_type__app_label="employee", codename="change_employee"
        )
    except Permission.DoesNotExist:
        return User.objects.filter(is_superuser=True, is_active=True)
    return User.objects.filter(
        Q(is_superuser=True)
        | Q(user_permissions=perm)
        | Q(groups__permissions=perm),
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
                    verb_ar=message,
                    verb_de=message,
                    verb_es=message,
                    verb_fr=message,
                    icon="ribbon-outline",
                    redirect=reverse(
                        "employee-view-individual", args=[employee.id]
                    ),
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


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler()
    scheduler.add_job(update_experience, "interval", hours=4)
    scheduler.add_job(block_unblock_disciplinary, "interval", seconds=25)
    scheduler.add_job(
        notify_probation_milestones,
        "cron",
        hour=8,
        minute=0,
        misfire_grace_time=3600 * 12,
        id="notify_probation_milestones",
        replace_existing=True,
    )
    scheduler.add_job(
        notify_probation_milestones,
        "interval",
        hours=6,
        id="notify_probation_milestones_interval",
        replace_existing=True,
    )
    scheduler.start()
