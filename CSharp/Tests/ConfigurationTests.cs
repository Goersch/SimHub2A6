using System;
using System.IO;
using System.Linq;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static void MonitorPlacement()
        {
            string directory = Prepare(root), path = Path.Combine(directory, "INI", HmiConfiguration.FileName);
            string original = File.ReadAllText(path);
            foreach (int number in new[] { 1, 2, 999 })
            {
                File.WriteAllText(path, original.Replace("monitor = 2", "monitor = " + number));
                var ui = new SimHub2SimRig.HMI.UiContext(directory);
                var expected = System.Windows.Forms.Screen.AllScreens.FirstOrDefault(s => s.DeviceName == @"\\.\DISPLAY" + number) ?? System.Windows.Forms.Screen.PrimaryScreen;
                using (var form = new SimHub2SimRig.HMI.MainForm(ui))
                {
                    Equal(System.Windows.Forms.FormStartPosition.Manual, form.StartPosition, "HMI uses the configured startup position");
                    Equal(expected.WorkingArea.Left + 25, form.Left, "Selected monitor horizontal position, monitor=" + number);
                    Equal(expected.WorkingArea.Top + 25, form.Top, "Selected monitor vertical position, monitor=" + number);
                }
            }
            File.WriteAllText(path, original.Replace("monitor = 2", "monitor = 0"));
            Throws<InvalidDataException>(() => new HmiConfiguration(directory), "Monitor numbers start at one");
            File.WriteAllText(path, original.Replace("monitor = 2", ""));
            Equal(2, new HmiConfiguration(directory).Monitor, "Existing HMI INIs default to monitor two");
        }
        static void ConfigurationSeparation()
        {
            string comRoot = Prepare(root), hmiRoot = Prepare(root);
            File.Delete(Path.Combine(comRoot, "INI", HmiConfiguration.FileName));
            string comPath = Path.Combine(comRoot, "INI", Configuration.FileName);
            string comText = File.ReadAllText(comPath).Replace("position_min = 0", "position_min = 100").Replace("position_max = 131071", "position_max = 120000")
                .Replace("warning_after_operating_hours = 80.0", "warning_after_operating_hours = 77.0").Replace("alarm_after_operating_hours = 100.0", "alarm_after_operating_hours = 99.0");
            File.WriteAllText(comPath, comText);
            var com = new Configuration(comRoot);
            Check(!com.Ini.Has("ui"), "Com configuration has no display settings");
            Equal(1000, com.Ini.Int("control", "axis_status_refresh_ms"), "Axis-status polling belongs to Com");
            Throws<FileNotFoundException>(() => new HmiConfiguration(comRoot), "HMI requires its own INI");
            Status settings;
            using (var engine = new Engine(com, true)) settings = Json.Decode<Status>(Json.Encode(engine.Snapshot()));
            Equal(100, settings.SimHubPositionMin, "IPC exports the configured input minimum");
            Equal(120000, settings.SimHubPositionMax, "IPC exports the configured input maximum");
            Equal(com.RawMax, settings.RawPositionMax, "IPC exports the maintenance position range");
            Near(77, settings.GreaseWarningHours, "IPC exports the configured grease warning");
            Near(99, settings.GreaseAlarmHours, "IPC exports the configured grease alarm");

            File.Delete(Path.Combine(hmiRoot, "INI", Configuration.FileName));
            var hmi = new HmiConfiguration(hmiRoot);
            Check(hmi.Ini.Sections.SequenceEqual(new[] { "ui" }), "HMI configuration contains only its own display settings");
            Equal(100, hmi.Ini.Int("ui", "refresh_ms"), "HMI retains the configured refresh interval");
            Throws<FileNotFoundException>(() => new Configuration(hmiRoot), "Com requires its own INI");
            var ui = new SimHub2SimRig.HMI.UiContext(hmiRoot, "SimHub2SimRig.ConfigTests." + Guid.NewGuid().ToString("N"));
            using (var form = new SimHub2SimRig.HMI.MainForm(ui))
            {
                settings.Grease["1"].playtimeMinutes = 78 * 60;
                form.UpdateStatus(settings);
                var rig = Descendants(form).OfType<SimHub2SimRig.HMI.RigView>().Single();
                Equal(100, rig.RawMin, "HMI uses the range received from Com");
                Equal(120000, rig.RawMax, "HMI does not load range settings from disk");
                var grid = Descendants(form).OfType<System.Windows.Forms.DataGridView>().Single(g => !(g is SimHub2SimRig.HMI.LogTable));
                Equal(ui.T("MainDialog", "grease_soon_required"), Convert.ToString(grid.Rows[0].Cells[4].Value), "HMI uses Com's grease thresholds");
            }
            string hmiPath = Path.Combine(hmiRoot, "INI", HmiConfiguration.FileName);
            File.WriteAllText(hmiPath, File.ReadAllText(hmiPath).Replace("refresh_ms = 100", "refresh_ms = 0"));
            Throws<InvalidDataException>(() => new HmiConfiguration(hmiRoot), "HMI validates its own refresh interval");
            File.WriteAllText(Path.Combine(comRoot, "INI", HmiConfiguration.FileName), "invalid HMI config");
            Equal(7, new Configuration(comRoot).Axes.Length, "A malformed HMI INI does not prevent Com startup");
            File.WriteAllText(Path.Combine(hmiRoot, "INI", Configuration.FileName), "invalid Com config");
            File.Copy(Path.Combine(root, "INI", HmiConfiguration.FileName), hmiPath, true);
            Equal(100, new HmiConfiguration(hmiRoot).Ini.Int("ui", "refresh_ms"), "A malformed Com INI does not prevent HMI startup");
        }
    }
}
