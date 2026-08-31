import unittest
from unittest.mock import patch

from SimHub2A6 import SimHub2SimRig
from SimHub2A6.SimHub2SimRig import _parse_positions


class ProtocolInputTests(unittest.TestCase):
    def test_positions_are_ignored_while_maintenance_is_active(self):
        with (
            patch.object(
                SimHub2SimRig.shcmd,
                "simhub_telegrams_blocked",
                return_value=True,
            ),
            patch.object(SimHub2SimRig, "set_values") as set_values,
            patch.object(SimHub2SimRig.shcmd, "arm_simhub_positions") as arm,
        ):
            SimHub2SimRig.parse_and_dispatch("POSITIONS 1 2 3 4 5 6 7")

        set_values.assert_not_called()
        arm.assert_not_called()

    def test_all_telegrams_are_ignored_after_shutdown_starts(self):
        shutdown_event = unittest.mock.MagicMock()
        with (
            patch.object(
                SimHub2SimRig.shcmd,
                "shutdown_in_progress",
                return_value=True,
            ),
            patch.object(SimHub2SimRig, "shutdownRequested", shutdown_event),
            patch.object(SimHub2SimRig, "set_values") as set_values,
            patch.object(SimHub2SimRig, "send_shutdown_response") as response,
        ):
            SimHub2SimRig.parse_and_dispatch(
                "POSITIONS 1 2 3 4 5 6 7;START;END;SHUTDOWN",
                ("127.0.0.1", 12345),
            )

        set_values.assert_not_called()
        shutdown_event.set.assert_not_called()
        response.assert_not_called()

    def test_position_payload_requires_seven_in_range_integers(self):
        self.assertEqual(
            _parse_positions("0 1 2 3 4 5 131071"),
            (0, 1, 2, 3, 4, 5, 131071),
        )
        with self.assertRaises(ValueError):
            _parse_positions("1 2 3")
        with self.assertRaises(ValueError):
            _parse_positions("1 2 3 4 5 6 x")
        with self.assertRaisesRegex(
            ValueError,
            r"invalid: axis 7, target position 131072$",
        ):
            _parse_positions("1 2 3 4 5 6 131072")

    def test_out_of_range_error_identifies_every_axis_and_target(self):
        with self.assertRaisesRegex(
            ValueError,
            r"invalid: axis 2, target position -1, "
            r"axis 6, target position 131072$",
        ):
            _parse_positions("1 -1 3 4 5 131072 7")
