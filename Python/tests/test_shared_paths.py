"""The relocated Python application must keep using the common runtime data."""

import unittest
from pathlib import Path

from Python import Analyse, Grease, Leveling, SimHub2SimRig
from Python.LIB import a6_simu, config, language, simrig_load_calibration
from Python.LIB.paths import INI_DIR, LOG_DIR, PYTHON_ROOT, SHARED_ROOT, SIMHUB_DATA_DIR


class SharedPathsTests(unittest.TestCase):
    def test_application_and_services_use_the_original_shared_directories(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(SHARED_ROOT, root)
        self.assertEqual(PYTHON_ROOT, root / "Python")
        self.assertEqual(INI_DIR, root / "INI")
        self.assertEqual(LOG_DIR, root / "LOG")
        self.assertEqual(SIMHUB_DATA_DIR, root / "SimHubData")
        paths = {
            config.CONFIG_PATH: root / "INI" / "SimHub2SimRig.ini",
            config.LOCAL_ROOM_LIGHT_CONFIG_PATH: root / "INI" / "room_light.ini",
            language.LANGUAGE_PATH: root / "INI" / "Language.ini",
            Grease.GREASE_DATA_FILE: root / "INI" / "grease_data.json",
            Leveling.LEVELING_OFFSET_FILE: root / "INI" / "leveling_offsets.json",
            Leveling.LEVELING_LOG_FILE: root / "LOG" / "leveling.log",
            simrig_load_calibration.LOAD_CALIBRATION_PATH: root / "INI" / "simrig_load_values.json",
            SimHub2SimRig.LOG_FILE: root / "LOG" / "simhub2a6.log",
        }
        for actual, expected in paths.items():
            with self.subTest(path=actual):
                self.assertEqual(actual, expected)
        self.assertEqual(Analyse.SIMHUB_DATA_DIR, root / "SimHubData")
        self.assertEqual(a6_simu.SIMHUB_DATA_DIR, root / "SimHubData")

    def test_configuration_and_language_still_load_from_the_shared_files(self):
        self.assertTrue(config.CONFIG_PATH.is_file())
        self.assertTrue(language.LANGUAGE_PATH.is_file())
        self.assertEqual(config.APP_CONFIG.rig.axes, config.load_configuration().rig.axes)
        self.assertEqual(language.text("Common", "status_ok"), "OK")
