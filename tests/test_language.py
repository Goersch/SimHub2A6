import unittest

from SimHub2A6.LIB.language import LANGUAGE_PATH, text


class LanguageTests(unittest.TestCase):
    def test_language_file_exists_and_loads_dialog_sections(self):
        self.assertTrue(LANGUAGE_PATH.is_file())
        self.assertEqual(text("Panels", "maintenance"), "Maintenance")
        self.assertEqual(text("Analysis", "no_data"), "No data")

    def test_language_text_expands_newlines_and_placeholders(self):
        message = text("MainDialog", "center_error", error="test error")
        self.assertEqual(
            message,
            "The axes could not be moved to the center position:\n"
            "test error",
        )
        self.assertEqual(
            text("Formats", "load_item", axis=4, value=12.34),
            "4: 12.3%",
        )


if __name__ == "__main__":
    unittest.main()
