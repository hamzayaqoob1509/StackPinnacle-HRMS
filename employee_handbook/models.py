"""
employee_handbook/models.py

Models for the Employee Handbook: a company-wide, read-only reference of
policies, guidelines and procedures. Each entry can carry written content, an
attached PDF, or both.
"""

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from horilla.models import HorillaModel, upload_path


class HandbookDocument(HorillaModel):
    """
    A single Employee Handbook entry.
    """

    CATEGORY_CHOICES = [
        ("policy", _("Policy")),
        ("guideline", _("Guideline")),
        ("procedure", _("Procedure")),
        ("code_of_conduct", _("Code of Conduct")),
        ("benefits", _("Benefits & Perks")),
        ("other", _("Other")),
    ]

    title = models.CharField(max_length=150, verbose_name=_("Title"))
    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        default="policy",
        verbose_name=_("Category"),
    )
    body = models.TextField(blank=True, verbose_name=_("Content"))
    document = models.FileField(
        upload_to=upload_path,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf"])],
        verbose_name=_("PDF Document"),
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Display Order"),
        help_text=_("Lower numbers are listed first."),
    )
    is_published = models.BooleanField(
        default=True,
        verbose_name=_("Published"),
        help_text=_("Unpublished entries are visible only to handbook editors."),
    )

    class Meta:
        verbose_name = _("Handbook Document")
        verbose_name_plural = _("Handbook Documents")
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if not self.body and not self.document:
            raise ValidationError(
                _("Add written content or attach a PDF document (or both).")
            )
