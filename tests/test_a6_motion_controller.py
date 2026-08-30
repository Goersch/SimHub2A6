import unittest
from typing import cast

from SimHub2A6.LIB import a6_registers as reg
from SimHub2A6.LIB.a6_driver import A6Driver
from SimHub2A6.LIB.a6_motion_controller import A6MotionController


class FakeDriver:
    def __init__(self):
        self.register_writes = []
        self.uint32_writes = []
        self.paired_writes = []
        self.uint32_read_value = 0

    def read_u32(self, axis, address, signed=False):
        self.last_uint32_read = (axis, address, signed)
        return self.uint32_read_value

    def write_reg(self, axis, address, value, log_hex=False, checkCRC=True):
        self.register_writes.append((axis, address, value, log_hex, checkCRC))

    def write_u32(self, axis, address, value, log_hex=False, checkCRC=True):
        self.uint32_writes.append((axis, address, value, log_hex, checkCRC))

    def write_two_u32(
        self, axis, address, first_value, second_value, log_hex=False, checkCRC=True
    ):
        self.paired_writes.append(
            (axis, address, first_value, second_value, log_hex, checkCRC)
        )


class A6MotionControllerTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.controller = A6MotionController(
            cast(A6Driver, self.driver), sleep=lambda _delay: None
        )

    def test_parameter_cache_suppresses_unchanged_modbus_writes(self):
        self.assertFalse(self.controller.planner_parameters_match(1, 100, 300, 400))
        self.controller.planner_set_parameters(1, 100, 300, 400)
        self.assertTrue(self.controller.planner_parameters_match(1, 100, 300, 400))
        first_write_count = (
            len(self.driver.register_writes)
            + len(self.driver.uint32_writes)
            + len(self.driver.paired_writes)
        )

        self.controller.planner_set_parameters(1, 100, 300, 400)
        unchanged_write_count = (
            len(self.driver.register_writes)
            + len(self.driver.uint32_writes)
            + len(self.driver.paired_writes)
        )
        self.assertEqual(first_write_count, unchanged_write_count)

        self.controller.planner_set_parameters(1, 100, 500, 400)
        self.assertFalse(self.controller.planner_parameters_match(1, 100, 300, 400))
        self.assertTrue(self.controller.planner_parameters_match(1, 100, 500, 400))
        self.assertEqual(self.driver.uint32_writes[-1][:3], (1, reg.C11_0A, 500))

    def test_position_cache_still_emits_trigger_edges(self):
        self.controller.planner_set_position_mm(1, 12.5)
        self.controller.planner_set_position_mm(1, 12.5)

        position_writes = [
            write for write in self.driver.uint32_writes if write[1] == reg.C11_06
        ]
        trigger_writes = [
            write for write in self.driver.register_writes if write[1] == reg.POS_TRIG
        ]
        self.assertEqual(len(position_writes), 1)
        self.assertEqual([write[2] for write in trigger_writes], [1, 0, 1, 0])

    def test_planner_can_start_at_the_current_position(self):
        self.controller.planner_start(4, -30.0)

        position_writes = [
            write for write in self.driver.uint32_writes if write[1] == reg.C11_06
        ]
        self.assertEqual(position_writes[0][2], -60_000)

    def test_broadcast_trigger_never_waits_for_crc(self):
        self.controller.planner_trigger(0, check_crc=True)
        self.assertEqual(
            self.driver.register_writes,
            [
                (0, reg.POS_TRIG, 1, False, False),
                (0, reg.POS_TRIG, 0, False, False),
            ],
        )

    def test_actual_position_is_converted_from_drive_units_to_mm(self):
        self.driver.uint32_read_value = 20_000

        position_mm = self.controller.read_position_mm(4)

        self.assertEqual(
            self.driver.last_uint32_read,
            (4, reg.U40_16, True),
        )
        self.assertAlmostEqual(position_mm, 10.0)

    def test_servo_enable_releases_and_disable_applies_brake(self):
        self.controller.set_servo_enabled(4, True)
        self.controller.set_servo_enabled(4, False)

        self.assertEqual(
            self.driver.register_writes,
            [
                (4, reg.S_ON, 1, False, True),
                (4, reg.S_ON, 0, False, True),
            ],
        )

    def test_homing_done_uses_do5_active_low_with_positive_logic(self):
        class FakeModbus:
            values = {
                reg.C04_38: 9,
                reg.C04_39: 0,
                reg.U40_05: 0xFFEE,
            }

            def read_regs(self, _axis, address, _quantity=1):
                return [self.values[address]]

        driver = A6Driver(FakeModbus())
        self.assertTrue(driver.homing_done(1))

        driver._modbus.values[reg.U40_05] = 0xFFFE
        self.assertFalse(driver.homing_done(1))

    def test_homing_done_respects_reversed_do5_logic(self):
        class FakeModbus:
            values = {
                reg.C04_38: 9,
                reg.C04_39: 1,
                reg.U40_05: 0xFFFE,
            }

            def read_regs(self, _axis, address, _quantity=1):
                return [self.values[address]]

        driver = A6Driver(FakeModbus())
        self.assertTrue(driver.homing_done(1))

        driver._modbus.values[reg.U40_05] = 0xFFEE
        self.assertFalse(driver.homing_done(1))


if __name__ == "__main__":
    unittest.main()
