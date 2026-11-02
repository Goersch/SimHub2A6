using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

namespace SimHub2SimRig.Shared
{
    public sealed class Ini
    {
        readonly Dictionary<string, Dictionary<string, string>> data = new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);
        public Ini(string path)
        {
            string section = "";
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith(";") || line.StartsWith("#")) continue;
                if (line.StartsWith("[") && line.EndsWith("]"))
                {
                    section = line.Substring(1, line.Length - 2);
                    if (data.ContainsKey(section)) throw new InvalidDataException("Duplicate INI section: " + section);
                    data[section] = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                }
                else
                {
                    int at = line.IndexOf('=');
                    if (at < 0 || !data.ContainsKey(section)) throw new InvalidDataException("Invalid INI line: " + raw);
                    string key = line.Substring(0, at).Trim();
                    if (data[section].ContainsKey(key)) throw new InvalidDataException("Duplicate INI key: " + key);
                    data[section][key] = line.Substring(at + 1).Trim();
                }
            }
        }
        public IEnumerable<string> Sections { get { return data.Keys; } }
        public bool Has(string section) { return data.ContainsKey(section); }
        public string Get(string section, string key) { return data[section][key].Replace("\\n", "\n"); }
        public double Number(string section, string key)
        {
            double value = double.Parse(Get(section, key), CultureInfo.InvariantCulture);
            if (double.IsNaN(value) || double.IsInfinity(value)) throw new InvalidDataException(section + "." + key + " must be finite");
            return value;
        }
        public int Int(string section, string key) { return int.Parse(Get(section, key), CultureInfo.InvariantCulture); }
        public bool Bool(string section, string key)
        {
            string value = Get(section, key).ToLowerInvariant();
            if (new[] { "1", "yes", "true", "on" }.Contains(value)) return true;
            if (new[] { "0", "no", "false", "off" }.Contains(value)) return false;
            throw new InvalidDataException("Invalid boolean: " + value);
        }
        public int[] Ints(string section, string key) { return Get(section, key).Split(',').Where(s => s.Trim().Length > 0).Select(s => int.Parse(s.Trim(), CultureInfo.InvariantCulture)).ToArray(); }
    }
    public sealed class AxisConfig
    {
        public int Id;
        public bool Enabled;
        public double Pitch, Stroke, Zero, Speed;
        public int Accel, Decel;
    }
    public sealed class BusConfig { public string Port; public int[] Axes; }
    public sealed class Configuration
    {
        public readonly string Root;
        public readonly Ini Ini;
        public readonly AxisConfig[] Axes;
        public readonly BusConfig[] Buses;
        public readonly int RawMax;
        public Configuration(string root)
        {
            Root = Path.GetFullPath(root);
            Ini = new Ini(Path.Combine(Root, "INI", "SimHub2SimRig.ini"));
            int count = Ini.Int("rig", "maximum_axes");
            if (count != 7) throw new InvalidDataException("Seven axes are required by the recording and geometry contracts");
            Axes = new AxisConfig[count];
            var sections = new Dictionary<int, string>();
            foreach (string section in Ini.Sections.Where(s => s.StartsWith("axes_")))
                foreach (int axis in Ini.Ints(section, "axes"))
                {
                    Require(axis >= 1 && axis <= count && !sections.ContainsKey(axis), "Invalid/duplicate grouped axis");
                    sections.Add(axis, section);
                }
            for (int i = 1; i <= count; i++)
            {
                string section = "axis_" + i;
                if (Ini.Has(section)) { Require(!sections.ContainsKey(i), "Individual and grouped axis parameters overlap"); sections[i] = section; }
                Require(sections.ContainsKey(i), "Missing axis " + i);
                section = sections[i];
                var a = new AxisConfig { Id = i, Enabled = Ini.Bool(section, "enabled"), Pitch = Ini.Number(section, "spindle_pitch_mm"), Stroke = Ini.Number(section, "stroke_mm"), Zero = Ini.Number(section, "zero_offset_mm"), Speed = Ini.Number(section, "speed_mm_s"), Accel = Ini.Int(section, "accTime"), Decel = Ini.Int(section, "decTime") };
                Require(a.Pitch > 0 && a.Stroke > 0 && a.Speed > 0 && a.Accel >= 100 && a.Decel >= 100, "Invalid axis parameters: " + i);
                Axes[i - 1] = a;
            }
            RawMax = Ini.Int("motion_limits", "raw_position_max");
            var buses = new List<BusConfig>();
            var ports = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var routed = new HashSet<int>();
            for (int i = 1; i <= Ini.Int("modbus", "connection_count"); i++)
            {
                string section = "modbus_connection_" + i;
                string port = Ini.Get(section, "port");
                int[] axes = Ini.Ints(section, "axes");
                Require(port.Length > 0 && ports.Add(port) && axes.Length > 0, "Invalid/duplicate COM port");
                foreach (int axis in axes) Require(axis >= 1 && axis <= count && routed.Add(axis), "Invalid/duplicate Modbus axis");
                int[] active = axes.Where(a => Axes[a - 1].Enabled).ToArray();
                if (active.Length > 0) buses.Add(new BusConfig { Port = port, Axes = active });
            }
            Require(Axes.Where(a => a.Enabled).All(a => routed.Contains(a.Id)), "Enabled axis missing Modbus route");
            Require(buses.Count > 0, "No enabled Modbus buses");
            Buses = buses.ToArray();
            Require(Ini.Int("simhub", "position_count") == count && RawMax > 0, "Invalid position count/range");
            Require(Ini.Int("simhub", "position_min") < Ini.Int("simhub", "position_max"), "Invalid SimHub range");
            Require(Ini.Int("simhub", "udp_port") >= 1 && Ini.Int("simhub", "udp_port") <= 65535, "Invalid UDP port");
            Require(Ini.Number("motion_limits", "minimum_mm") < Ini.Number("motion_limits", "maximum_mm"), "Invalid motion limits");
            int hf = Ini.Int("control", "hub_axis_from"), ht = Ini.Int("control", "hub_axis_to");
            Require(hf == 4 && ht == 7, "Hub geometry requires axes 4..7");
            foreach (var pair in new[] { "modbus:baud", "modbus:serial_timeout_s", "modbus:inter_frame_delay_s", "modbus:response_poll_delay_s", "modbus:queue_drain_timeout_s", "modbus:task_wait_timeout_s", "modbus:worker_join_timeout_s", "simhub:sender_cycle_s", "simhub:disconnect_timeout_s", "simulation:simulation_interval_s", "control:actual_position_poll_interval_s", "control:axis_wait_timeout_s", "control:homing_wait_timeout_s", "control:wait_poll_interval_s", "a6:operation_timeout_s", "a6:poll_interval_s", "motion_limits:reference_spindle_pitch_mm", "motion_limits:reference_app_units_per_mm", "rig:DistanceFrontDrivesLeft2Right", "rig:DistanceRearDrivesLeft2Right", "rig:DistanceFront2RearDrives", "rig:CenterOfGravityHeight", "leveling:load_rate_units_per_percent", "grease:position_poll_interval_s", "grease:move_timeout_s", "ui:refresh_ms" })
            { string[] p = pair.Split(':'); Require(Ini.Number(p[0], p[1]) > 0, "Must be positive: " + pair); }
            Require(Ini.Int("leveling", "load_rate_samples") > 0 && Ini.Int("leveling", "verify_batches") > 0 && Ini.Int("leveling", "settle_checks") > 0, "Invalid leveling sample counts");
            Require(Ini.Ints("leveling", "offset_axes").SequenceEqual(new[] { 4, 5, 6, 7 }), "Leveling requires four hub axes");
            foreach (string name in new[] { "coarse", "medium", "fine" })
            {
                string s = "leveling_stage_" + name;
                Require(Ini.Int(s, "max_iterations") > 0 && Ini.Number(s, "step_mm") > 0, "Invalid leveling stage");
                foreach (string key in new[] { "filter_alpha", "raw_weight" }) Require(Ini.Number(s, key) >= 0 && Ini.Number(s, key) <= 1, "Invalid leveling weight");
            }
            Require(Ini.Number("grease", "warning_after_operating_hours") > 0 && Ini.Number("grease", "warning_after_operating_hours") < Ini.Number("grease", "alarm_after_operating_hours"), "Invalid grease limits");
        }
        static void Require(bool condition, string message) { if (!condition) throw new InvalidDataException(message); }
        public string PathFor(string relative) { return Path.Combine(Root, relative); }
        public int[] Enabled(int first = 1, int last = 7) { return Axes.Where(a => a.Enabled && a.Id >= first && a.Id <= last).Select(a => a.Id).ToArray(); }
        public double UnitsPerMm(int axis) { return Ini.Number("motion_limits", "reference_app_units_per_mm") * Ini.Number("motion_limits", "reference_spindle_pitch_mm") / Axes[axis - 1].Pitch; }
        public int Units(int axis, double mm) { return checked((int)Math.Round(mm * UnitsPerMm(axis), MidpointRounding.ToEven)); }
        public int PitchRpm(int axis, int rpm) { return (int)(rpm * Ini.Number("motion_limits", "reference_spindle_pitch_mm") / Axes[axis - 1].Pitch); }
        public int SpeedRpm(int axis, double speed) { return (int)(speed * 60.0 / Axes[axis - 1].Pitch); }
        public static string FindRoot(string[] args)
        {
            int at = Array.IndexOf(args, "--root");
            if (at >= 0) { if (at + 1 >= args.Length) throw new ArgumentException("--root needs a directory"); return Path.GetFullPath(args[at + 1]); }
            var dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            while (dir != null) { if (File.Exists(Path.Combine(dir.FullName, "INI", "SimHub2SimRig.ini"))) return dir.FullName; dir = dir.Parent; }
            throw new DirectoryNotFoundException("INI/SimHub2SimRig.ini not found; pass --root <directory>");
        }
    }
}
