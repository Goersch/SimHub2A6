import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from SimHub2A6 import Dialog as DialogModule
from SimHub2A6 import Grease, SimHubCommands
from SimHub2A6.LIB.config import GREASE_CONFIG


class GreaseDataTests(unittest.TestCase):
    def test_dialog_grease_status_uses_warning_and_alarm_thresholds(self):
        warning_minutes = GREASE_CONFIG.warning_after_operating_hours * 60
        alarm_minutes = GREASE_CONFIG.alarm_after_operating_hours * 60

        self.assertEqual(DialogModule.grease_status(warning_minutes)[0], "OK")
        self.assertEqual(
            DialogModule.grease_status(warning_minutes + 1)[0],
            "Due soon",
        )
        self.assertEqual(
            DialogModule.grease_status(alarm_minutes + 1)[0],
            "Required",
        )

    def test_grease_table_data_is_owned_and_persisted_by_grease_module(self):
        with TemporaryDirectory() as directory:
            data_file = Path(directory) / "grease_data.json"
            with (
                patch.object(Grease, "GREASE_DATA_FILE", data_file),
                patch.object(Grease, "_grease_data", None),
            ):
                data = Grease.grease_data_snapshot()
                self.assertEqual(set(data), {"1", "2", "3", "4-7"})

                Grease.reset_grease_data(2, 2)
                stored = json.loads(data_file.read_text(encoding="utf-8"))

        self.assertTrue(stored["2"]["lastGreaseAt"])
        self.assertEqual(stored["2"]["playtimeMinutes"], 0.0)

    def test_hub_grease_releases_and_reapplies_brakes(self):
        controller = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.return_value = True
        with (
            patch.object(Grease, "motion_controller", controller),
            patch.object(Grease, "enabled_axes", return_value=[4, 5, 6, 7]),
            patch.object(Grease, "_set_parameters"),
            patch.object(Grease, "_move_axes_to_mm"),
            patch.object(SimHubCommands, "ensure_maintenance_planners"),
        ):
            Grease._worker(4, 7, stop_event)

        self.assertEqual(
            controller.set_servo_enabled.call_args_list,
            [
                *(call(axis, True) for axis in range(4, 8)),
                *(call(axis, False) for axis in range(4, 8)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
