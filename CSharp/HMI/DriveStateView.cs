using System;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public static class DriveStateView
    {
        // AxisStatus.Error is the legacy IPC name for U40.43 (operation /
        // inhibit reason), not an alarm number. Preserve the Python dialog's
        // complete mapping, including both 0 and 5 meaning OK.
        public static string ReasonText(UiContext ui, int reason)
        {
            switch (reason)
            {
                case 0: case 5: return ui.T("Common", "status_ok");
                case 1: return ui.T("MainDialog", "error_servo_off");
                case 2: return ui.T("MainDialog", "error_ready");
                case 3: return ui.T("MainDialog", "error_start_locked");
                case 4: return ui.T("MainDialog", "error_positioning_locked");
                default: return ui.T("MainDialog", "error_unknown").Replace("{value}", reason.ToString(System.Globalization.CultureInfo.CurrentCulture));
            }
        }

        public static bool IsFault(AxisStatus axis)
        {
            // U41.0A == 3 is the same fault criterion used by Com's existing
            // hub interlock. U40.43 alone does not establish a drive alarm.
            return axis.Enabled && axis.Servo == 3;
        }

        public static bool IsInhibited(AxisStatus axis)
        {
            return axis.Enabled && (axis.Error == 3 || axis.Error == 4);
        }

        public static string Text(UiContext ui, AxisStatus axis, Status status)
        {
            if (!axis.Enabled) return "Disabled";
            if (IsFault(axis)) return "Drive fault (ServoStatus 3)";
            if (status.Leveling && axis.Axis >= 4 && axis.Axis <= 7)
                return ui.T("MainDialog", "state_leveling");
            if (axis.Error == 0 && status.Greasing && axis.Axis >= status.GreaseFirst && axis.Axis <= status.GreaseLast)
                return ui.T("MainDialog", "state_greasing");
            return ReasonText(ui, axis.Error);
        }
    }
}
