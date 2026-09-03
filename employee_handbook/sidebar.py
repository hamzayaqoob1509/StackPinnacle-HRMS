"""
employee_handbook/sidebar.py

Registers the "Employee Handbook" entry in the Horilla left sidebar. It has no
accessibility gate, so every authenticated employee sees it.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as trans

MENU = trans("Employee Handbook")
IMG_SRC = "images/ui/handbook.svg"

SUBMENUS = [
    {
        "menu": trans("Handbook"),
        "redirect": reverse("handbook-view"),
    },
]
