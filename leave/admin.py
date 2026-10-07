"""
Module for registering LeaveType, LeaveRequest, AvailableLeave
models with the Django admin site.
"""

from django.apps import apps
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from leave.forms import LeaveTypeAdminForm

from .models import (
    AvailableLeave,
    LeaveAllocationRequest,
    LeaveallocationrequestComment,
    LeaveGeneralSetting,
    LeaveRequest,
    LeaverequestComment,
    LeaveRequestConditionApproval,
    LeaveType,
    ProRataLeaveAllocation,
    RestrictLeave,
)


class LeaveTypeAdmin(admin.ModelAdmin):
    form = LeaveTypeAdminForm


# Register your models here.
admin.site.register(LeaveType, LeaveTypeAdmin)
admin.site.register(LeaveRequest)
admin.site.register(AvailableLeave)
admin.site.register(LeaveAllocationRequest, SimpleHistoryAdmin)
admin.site.register(LeaveRequestConditionApproval)
admin.site.register(LeaverequestComment)
admin.site.register(LeaveallocationrequestComment)
admin.site.register(RestrictLeave)
admin.site.register(LeaveGeneralSetting)


@admin.register(ProRataLeaveAllocation)
class ProRataLeaveAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "leave_type_id",
        "cycle_year",
        "effective_date",
        "allocated_days",
        "source",
        "created_at",
    )
    list_filter = ("cycle_year", "source", "leave_type_id")
    search_fields = (
        "employee_id__employee_first_name",
        "employee_id__employee_last_name",
    )
if apps.is_installed("attendance"):
    from .models import CompensatoryLeaveRequest

    admin.site.register(CompensatoryLeaveRequest)
