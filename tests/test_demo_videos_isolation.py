"""
test_demo_videos_isolation.py — Verify strict demo video isolation and prompt generation across courses.
"""

import unittest
from src.core import kb_loader, prompt
from src.state.chat_state import get_default_state


class TestDemoVideosIsolation(unittest.TestCase):

    def test_face_yoga_demo_videos_display(self):
        """Face Yoga must report no recorded demo videos and never leak Yoga teachers."""
        display = kb_loader.format_demo_videos_display("face_yoga")
        self.assertIn("not available for Face Yoga", display)
        self.assertNotIn("Suman Lata", display)
        self.assertNotIn("Mradula", display)
        self.assertNotIn("Priya Mathur", display)

    def test_yoga_demo_videos_display(self):
        """Yoga must list Suman Lata, Mradula, Priya Mathur."""
        display = kb_loader.format_demo_videos_display("yoga")
        self.assertIn("Suman Lata", display)
        self.assertIn("Mradula", display)
        self.assertIn("Priya Mathur", display)

    def test_ladies_dance_demo_videos_display(self):
        """Ladies Dance must list Aditi Vyas, Gresi Gurung, Mansi Pal and note Lokesh Kumar."""
        display = kb_loader.format_demo_videos_display("ladies_dance")
        self.assertIn("Aditi Vyas", display)
        self.assertIn("Gresi Gurung", display)
        self.assertIn("Mansi Pal", display)
        self.assertIn("Lokesh Kumar", display)

    def test_kathak_demo_videos_display(self):
        """Kathak must list Ms. Madhuri and Kathak teachers."""
        display = kb_loader.format_demo_videos_display("kathak")
        self.assertIn("Ms. Madhuri", display)

    def test_face_yoga_system_prompt_isolation(self):
        """System prompt for Face Yoga must contain Face Yoga demo info and NOT mention Suman Lata in demo context."""
        state = get_default_state("+919876543210")
        state["course"] = "face_yoga"
        sp = prompt.format_system_prompt(state, course_id="face_yoga")
        
        self.assertIn("ACTIVE COURSE INSTRUCTOR DEMO VIDEOS", sp)
        self.assertIn("Recorded demo videos are currently not available for Face Yoga instructors", sp)
        # Verify global_rules did not leak (Suman Lata, Mradula, Priya Mathur) into Face Yoga trial class
        course_data = kb_loader.get_course_data("face_yoga")
        demo_rule = course_data.get("trial_class", {}).get("demo_videos_rule", "")
        self.assertNotIn("Suman Lata", demo_rule)
        self.assertNotIn("Mradula", demo_rule)


if __name__ == "__main__":
    unittest.main()
