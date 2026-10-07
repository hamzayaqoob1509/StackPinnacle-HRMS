"""
default_currency context processor under a company scope that sees no row.

PayrollSettings.objects is company-scoped. A user on "All my companies" whose
allowed/assigned company list is empty gets qs.none() for every scoped query,
so the old processor created a row it then couldn't read and crashed with
AttributeError on every page -- /ess/ 500'd in production after the upgrade.
"""

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from horilla.horilla_middlewares import _thread_locals, set_selected_company
from horilla.testkit import make_company
from payroll.context_processors import default_currency
from payroll.models.tax_models import PayrollSettings


class DefaultCurrencyScopeTests(TestCase):
    def setUp(self):
        self.company = make_company("Payroll Co")
        PayrollSettings.objects.entire().delete()
        self.request = SimpleNamespace(
            session={},
            user=AnonymousUser(),
            all_my_company_ids=[],
            selected_company_instance=None,
        )
        _thread_locals.request = self.request
        set_selected_company("all")

    def tearDown(self):
        set_selected_company(None)
        _thread_locals.request = None

    def test_existing_row_outside_scope_is_used(self):
        PayrollSettings.objects.entire().create(
            company_id=self.company, currency_symbol="Rs", position="prefix"
        )
        self.assertIsNone(PayrollSettings.objects.first())

        context = default_currency(self.request)

        self.assertEqual(context, {"currency": "Rs", "position": "prefix"})
        self.assertEqual(PayrollSettings.objects.entire().count(), 1)

    def test_empty_table_creates_one_row_and_stops(self):
        for _ in range(3):
            context = default_currency(self.request)

        self.assertEqual(context["currency"], "$")
        self.assertEqual(PayrollSettings.objects.entire().count(), 1)
