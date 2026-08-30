import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from SimHub2A6.Analyse import RESIZE_REDRAW_DELAY_MS as ANALYSE_DELAY
from SimHub2A6.Analyse import SignalChart
from SimHub2A6.LIB.ui_widgets import RESIZE_REDRAW_DELAY_MS as TRIGGER_DELAY
from SimHub2A6.LIB.ui_widgets import TriggerIntervalChart


class ResizeRedrawTests(unittest.TestCase):
    def test_resize_events_are_debounced(self):
        for chart_class, delay in (
            (SignalChart, ANALYSE_DELAY),
            (TriggerIntervalChart, TRIGGER_DELAY),
        ):
            with self.subTest(chart=chart_class.__name__):
                chart = SimpleNamespace(
                    _redraw_after_id="previous",
                    after_cancel=Mock(),
                    after=Mock(return_value="next"),
                )
                chart._run_scheduled_redraw = Mock()

                chart_class._schedule_redraw(chart)

                chart.after_cancel.assert_called_once_with("previous")
                chart.after.assert_called_once_with(
                    delay,
                    chart._run_scheduled_redraw,
                )
                self.assertEqual(chart._redraw_after_id, "next")

    def test_scheduled_callback_is_cleared_before_drawing(self):
        for chart_class in (SignalChart, TriggerIntervalChart):
            with self.subTest(chart=chart_class.__name__):
                redraw_ids_seen = []
                chart = SimpleNamespace(_redraw_after_id="pending")
                chart.draw = Mock(
                    side_effect=lambda current_chart=chart, seen=redraw_ids_seen: (
                        seen.append(current_chart._redraw_after_id)
                    )
                )

                chart_class._run_scheduled_redraw(chart)

                self.assertEqual(redraw_ids_seen, [None])

    def test_moving_without_resizing_does_not_schedule_a_redraw(self):
        for chart_class in (SignalChart, TriggerIntervalChart):
            with self.subTest(chart=chart_class.__name__):
                chart = SimpleNamespace(
                    _last_configure_size=(800, 600),
                    _redraw_after_id=None,
                    after_cancel=Mock(),
                    after=Mock(),
                )
                event = SimpleNamespace(width=800, height=600)

                chart_class._schedule_redraw(chart, event)

                chart.after_cancel.assert_not_called()
                chart.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
