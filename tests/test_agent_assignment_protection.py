import asyncio
import sys
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)
os.chdir(base_dir)

from src.core.kb_loader import get_support_executives, get_support_executive_for_phone
from src.state.chat_state import is_protected_from_agent_assignment
from src.services.followup_service import get_followup_decision
from src.services.agent_handoff import execute_agent_handoff_async


class TestAgentAssignmentProtection(unittest.IsolatedAsyncioTestCase):

    def test_01_sudesh_salutation_is_maam(self):
        execs = get_support_executives()
        sudesh = next((e for e in execs if e.get("name", "").lower() == "sudesh"), None)
        self.assertIsNotNone(sudesh, "Sudesh not found in support_executives.json")
        self.assertEqual(sudesh.get("salutation"), "ma'am", f"Expected Sudesh to be ma'am, got {sudesh.get('salutation')}")

    def test_02_is_protected_from_agent_assignment(self):
        # 1. Paid lead variations
        self.assertEqual(is_protected_from_agent_assignment({"payment_status": "PAID"}), (True, "PAID"))
        self.assertEqual(is_protected_from_agent_assignment({"stage": "ENROLLED"}), (True, "PAID"))
        self.assertEqual(is_protected_from_agent_assignment({"is_enrolled": True}), (True, "PAID"))

        # 2. QR Sent variations
        self.assertEqual(is_protected_from_agent_assignment({"is_qr_sent": True}), (True, "QR_SENT"))
        self.assertEqual(is_protected_from_agent_assignment({"qr_code_sent": True}), (True, "QR_SENT"))
        self.assertEqual(is_protected_from_agent_assignment({"qr_id": "qr_12345"}), (True, "QR_SENT"))
        self.assertEqual(is_protected_from_agent_assignment({"order_id": "qr_99999"}), (True, "QR_SENT"))
        self.assertEqual(is_protected_from_agent_assignment({"stage": "PAYMENT_QR_SENT"}), (True, "QR_SENT"))

        # 3. Normal pre-QR lead
        self.assertEqual(is_protected_from_agent_assignment({"stage": "TIMING_SELECTED"}), (False, ""))
        self.assertEqual(is_protected_from_agent_assignment({"stage": "NEW"}), (False, ""))
        self.assertEqual(is_protected_from_agent_assignment({}), (False, ""))

    def test_03_rotational_support_executive(self):
        p1 = "919901072888"
        p2 = "918238272423"
        exec1 = get_support_executive_for_phone(p1)
        exec2 = get_support_executive_for_phone(p2)
        self.assertIn("phone", exec1)
        self.assertIn("name", exec1)
        self.assertIn("salutation", exec1)
        self.assertEqual(exec1["salutation"], "ma'am")
        self.assertEqual(exec2["salutation"], "ma'am")

    @patch("src.services.agent_handoff.assign_chat_to_agent_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.mark_escalated_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.send_text_message_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.save_message_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.save_user_state_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.get_user_state_async", new_callable=AsyncMock)
    async def test_04_handoff_prohibited_for_paid_lead(
        self, mock_get_state, mock_save_state, mock_save_msg, mock_send_text, mock_mark_esc, mock_assign
    ):
        mock_get_state.return_value = {
            "phone": "919000000001",
            "payment_status": "PAID",
            "stage": "ENROLLED",
            "language": "Hinglish",
        }

        res = await execute_agent_handoff_async(
            phone="919000000001",
            user_reply_text="Help me",
            escalation_reason="Customer requested agent",
        )

        self.assertTrue(res)
        # Verify agent assignment is strictly prohibited!
        mock_assign.assert_not_called()
        mock_mark_esc.assert_not_called()

        # Verify customer received support executive contact
        mock_send_text.assert_called_once()
        sent_msg = mock_send_text.call_args[0][1]
        self.assertIn("customer support team", sent_msg)
        self.assertIn("📞", sent_msg)

        # Verify state saved has is_escalated=False
        saved_state = mock_save_state.call_args[0][1]
        self.assertFalse(saved_state["is_escalated"])

    @patch("src.services.agent_handoff.assign_chat_to_agent_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.mark_escalated_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.send_text_message_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.save_message_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.save_user_state_async", new_callable=AsyncMock)
    @patch("src.services.agent_handoff.get_user_state_async", new_callable=AsyncMock)
    async def test_05_handoff_prohibited_for_qr_sent_lead(
        self, mock_get_state, mock_save_state, mock_save_msg, mock_send_text, mock_mark_esc, mock_assign
    ):
        mock_get_state.return_value = {
            "phone": "919000000002",
            "payment_status": "NONE",
            "stage": "PAYMENT_PENDING",
            "is_qr_sent": True,
            "qr_id": "qr_test123",
            "language": "English",
        }

        res = await execute_agent_handoff_async(
            phone="919000000002",
            user_reply_text="Need an agent",
            escalation_reason="Customer requested agent",
        )

        self.assertTrue(res)
        # Verify agent assignment is strictly prohibited!
        mock_assign.assert_not_called()
        mock_mark_esc.assert_not_called()

        # Verify customer received support executive contact + QR reminder
        mock_send_text.assert_called_once()
        sent_msg = mock_send_text.call_args[0][1]
        self.assertIn("customer support team", sent_msg)
        self.assertIn("UPI QR code", sent_msg)

        # Verify state saved has is_escalated=False
        saved_state = mock_save_state.call_args[0][1]
        self.assertFalse(saved_state["is_escalated"])

    def test_06_qr_lead_followup_never_escalates(self):
        # Count 0: 5 min payment assistance
        dec0 = get_followup_decision("919000000003", count=0, course_id="yoga", course_name="Yoga", lead_at_qr=True)
        self.assertEqual(dec0.action, "SEND_MESSAGE")
        self.assertIn("UPI QR code", dec0.message)

        # Count 1: 30 min payment assistance
        dec1 = get_followup_decision("919000000003", count=1, course_id="yoga", course_name="Yoga", lead_at_qr=True)
        self.assertEqual(dec1.action, "SEND_MESSAGE")
        self.assertIn("QR code", dec1.message)

        # Count 2: final reminder
        dec2 = get_followup_decision("919000000003", count=2, course_id="yoga", course_name="Yoga", lead_at_qr=True)
        self.assertEqual(dec2.action, "SEND_MESSAGE")

        # Count >= 3: SKIP (NEVER ESCALATE_TO_AGENT)
        dec3 = get_followup_decision("919000000003", count=3, course_id="yoga", course_name="Yoga", lead_at_qr=True)
        self.assertEqual(dec3.action, "SKIP")
        self.assertNotEqual(dec3.action, "ESCALATE_TO_AGENT")


if __name__ == "__main__":
    unittest.main()
