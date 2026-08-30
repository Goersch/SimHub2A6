import unittest
from unittest.mock import MagicMock, patch

from SimHub2A6 import Dialog as DialogModule
from SimHub2A6 import SimHub2SimRig


class ApplicationCleanupTests(unittest.TestCase):
    def test_standalone_dialog_handles_ctrl_c_without_traceback(self):
        dialog = MagicMock()
        dialog.mainloop.side_effect = KeyboardInterrupt
        with (
            patch.object(DialogModule, "configure_logging"),
            patch.object(DialogModule, "Dialog", return_value=dialog),
            patch.object(DialogModule, "get_simhub_commands_module", return_value=None),
        ):
            DialogModule.run_standalone()

        dialog.close.assert_called_once_with()

    def test_occupied_listener_prevents_hardware_initialization(self):
        sock = MagicMock()
        sock.bind.side_effect = OSError("address in use")

        with (
            patch.object(SimHub2SimRig, "install_file_logging"),
            patch.object(SimHub2SimRig, "delete_expired_simhub_data"),
            patch.object(SimHub2SimRig.shcmd, "handle_init") as handle_init,
            patch.object(SimHub2SimRig.socket, "socket", return_value=sock),
            patch.object(SimHub2SimRig, "Dialog") as dialog,
        ):
            SimHub2SimRig.main()

        handle_init.assert_not_called()
        dialog.assert_not_called()
        sock.close.assert_called_once_with()

    def test_dialog_startup_error_runs_shutdown_cleanup(self):
        thread = MagicMock()
        sock = MagicMock()

        with (
            patch.object(SimHub2SimRig, "install_file_logging"),
            patch.object(SimHub2SimRig, "delete_expired_simhub_data"),
            patch.object(SimHub2SimRig.shcmd, "handle_init"),
            patch.object(SimHub2SimRig.shcmd, "handle_end") as handle_end,
            patch.object(SimHub2SimRig.threading, "Thread", return_value=thread),
            patch.object(SimHub2SimRig.socket, "socket", return_value=sock),
            patch.object(
                SimHub2SimRig,
                "Dialog",
                side_effect=RuntimeError("dialog startup failed"),
            ),
            patch.object(SimHub2SimRig.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "dialog startup failed"):
                SimHub2SimRig.main()

        handle_end.assert_called_once_with()
        sock.close.assert_called_once_with()

    def test_dialog_is_closed_when_mainloop_returns(self):
        thread = MagicMock()
        sock = MagicMock()
        dialog = MagicMock()

        with (
            patch.object(SimHub2SimRig, "install_file_logging"),
            patch.object(SimHub2SimRig, "delete_expired_simhub_data"),
            patch.object(SimHub2SimRig.shcmd, "handle_init"),
            patch.object(SimHub2SimRig.shcmd, "handle_end"),
            patch.object(SimHub2SimRig.threading, "Thread", return_value=thread),
            patch.object(SimHub2SimRig.socket, "socket", return_value=sock),
            patch.object(SimHub2SimRig, "Dialog", return_value=dialog),
        ):
            SimHub2SimRig.main()

        dialog.close.assert_called_once_with()
        sock.close.assert_called_once_with()

    def test_dialog_is_destroyed_when_close_cleanup_fails(self):
        dialog = MagicMock()
        dialog._closing = False
        dialog._analyse_dialog = None
        dialog._leveling_running = False
        dialog._refresh_after_id = None
        dialog._motor_after_id = None
        dialog._trigger_chart_after_id = None
        dialog._apply_motor_after_ids = set()
        dialog.winfo_exists.return_value = True

        with patch(
            "SimHub2A6.Dialog.Grease.save_grease_data",
            side_effect=RuntimeError("save failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                SimHub2SimRig.Dialog.close(dialog)

        dialog.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
