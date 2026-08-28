"""
employee_handbook/admin.py
"""

from django.contrib import admin

from employee_handbook.models import HandbookDocument


@admin.register(HandbookDocument)
class HandbookDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "order", "is_published", "created_at")
    list_filter = ("category", "is_published")
    search_fields = ("title", "body")
