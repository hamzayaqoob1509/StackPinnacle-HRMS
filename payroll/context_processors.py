"""
context_processor.py

This module is used to register context processor`
"""

from employee.models import Employee
from payroll.models import tax_models as models
from payroll.models.models import Deduction


def get_payroll_settings(request=None):
    """
    Return the PayrollSettings row for the current company scope, never None.

    PayrollSettings.objects is company-scoped, so a user whose scope matches no
    row (e.g. "All my companies" with no assignments) sees an empty queryset.
    Creating a row there doesn't help: the new row is just as invisible, so the
    caller still gets None -- and a fresh row is saved on every request. Fall
    back to the unscoped table instead; currency display is not tenant data.
    """
    settings = (
        models.PayrollSettings.objects.first()
        or models.PayrollSettings.objects.entire().order_by("pk").first()
    )
    if settings is None:
        settings = models.PayrollSettings.objects.create(
            currency_symbol="$",
            company_id=getattr(request, "selected_company_instance", None),
        )
    return settings


def default_currency(request):
    """
    This method will return the currency
    """
    settings = get_payroll_settings(request)
    return {
        "currency": request.session.get("currency", settings.currency_symbol),
        "position": request.session.get("position", settings.position),
    }


def host(request):
    """
    This method will return the host
    """
    protocol = "https" if request.is_secure() else "http"
    return {"host": request.get_host(), "protocol": protocol}


def get_deductions(request):
    """
    This method used to return the deduction
    """
    deductions = Deduction.objects.filter(
        only_show_under_employee=False, employer_rate__gt=0
    )
    return {"get_deductions": deductions}


def get_active_employees(request):
    """
    This method used to return the deduction
    """
    employees = Employee.objects.filter(
        is_active=True, contract_set__isnull=False, payslip__isnull=False
    ).distinct()
    return {"get_active_employees": employees}
