"""
employee_handbook/forms.py
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from employee_handbook.models import HandbookDocument


class HandbookDocumentForm(forms.ModelForm):
    """
    Create / edit form for a handbook entry. ``body`` is rendered with the
    shared Summernote rich-text editor (auto-initialised via ``data-summernote``).
    """

    class Meta:
        model = HandbookDocument
        fields = ["title", "category", "body", "document", "order", "is_published"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "oh-input w-100"}),
            "category": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
            "body": forms.Textarea(
                attrs={"data-summernote": "", "style": "display:none;"}
            ),
            "document": forms.ClearableFileInput(
                attrs={"class": "oh-input w-100", "accept": "application/pdf"}
            ),
            "order": forms.NumberInput(attrs={"class": "oh-input w-100", "min": 0}),
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
