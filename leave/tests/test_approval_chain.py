"""Multiple-approval chains are approved in order, and rejecting never crashes.

Before this, every approve path (web, API, API bulk) let any manager in the
chain act at any time, and the request became approved as soon as the *last*
manager in the chain approved, whether or not the earlier ones had. The API
bulk endpoint ignored the chain completely and approved on the first call.

Rejecting took the rejecter's step with ``approver[0]``, so anyone allowed to
reject without being in the chain (an HR user with the permission, or the
reporting manager) got a server error. The API reject also restored the
balance of the person rejecting instead of the employee's.
"""

import datetime
import json

from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Department, MultipleApprovalCondition, MultipleApprovalManagers
from horilla.testkit import make_company, make_employee, make_user
from leave.models import (
    AvailableLeave,
    LeaveRequest,
    LeaveRequestConditionApproval,
    LeaveType,
)

WEB_APPROVE = "/leave/request-approve/{}/"
WEB_REJECT = "/leave/request-cancel/{}/"
API_APPROVE = "/api/v1/leave/approve/{}/"
API_REJECT = "/api/v1/leave/reject/{}/"
API_BULK = "/api/v1/leave/request-bulk-action/"


class ApprovalChainTests(TestCase):
    def setUp(self):
        company = make_company("Chain Co")
        self.employee_user = make_user("chain_emp", password="secret123")
        self.employee = make_employee(
            company=company,
            email="chain_emp@test.horilla",
            user=self.employee_user,
            phone="3000001",
        )
        self.first_user = make_user("chain_first", password="secret123")
        self.first = make_employee(
            company=company,
            email="chain_first@test.horilla",
            user=self.first_user,
            phone="3000002",
        )
        self.last_user = make_user("chain_last", password="secret123")
        self.last = make_employee(
            company=company,
            email="chain_last@test.horilla",
            user=self.last_user,
            phone="3000003",
        )
        self.hr_user = make_user("chain_hr", password="secret123")
        self.hr = make_employee(
            company=company,
            email="chain_hr@test.horilla",
            user=self.hr_user,
            phone="3000004",
        )
        self.hr_user.user_permissions.add(
            Permission.objects.get(codename="change_leaverequest")
        )
        self.hr_user = type(self.hr_user).objects.get(pk=self.hr_user.pk)

        self.leave_type = LeaveType.objects.create(
            name="Chain Annual", payment="paid", total_days=10, company_id=company
        )
        self.balance, _ = AvailableLeave.objects.get_or_create(
            employee_id=self.employee,
            leave_type_id=self.leave_type,
            defaults={"available_days": 10},
        )
        AvailableLeave.objects.filter(pk=self.balance.pk).update(
            available_days=10, carryforward_days=0
        )
        self.leave_request = LeaveRequest.objects.create(
            employee_id=self.employee,
            leave_type_id=self.leave_type,
            start_date=datetime.date.today() + datetime.timedelta(days=7),
            end_date=datetime.date.today() + datetime.timedelta(days=8),
            description="chain test",
            status="requested",
        )
        # No department condition matches in this fixture; build the chain
        # the way LeaveRequest.save() does.
        LeaveRequestConditionApproval.objects.filter(
            leave_request_id=self.leave_request
        ).delete()
        for sequence, manager in enumerate((self.first, self.last), start=1):
            LeaveRequestConditionApproval.objects.create(
                sequence=sequence,
                leave_request_id=self.leave_request,
                manager_id=manager,
            )
        # In a real setup the chain comes from a condition whose managers are
        # registered here; that registration is what lets them into the
        # approve/reject views at all.
        department = Department.objects.create(department="Chain Dept")
        condition = MultipleApprovalCondition.objects.create(
            department=department,
            condition_field="requested_days",
            condition_operator="ge",
            condition_value="1",
            company_id=company,
        )
        for sequence, manager in enumerate((self.first, self.last), start=1):
            MultipleApprovalManagers.objects.create(
                condition_id=condition, sequence=sequence, employee_id=manager.pk
            )
        self.days = LeaveRequest.objects.get(pk=self.leave_request.pk).requested_days

    def _reload(self):
        return LeaveRequest.objects.get(pk=self.leave_request.pk)

    def _step(self, manager):
        return LeaveRequestConditionApproval.objects.get(
            leave_request_id=self.leave_request, manager_id=manager
        )

    def _available(self):
        return AvailableLeave.objects.get(pk=self.balance.pk).available_days

    # --- model ------------------------------------------------------------

    def test_later_approver_cannot_go_first(self):
        approved, waiting = self._reload().approve_condition_step(self.last)
        self.assertFalse(approved)
        self.assertEqual(waiting.manager_id, self.first)
        self.assertFalse(self._step(self.last).is_approved)

    def test_steps_advance_in_order(self):
        approved, waiting = self._reload().approve_condition_step(self.first)
        self.assertTrue(approved)
        self.assertEqual(waiting.manager_id, self.last)
        approved, waiting = self._reload().approve_condition_step(self.last)
        self.assertTrue(approved)
        self.assertIsNone(waiting)

    def test_consecutive_steps_of_one_manager_are_approved_together(self):
        LeaveRequestConditionApproval.objects.filter(
            leave_request_id=self.leave_request, manager_id=self.last
        ).update(sequence=3)
        LeaveRequestConditionApproval.objects.create(
            sequence=2, leave_request_id=self.leave_request, manager_id=self.first
        )
        approved, waiting = self._reload().approve_condition_step(self.first)
        self.assertTrue(approved)
        self.assertEqual(waiting.manager_id, self.last)

    # --- web --------------------------------------------------------------

    def test_web_last_approver_first_leaves_request_pending(self):
        self.client.force_login(self.last_user)
        self.client.get(WEB_APPROVE.format(self.leave_request.pk))
        self.assertEqual(self._reload().status, "requested")
        self.assertFalse(self._step(self.last).is_approved)
        self.assertEqual(self._available(), 10)

    def test_web_chain_in_order_approves_and_deducts(self):
        self.client.force_login(self.first_user)
        self.client.get(WEB_APPROVE.format(self.leave_request.pk))
        self.assertEqual(self._reload().status, "requested")
        self.assertTrue(self._step(self.first).is_approved)

        self.client.force_login(self.last_user)
        self.client.get(WEB_APPROVE.format(self.leave_request.pk))
        self.assertEqual(self._reload().status, "approved")
        self.assertEqual(self._available(), 10 - self.days)

    def _hx_message(self, user):
        self.client.force_login(user)
        response = self.client.get(
            WEB_APPROVE.format(self.leave_request.pk), HTTP_HX_REQUEST="true"
        )
        return json.loads(response["HX-Trigger"])["horillaMessage"]

    def test_web_partial_approval_says_it_is_waiting(self):
        """The first approver must not be told the leave is approved."""
        message = self._hx_message(self.first_user)
        self.assertEqual(message["level"], "success")
        self.assertEqual(
            message["text"],
            "Your approval is saved. Waiting for "
            f"{self.last.get_full_name()} to approve.",
        )
        message = self._hx_message(self.last_user)
        self.assertEqual(message["text"], "Leave request approved successfully..")

    def test_web_out_of_turn_message_names_the_waiting_approver(self):
        message = self._hx_message(self.last_user)
        self.assertEqual(message["level"], "error")
        self.assertEqual(
            message["text"],
            f"{self.first.get_full_name()} needs to approve this request first.",
        )

    def test_web_reject_by_hr_outside_chain_does_not_crash(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(
            WEB_REJECT.format(self.leave_request.pk),
            {"reason": "Not this week"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reload().status, "rejected")

    def test_web_reject_by_chain_member_marks_their_step(self):
        self.client.force_login(self.first_user)
        self.client.post(
            WEB_REJECT.format(self.leave_request.pk),
            {"reason": "Not this week"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(self._reload().status, "rejected")
        self.assertTrue(self._step(self.first).is_rejected)

    # --- API --------------------------------------------------------------

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_api_last_approver_first_is_refused(self):
        response = self._api(self.last_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._reload().status, "requested")
        self.assertEqual(self._available(), 10)

    def test_api_hr_outside_chain_gets_400_not_500(self):
        response = self._api(self.hr_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._reload().status, "requested")

    def test_api_chain_in_order_approves(self):
        self._api(self.first_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(self._reload().status, "requested")
        response = self._api(self.last_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reload().status, "approved")
        self.assertEqual(self._available(), 10 - self.days)

    def test_api_bulk_by_first_approver_only_records_their_step(self):
        response = self._api(self.first_user).put(
            API_BULK, {"leave_request_id": [self.leave_request.pk]}, format="multipart"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reload().status, "requested")
        self.assertTrue(self._step(self.first).is_approved)
        self.assertEqual(self._available(), 10)

    def test_api_reject_by_hr_restores_the_employees_balance(self):
        self._api(self.first_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self._api(self.last_user).put(
            API_APPROVE.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(self._available(), 10 - self.days)
        response = self._api(self.hr_user).put(
            API_REJECT.format(self.leave_request.pk), {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reload().status, "rejected")
        self.assertEqual(self._available(), 10)
