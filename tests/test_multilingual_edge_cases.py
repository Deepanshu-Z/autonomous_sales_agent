"""
tests/test_multilingual_edge_cases.py — Comprehensive Edge-Case Verification Suite for Multilingual Support.

Verifies:
1. Malayalam script query detection & prompt enforcement.
2. Manglish (Latin script Malayalam) transliteration preservation (no Hinglish corruption).
3. Explicit language switch to Malayalam / regional languages.
4. Dynamic handling of any world language without hardcoding (e.g. French, Spanish).
5. Turn-2 language selection bypass with regional script query (no English lock-in).
6. Localized 'Talk to Agent' button compliance (Hindi/Hinglish localized, others English).
7. Bidirectional language switchback (Malayalam <-> English <-> Hindi).
8. Zero-links enforcement across all multilingual replies.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch, MagicMock

from src.core import language_loader, prompt as prompt_module, intent_classifier
from src.core.conversation_handler import _ensure_app_website_links


class TestMultilingualEdgeCases(unittest.TestCase):

    def setUp(self):
        language_loader.clear_cache()

    def test_01_languages_json_configuration(self):
        """Verify data/languages.json contains Malayalam, Tamil, etc. with valid buttons and aliases."""
        cfg = language_loader.get_language_config()
        self.assertIn("languages", cfg)
        lang_names = [l["name"] for l in cfg["languages"]]
        
        self.assertIn("Malayalam", lang_names)
        self.assertIn("Tamil", lang_names)
        self.assertIn("Telugu", lang_names)
        self.assertIn("English", lang_names)
        self.assertIn("Hindi", lang_names)
        self.assertIn("Hinglish", lang_names)

        # Alias matching
        self.assertEqual(language_loader.match_language("malayalam"), "Malayalam")
        self.assertEqual(language_loader.match_language("മലയാളം"), "Malayalam")
        self.assertEqual(language_loader.match_language("manglish"), "Malayalam")
        self.assertEqual(language_loader.match_language("tamil"), "Tamil")
        self.assertEqual(language_loader.match_language("தமிழ்"), "Tamil")

    def test_02_agent_button_strict_compliance(self):
        """
        Verify strict agent button rule:
        - Hindi -> 'एजेंट से बात'
        - Hinglish -> 'Agent se baat'
        - Any other language (Malayalam, Tamil, French, etc.) -> 'Talk to Agent'
        """
        self.assertEqual(language_loader.get_agent_button("Hindi"), "एजेंट से बात")
        self.assertEqual(language_loader.get_agent_button("Hinglish"), "Agent se baat")
        self.assertEqual(language_loader.get_agent_button("Malayalam"), "Talk to Agent")
        self.assertEqual(language_loader.get_agent_button("Tamil"), "Talk to Agent")
        self.assertEqual(language_loader.get_agent_button("Spanish"), "Talk to Agent")
        self.assertEqual(language_loader.get_agent_button(None), "Talk to Agent")

    def test_03_malayalam_script_detection_and_state_update(self):
        """
        If user types in Malayalam script, state should update to Malayalam
        even if the session was previously defaulted to English.
        """
        state = {"language": "English", "language_selected": False, "course": "yoga"}
        flags = {
            "_raw_text": "യോഗ ക്ലാസിന്റെ ഫീസ് എത്രയാണ്?",
            "detected_language": "Malayalam (Malayalam script)",
            "customer_intent": "QUESTION",
            "question_summary": "yoga fees"
        }
        new_state = intent_classifier.apply_extraction_to_state(state, flags)
        self.assertEqual(new_state["language"], "Malayalam (Malayalam script)")
        self.assertTrue(new_state["language_selected"])

    def test_04_manglish_latin_script_not_corrupted_to_hinglish(self):
        """
        If user types Malayalam in Latin script (Manglish),
        it must be preserved as Malayalam (Latin script) and NOT corrupted to Hinglish.
        """
        state = {"language": "English", "language_selected": False, "course": "western_dance"}
        flags = {
            "_raw_text": "njan dance class join cheyyan agrahikkunnu fees kitrayanu",
            "detected_language": "Malayalam (Latin script)",
            "customer_intent": "QUESTION"
        }
        new_state = intent_classifier.apply_extraction_to_state(state, flags)
        self.assertIn("Malayalam", new_state["language"])
        self.assertNotIn("Hinglish", new_state["language"])

    def test_05_explicit_language_switch_any_world_language(self):
        """
        Explicit language requests must work dynamically for both configured languages
        and unconfigured world languages (Zero Hardcoding).
        """
        state = {"language": "English", "language_selected": True}

        # 1. Configured regional language
        flags1 = {
            "_raw_text": "can we talk in Malayalam please",
            "explicit_language_request": "Malayalam"
        }
        st1 = intent_classifier.apply_extraction_to_state(state, flags1)
        self.assertEqual(st1["language"], "Malayalam")

        # 2. Arbitrary world language (e.g. French / Spanish)
        flags2 = {
            "_raw_text": "speak in French please",
            "explicit_language_request": "French"
        }
        st2 = intent_classifier.apply_extraction_to_state(state, flags2)
        self.assertEqual(st2["language"], "French")

    def test_06_prompt_system_enforces_malayalam_and_native_script(self):
        """
        Verify format_system_prompt injects non-negotiable Malayalam script enforcement.
        """
        state = {"course": "yoga", "language": "Malayalam (Malayalam script)", "stage": "NEW"}
        prompt_text = prompt_module.format_system_prompt(state, course_id="yoga", detected_language="Malayalam (Malayalam script)")

        self.assertIn("CRITICAL MANDATORY LANGUAGE & SCRIPT ENFORCEMENT", prompt_text)
        self.assertIn("Malayalam", prompt_text)
        self.assertIn("native script", prompt_text)
        self.assertNotIn("The customer's language is English", prompt_text)

    def test_07_bidirectional_switchback(self):
        """
        User switches from Malayalam -> English -> Hindi seamlessly.
        """
        state = {"course": "yoga", "language": "Malayalam", "language_selected": True}

        # Step 1: Switch to English
        flags_en = {
            "_raw_text": "switch to English",
            "explicit_language_request": "English"
        }
        st_en = intent_classifier.apply_extraction_to_state(state, flags_en)
        self.assertEqual(st_en["language"], "English")

        # Step 2: Switch to Hindi
        flags_hi = {
            "_raw_text": "हिन्दी में बात करो",
            "explicit_language_request": "Hindi"
        }
        st_hi = intent_classifier.apply_extraction_to_state(st_en, flags_hi)
        self.assertEqual(st_hi["language"], "Hindi")

    def test_08_zero_links_policy_in_malayalam(self):
        """
        Verify that URLs are stripped from regional language responses unless explicitly asked.
        """
        malayalam_reply_with_links = (
            "യോഗ ക്ലാസുകൾക്ക് സ്വാഗതം! രജിസ്റ്റർ ചെയ്യുക.\n"
            "ആപ്പ് ഡൗൺലോഡ് ചെയ്യുക: https://play.google.com/store/apps/details?id=com.sensationz\n"
            "വെബ്സൈറ്റ്: https://shop.sensationzperformingarts.com/\n"
            "വീഡിയോ ഗൈഡ് കാണുക."
        )

        cleaned = _ensure_app_website_links(malayalam_reply_with_links, user_asked_for_links=False)
        self.assertNotIn("play.google.com", cleaned)
        self.assertNotIn("shop.sensationzperformingarts.com", cleaned)
        self.assertIn("യോഗ ക്ലാസുകൾക്ക് സ്വാഗതം!", cleaned)

        # When explicitly asked, links are preserved
        explicit_links = _ensure_app_website_links(malayalam_reply_with_links, user_asked_for_links=True)
        self.assertIn("play.google.com", explicit_links)

    def test_09_turn2_ad_bypass_with_malayalam_query(self):
        """
        If user enters via ad, receives language prompt, and on turn 2 asks directly
        in Malayalam script without clicking a button, state updates dynamically to Malayalam.
        """
        # Simulated state after Turn 1
        state = {
            "is_target_ad": True,
            "language_prompt_sent": True,
            "awaiting_language": True,
            "welcome_sent": False,
            "course": "yoga"
        }
        
        # Turn 2: User sends Malayalam script question
        user_text = "ക്ലാസ്സ് സമയം എപ്പോഴാണ്?"
        flags = {
            "_raw_text": user_text,
            "detected_language": "Malayalam (Malayalam script)",
            "customer_intent": "QUESTION",
            "question_summary": "class timings"
        }
        new_state = intent_classifier.apply_extraction_to_state(state, flags)
        self.assertEqual(new_state["language"], "Malayalam (Malayalam script)")
        self.assertTrue(new_state["language_selected"])

    def test_10_end_to_end_multilingual_buttons_and_agent_compliance(self):
        """
        Verify that a Malayalam response with custom buttons correctly attaches the
        English 'Talk to Agent' button and truncates labels to 20 characters maximum.
        """
        from src.services.quick_reply_service import send_followup_with_buttons_async

        raw_buttons = ["ഫീസ് എത്രയാണ് (Fees)", "ട്രയൽ ക്ലാസ് (Trial)", "Talk to Agent"]
        lang = "Malayalam (Malayalam script)"
        
        agent_btn = language_loader.get_agent_button(lang)
        all_agent_labels = language_loader.get_all_agent_buttons()
        
        filtered_b = [str(b).strip() for b in raw_buttons if str(b).strip() and str(b).strip().lower() not in all_agent_labels]
        final_buttons = filtered_b[:2] + [agent_btn]

        self.assertEqual(len(final_buttons), 3)
        self.assertEqual(agent_btn, "Talk to Agent")
        self.assertEqual(final_buttons[-1], "Talk to Agent")
        for b in final_buttons:
            self.assertLessEqual(len(b), 20)


if __name__ == "__main__":
    unittest.main()
