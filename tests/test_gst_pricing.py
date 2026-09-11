import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import asyncio
from src.core import kb_loader
from src.core import conversation_handler


def test_gst_slabs_in_kb():
    print("\n--- Test 1: KB Loader GST Slabs ---")
    yoga_gst = kb_loader.get_course_gst_percentage("yoga")
    kathak_gst = kb_loader.get_course_gst_percentage("kathak")
    face_yoga_gst = kb_loader.get_course_gst_percentage("face_yoga")

    print(f"Yoga GST: {yoga_gst}%")
    print(f"Kathak GST: {kathak_gst}%")
    print(f"Face Yoga GST: {face_yoga_gst}%")

    assert yoga_gst == 5, f"Expected 5% for Yoga, got {yoga_gst}%"
    assert kathak_gst == 18, f"Expected 18% for Kathak, got {kathak_gst}%"
    assert face_yoga_gst == 5, f"Expected 5% for Face Yoga, got {face_yoga_gst}%"

    yoga_display = kb_loader.format_package_fees_display("yoga")
    assert "5% GST" in yoga_display, f"Expected 5% GST in Yoga display: {yoga_display}"

    kathak_display = kb_loader.format_package_fees_display("kathak")
    assert "18% GST" in kathak_display, f"Expected 18% GST in Kathak display: {kathak_display}"

    face_display = kb_loader.format_package_fees_display("face_yoga")
    assert "5% GST" in face_display, f"Expected 5% GST in Face Yoga display: {face_display}"

    print("PASS: KB Loader GST slabs and display formatting verified!")


async def test_live_llm_gst_responses():
    print("\n--- Test 2: Live LLM GST Responses ---")

    # Case A: Yoga "View Packages"
    print("\n[Case A] Yoga: User asks 'View Packages'")
    res_packages = await conversation_handler.handle_incoming_message(
        user_message="View Packages",
        session_state={"stage": "NEW", "course": "yoga", "is_target_ad": True},
        conversation_history=[],
        course_id="yoga"
    )
    reply_a = res_packages.get("reply", "")
    print(f"AI Reply:\n{reply_a}\n")
    assert "gst" in reply_a.lower() or "5%" in reply_a, "Reply must mention GST slab note when viewing packages"

    # Case B: Yoga "300 se jyda to nhi dene na ???"
    print("\n[Case B] Yoga: User asks '300 se jyda to nhi dene na ???'")
    res_extra = await conversation_handler.handle_incoming_message(
        user_message="300 se jyda to nhi dene na ???",
        session_state={"stage": "NEW", "course": "yoga", "is_target_ad": True},
        conversation_history=[
            {"role": "user", "content": "View Packages"},
            {"role": "assistant", "content": reply_a}
        ],
        course_id="yoga"
    )
    reply_b = res_extra.get("reply", "")
    print(f"AI Reply:\n{reply_b}\n")
    # Must NOT claim they don't have to pay more than 300
    assert "isse zyada nahi dena" not in reply_b.lower()
    assert "isse jyada nahi dena" not in reply_b.lower()
    # Must explain 5% GST / 315
    assert "gst" in reply_b.lower(), "Must clarify GST applies"
    assert any(x in reply_b for x in ["5%", "15", "315"]), "Must mention 5% GST or ₹15 / ₹315"

    # Case C: Kathak 18% GST
    print("\n[Case C] Kathak: User asks 'Kathak ki 1 month fees kitni h aur koi extra charge h kya?'")
    res_kathak = await conversation_handler.handle_incoming_message(
        user_message="Kathak ki 1 month fees kitni h aur koi extra charge h kya?",
        session_state={"stage": "NEW", "course": "kathak", "is_target_ad": True},
        conversation_history=[],
        course_id="kathak"
    )
    reply_c = res_kathak.get("reply", "")
    print(f"AI Reply:\n{reply_c}\n")
    assert "18%" in reply_c or "gst" in reply_c.lower(), "Kathak reply must reflect 18% GST slab"

    print("PASS: Live LLM GST responses tested and verified!")


if __name__ == "__main__":
    test_gst_slabs_in_kb()
    asyncio.run(test_live_llm_gst_responses())
    print("\nALL GST TESTS PASSED SUCCESSFULLY!")
