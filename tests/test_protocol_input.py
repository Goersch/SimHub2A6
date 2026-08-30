import unittest

from SimHub2A6.SimHub2SimRig import _parse_positions


class ProtocolInputTests(unittest.TestCase):
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
