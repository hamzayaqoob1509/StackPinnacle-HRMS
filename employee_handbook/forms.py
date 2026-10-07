"""
employee_handbook/forms.py
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from employee_handbook.models import HandbookDocument


class HandbookDocumentForm(ModelForm):
    """
    Create / edit form for a handbook entry. ``body`` is rendered with the
    shared Summernote rich-text editor (auto-initialised via ``data-summernote``).
    """

    cols = {"title": 12, "category": 12, "body": 12, "document": 12}

    class Meta:
        model = HandbookDocument
        fields = ["title", "category", "body", "document", "order", "is_published"]
        widgets = {
            "body": forms.Textarea(
                attrs={"data-summernote": "", "style": "display:none;"}
            ),
            "document": forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        body = cleaned_data.get("body")
        document = cleaned_data.get("document")
        if not body and not document:
            raise forms.ValidationError(
                _("Add written content or attach a PDF document (or both).")
            )
        return cleaned_data
