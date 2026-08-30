import unittest
import warnings

from SimHub2A6 import A6Commands
from SimHub2A6.LIB import a6_driver
from SimHub2A6.LIB.logging_config import get_logger, set_gui_log_sink


class LoggingAndCompatibilityTests(unittest.TestCase):
    def test_optional_gui_log_sink_receives_formatted_messages(self):
        messages = []
        set_gui_log_sink(messages.append)
        try:
            get_logger("test.gui").warning("visible warning")
        finally:
            set_gui_log_sink(None)
        self.assertTrue(any("visible warning" in message for message in messages))

    def test_snake_case_a6_api_keeps_legacy_aliases(self):
        self.assertIs(A6Commands.connect_axes, A6Commands.A6_connect)
        self.assertIs(A6Commands.planner_stop, A6Commands.A6_planner_stop)
        self.assertIs(a6_driver.write_register.__self__, a6_driver.driver)
        self.assertIs(
            a6_driver.write_register.__func__,
            a6_driver.driver.write_register.__func__,
        )

    def test_legacy_facade_has_explicit_exports(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from SimHub2A6.LIB import A6Lib
        self.assertIn("C11_06", A6Lib.__all__)
        self.assertNotIn("warnings", A6Lib.__all__)
