"""
employee_handbook/views.py

Employee Handbook views.

Every authenticated user can browse and download handbook entries. Creating,
editing and deleting entries is gated behind the ``employee_handbook``
permissions, which only administrators hold by default.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from base.methods import paginator_qry
from employee_handbook.forms import HandbookDocumentForm
from employee_handbook.models import HandbookDocument
from horilla.decorators import (
    hx_request_required,
    login_required,
    permission_required,
)

EDIT_PERM = "employee_handbook.add_handbookdocument"


def _can_edit(request):
    return request.user.has_perm(EDIT_PERM)


def _visible_documents(request):
    """Return the handbook entries the requesting user is allowed to see."""
    documents = HandbookDocument.objects.filter(is_active=True)
    if not _can_edit(request):
        documents = documents.filter(is_published=True)

    search = request.GET.get("search")
    if search:
        documents = documents.filter(title__icontains=search)

    category = request.GET.get("category")
    if category:
        documents = documents.filter(category=category)

    return documents


@login_required
def handbook_view(request):
    """Main Employee Handbook page."""
    documents = _visible_documents(request)
    return render(
        request,
        "employee_handbook/handbook.html",
        {
            "documents": paginator_qry(documents, request.GET.get("page")),
            "categories": HandbookDocument.CATEGORY_CHOICES,
            "search": request.GET.get("search", ""),
            "selected_category": request.GET.get("category", ""),
            "pd": request.GET.urlencode(),
        },
    )


@login_required
@hx_request_required
def handbook_list(request):
    """HTMX partial: the filtered/paginated list of handbook entries."""
    documents = _visible_documents(request)
    return render(
        request,
        "employee_handbook/handbook_list.html",
        {
            "documents": paginator_qry(documents, request.GET.get("page")),
            "pd": request.GET.urlencode(),
        },
    )


@login_required
@hx_request_required
@permission_required(EDIT_PERM)
def handbook_create(request):
    """Create a new handbook entry, or edit an existing one (``?instance_id=``)."""
    instance_id = request.GET.get("instance_id")
    instance = (
        HandbookDocument.objects.filter(id=instance_id).first()
        if instance_id
        else None
    )
    form = HandbookDocumentForm(instance=instance)
    if request.method == "POST":
        form = HandbookDocumentForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                _("Handbook entry updated.")
                if instance
                else _("Handbook entry added."),
            )
            return render(
                request,
                "employee_handbook/handbook_form.html",
                {"form": HandbookDocumentForm()},
            )
    return render(
        request,
        "employee_handbook/handbook_form.html",
        {"form": form, "instance": instance},
    )


@login_required
@hx_request_required
def handbook_detail(request, pk):
    """HTMX partial: read-only detail of a single handbook entry."""
    document = get_object_or_404(HandbookDocument, pk=pk, is_active=True)
    if not document.is_published and not _can_edit(request):
        raise Http404
    return render(
        request,
        "employee_handbook/handbook_detail.html",
        {"document": document},
    )


@login_required
@permission_required("employee_handbook.delete_handbookdocument")
def handbook_delete(request, pk):
    """Delete a handbook entry."""
    document = HandbookDocument.objects.filter(pk=pk).first()
    if document:
        document.delete()
        messages.success(request, _("Handbook entry deleted."))
    else:
        messages.error(request, _("Handbook entry not found."))
    return redirect("handbook-view")
