import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from SimHub2A6 import Dialog as DialogModule


class TriggerChartRefreshTests(unittest.TestCase):
    def test_refresh_transfers_every_pending_sample_and_schedules_one_second(self):
        pending = deque(((999.0, 10.5), (999.5, 11.0), (1000.0, 12.0)))
        simhub = SimpleNamespace(
            triggerIntervalLock=threading.Lock(),
            triggerIntervalSamples=pending,
        )
        dialog = SimpleNamespace(
            _closing=False,
            _trigger_chart_samples=deque(((939.0, 9.0), (940.0, 10.0))),
            trigger_interval_chart=Mock(),
            winfo_exists=Mock(return_value=True),
            after=Mock(return_value="next-refresh"),
            _refresh_trigger_chart=Mock(),
        )

        with (
            patch.object(DialogModule, "get_simhub_module", return_value=simhub),
            patch.object(DialogModule.time, "time", return_value=1000.0),
        ):
            DialogModule.Dialog._refresh_trigger_chart(dialog)

        self.assertEqual(list(pending), [])
        self.assertEqual(
            list(dialog._trigger_chart_samples),
            [(940.0, 10.0), (999.0, 10.5), (999.5, 11.0), (1000.0, 12.0)],
        )
        dialog.trigger_interval_chart.set_samples.assert_called_once_with(
            [(940.0, 10.0), (999.0, 10.5), (999.5, 11.0), (1000.0, 12.0)],
            1000.0,
        )
        dialog.after.assert_called_once_with(
            1000,
            dialog._refresh_trigger_chart,
        )
        self.assertEqual(dialog._trigger_chart_after_id, "next-refresh")


if __name__ == "__main__":
    unittest.main()
