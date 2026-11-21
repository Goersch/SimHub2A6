using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static string[] WithAdditionalInitParameters(string[] reference)
        {
            // The Python reference predates the C# axis-group parameter writes.
            // Keep every reference frame and insert only the new expected commands.
            var expected = new List<string>();
            foreach (string frame in reference)
            {
                if (frame.Substring(2, 6) == "060022")
                {
                    int axis = int.Parse(frame.Substring(0, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                    expected.Add(Hex(Rtu.Write(axis, 0x0006, axis <= 3 ? 1 : 2)));
                    expected.Add(frame);
                    if (axis >= 4 && axis <= 7)
                    {
                        expected.Add(Hex(Rtu.Write(axis, 0x0113, 1)));
                        expected.Add(Hex(Rtu.Write(axis, 0x0114, 100)));
                    }
                }
                else expected.Add(frame);
            }
            return expected.ToArray();
        }
        static void InitializationSettings()
        {
            string directory = Prepare(root), path = Path.Combine(directory, "INI", Configuration.FileName);
            string original = File.ReadAllText(path);
            var values = new Dictionary<string, int> {
                { "c00_22", 0 }, { "c03_02", 123456 }, { "c03_04", 654321 },
                { "c04_01", 0 }, { "c04_05", 0 }, { "c04_08", 5 }, { "c04_12", 123 },
                { "c04_14", 20 }, { "c04_18", 24 }, { "c04_1c", 4 },
                { "c05_0a", 45678 }, { "c05_0c", 987 }, { "c10_00", 1 },
                { "c10_01", -1 }, { "c10_04", 12345 }, { "c10_06", 54321 }
            };
            string text = original;
            foreach (var pair in values)
                text = System.Text.RegularExpressions.Regex.Replace(text, @"(?m)^" + pair.Key + @" = [^\r\n]+", pair.Key + " = " + pair.Value.ToString(CultureInfo.InvariantCulture));
            text = text.Replace("c00_06_axes_1_3 = 1", "c00_06_axes_1_3 = 7").Replace("c00_06_axes_4_7 = 2", "c00_06_axes_4_7 = 9")
                .Replace("c01_13_axes_4_7 = 1", "c01_13_axes_4_7 = 2").Replace("c01_14_axes_4_7 = 100", "c01_14_axes_4_7 = 250")
                .Replace("parameter_delay_s = 0.2", "parameter_delay_s = 0.03").Replace("save_delay_s = 0.2", "save_delay_s = 0.04").Replace("restart_delay_s = 2.0", "restart_delay_s = 0.05");
            File.WriteAllText(path, text);
            var settings = new Configuration(directory);
            foreach (int axis in settings.Enabled())
            foreach (bool save in new[] { false, true })
                foreach (bool high in new[] { false, true })
                    using (var bus = new Modbus(settings))
                    {
                        bus.Connect(b => {
                            var wire = new VirtualWire(b.Axes, high);
                            foreach (int a in b.Axes)
                            {
                                wire.Set(a, 0x0401, save ? 1 : 0);
                                wire.Set(a, 0x0113, 3); wire.Set(a, 0x0114, 321);
                            }
                            return wire;
                        });
                        var delays = new List<double>();
                        var motion = new MotionController(settings, bus, delays.Add);
                        motion.Initialize(axis);
                        Equal(axis <= 3 ? 7 : 9, bus.Read(axis, 0x0006)[0], "INIT uses the configured C00.06 value for axis " + axis);
                        Equal(axis <= 3 ? 3 : 2, bus.Read(axis, 0x0113)[0], "INIT changes C01.13 only on axes 4..7, axis " + axis);
                        Equal(axis <= 3 ? 321 : 250, bus.Read(axis, 0x0114)[0], "INIT uses raw 0.1-percent units only on axes 4..7, axis " + axis);
                        foreach (var pair in values)
                        {
                            int address = int.Parse(pair.Key.Substring(1).Replace("_", ""), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                            bool wide = new[] { 0x0302, 0x0304, 0x050a, 0x1004, 0x1006 }.Contains(address);
                            long actual = wide ? bus.ReadU32(axis, address) : bus.Read(axis, address)[0];
                            Equal(wide ? (long)pair.Value : pair.Value & 65535L, actual, "Configured INIT register " + pair.Key);
                        }
                        Equal(save ? 1 : 0, bus.Read(axis, 0x0a05)[0], "Saving compares the configured C04.01 target with the old register value");
                        Check(delays.SequenceEqual(save ? new[] { .03, .04, .05 } : new[] { .03 }), "Configured INIT delays and conditional save waits");
                        Equal(0, bus.Read(axis, 0x0411)[0], "INIT keeps servo off");
                        Equal((long)settings.Units(axis, settings.Ini.Number("motion_limits", "maximum_mm")), bus.ReadU32(axis, 0x0e00, true), "Upper limit still uses axis conversion");
                        Equal((long)settings.Units(axis, settings.Ini.Number("motion_limits", "minimum_mm")), bus.ReadU32(axis, 0x0e03, true), "Lower limit still uses signed axis conversion");
                        Equal((long)settings.Units(axis, settings.Ini.Number("motion_limits", "homing_mm")), bus.ReadU32(axis, 0x100b, true), "Homing offset still uses axis conversion");
                        motion.StartHoming(axis, true, true, false);
                        Equal(1, bus.Read(axis, 0x1000)[0], "Homing preserves configured trigger mode");
                        Equal((long)settings.Ini.Int("a6", "homing_accel_ms"), bus.ReadU32(axis, 0x1004), "Active homing uses its dedicated acceleration");
                        Equal((long)settings.Ini.Int("a6", "homing_decel_ms"), bus.ReadU32(axis, 0x1006), "Active homing uses its dedicated deceleration");
                    }
            foreach (var invalid in new[] {
                new[] { "c00_06_axes_1_3 = 1", "c00_06_axes_1_3 = -1" },
                new[] { "c00_06_axes_4_7 = 2", "c00_06_axes_4_7 = 12001" },
                new[] { "c01_13_axes_4_7 = 1", "c01_13_axes_4_7 = -1" },
                new[] { "c01_13_axes_4_7 = 1", "c01_13_axes_4_7 = 5" },
                new[] { "c01_14_axes_4_7 = 100", "c01_14_axes_4_7 = -1" },
                new[] { "c01_14_axes_4_7 = 100", "c01_14_axes_4_7 = 2001" },
                new[] { "c03_02 = 131072", "c03_02 = 0" },
                new[] { "c04_01 = 1", "c04_01 = 2" },
                new[] { "c04_12 = 1", "c04_12 = 65536" },
                new[] { "c05_0c = 10", "c05_0c = 4" },
                new[] { "c10_01 = 17", "c10_01 = -3" },
                new[] { "c10_04 = 100", "c10_04 = 3600001" },
                new[] { "save_delay_s = 0.2", "save_delay_s = -0.1" },
                new[] { "save_delay_s = 0.2", "save_delay_s = NaN" }
            })
            {
                File.WriteAllText(path, original.Replace(invalid[0], invalid[1]));
                Throws<InvalidDataException>(() => new Configuration(directory), "Reject invalid INIT setting before connecting: " + invalid[1]);
            }
        }
    }
}
