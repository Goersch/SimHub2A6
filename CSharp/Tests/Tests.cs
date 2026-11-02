using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public sealed class FrameCase { public string Operation; public int Axis, Address; public int[] Values; public string Hex; }
    public sealed class ConversionCase { public int Axis, Units; public double Mm; }
    public sealed class TargetCase { public int Axis, Raw; public double Offset, Mm; }
    public sealed class GeometryCase { public double[] Positions, Ground, Loads; }
    public sealed class DynamicsCase { public double Target, Position, Velocity, Speed, Dt, ExpectedPosition, ExpectedVelocity; public int Accel, Decel; }
    public sealed class Scenario { public string Name; public int Axis; public bool High; public Dictionary<string, string[]> Frames; }
    public sealed class UiStateCase { public int Reason; public string Text; }
    public sealed class Oracle { public FrameCase[] Frames; public ConversionCase[] Conversions; public TargetCase[] Targets; public GeometryCase[] Geometry; public DynamicsCase[] Dynamics; public Scenario[] Scenarios, Lifecycle, Leveling; public UiStateCase[] UiStates; }
    public static partial class TestProgram
    {
        static int checks, failures;
        static readonly List<object> results = new List<object>();
        static Configuration config;
        static string root, testRoot, binaryDirectory;
        static void Check(bool condition, string detail) { Interlocked.Increment(ref checks); if (!condition) throw new Exception(detail); }
        static void Equal<T>(T expected, T actual, string detail) { Check(EqualityComparer<T>.Default.Equals(expected, actual), detail + ": expected " + expected + ", got " + actual); }
        static void Near(double expected, double actual, string detail) { Check(Math.Abs(expected - actual) < 1e-8, detail + ": " + expected.ToString("R", CultureInfo.InvariantCulture) + " != " + actual.ToString("R", CultureInfo.InvariantCulture)); }
        static void Throws<T>(Action action, string detail) where T : Exception
        { try { action(); } catch (T) { checks++; return; } throw new Exception("Expected " + typeof(T).Name + ": " + detail); }
        static string Hex(byte[] data) { return BitConverter.ToString(data).Replace("-", ""); }
        static void Run(string name, Action action)
        {
            int before = checks; var watch = Stopwatch.StartNew();
            try { action(); Console.WriteLine("PASS " + name + " (" + (checks - before) + " checks)"); results.Add(new { Name = name, Passed = true, Checks = checks - before, Seconds = watch.Elapsed.TotalSeconds }); }
            catch (Exception e) { failures++; Console.WriteLine("FAIL " + name + "\n" + e); results.Add(new { Name = name, Passed = false, Error = e.ToString(), Checks = checks - before, Seconds = watch.Elapsed.TotalSeconds }); }
        }
        static void Until(Func<bool> condition, string message, double seconds = 15)
        { double end = Timing.Now + seconds; while (Timing.Now < end) { if (condition()) { checks++; return; } Thread.Sleep(20); } throw new TimeoutException(message); }
        static Dictionary<string, VirtualWire> Wires(Modbus bus, bool high = false, bool save = false)
        {
            var wires = new Dictionary<string, VirtualWire>();
            bus.Connect(b => { var wire = new VirtualWire(b.Axes, high); if (save) foreach (int a in b.Axes) wire.Set(a, 0x0401, 0); wires[b.Port] = wire; return wire; }); return wires;
        }
        static void Trace(Scenario scenario)
        {
            using (var bus = new Modbus(config))
            {
                var wires = Wires(bus, scenario.High, scenario.Name == "initialize-save");
                var motion = new MotionController(config, bus, _ => { }); int axis = scenario.Axis;
                switch (scenario.Name)
                {
                    case "initialize": case "initialize-save": motion.Initialize(axis); break;
                    case "start": motion.PlannerStart(axis, -30.1235); break;
                    case "parameters":
                        int[][] p = { new[] { 100, 300, 400 }, new[] { 100, 300, 400 }, new[] { 100, 500, 400 }, new[] { 100, 500, 600 }, new[] { 200, 700, 800 }, new[] { -1, 1, 2 } };
                        foreach (int[] v in p) motion.Parameters(axis, v[0], v[1], v[2], true); motion.Trigger(); break;
                    case "positions":
                        foreach (double mm in new[] { -100, -30.1235, -.0005, 0, .0005, .0015, 12.5, 12.5, 100 }) motion.Position(axis, mm, false, true);
                        motion.Trigger(); motion.Position(axis, 100); break;
                    case "home": motion.StartHoming(axis, true, true, false); motion.TriggerHoming(0); Check(motion.HomingComplete(axis), "Homing complete"); break;
                    case "stop": motion.PlannerStop(axis); break;
                    case "move": Check(motion.MoveToMm(axis, -12.5), "Move completed"); break;
                    default: throw new Exception("Unknown scenario");
                }
                foreach (var pair in scenario.Frames)
                {
                    string[] actual = wires[pair.Key].Frames.Select(Hex).ToArray();
                    Equal(pair.Value.Length, actual.Length, scenario.Name + " axis " + axis + " " + pair.Key + " length");
                    for (int i = 0; i < actual.Length; i++) Equal(pair.Value[i], actual[i], scenario.Name + " axis " + axis + " high=" + scenario.High + " frame " + i + " " + pair.Key);
                }
            }
        }
        static void TransportErrors()
        {
            using (var bus = new Modbus(config))
            {
                var wires = Wires(bus); var wire = wires[config.Buses.First(b => b.Axes.Contains(1)).Port];
                int attempts = 0;
                wire.Override = f => {
                    if (f[1] != 6) return null;
                    attempts++; if (attempts > 3) return null;
                    byte[] corrupt = (byte[])f.Clone(); corrupt[corrupt.Length - 1] ^= 255; return corrupt;
                };
                bus.Write(1, 0x1100, 3); Equal(4, attempts, "CRC retry count");
                attempts = 0; wire.Override = f => { attempts++; byte[] rx = (byte[])f.Clone(); rx[rx.Length - 1] ^= 255; return rx; };
                Throws<CrcException>(() => bus.Write(1, 0x1100, 3), "Repeated CRC error"); Equal(4, attempts, "Retry exhaustion");
                wire.Override = f => Rtu.Seal(new byte[] { 1, 0x86, 4 });
                Throws<IOException>(() => bus.Write(1, 0x1100, 3), "Standard exception");
                wire.Override = f => Rtu.Seal(new byte[] { 1, 0x90, 0, 0, 0, 9 });
                Throws<IOException>(() => bus.Write(1, 0x1100, 3), "Vendor error");
                wire.Override = f => new byte[0]; bus.Write(1, 0x1100, 3);
                Throws<IOException>(() => bus.Read(1, 0x4007), "Read without response");
                wire.Override = f => Rtu.Seal(new byte[] { 2, 3, 2, 0, 1 });
                Throws<IOException>(() => bus.Read(1, 0x4007), "Wrong slave");
                wire.Override = null;
                bus.U32(1, 0x2000, -123456789); Equal(-123456789L, bus.ReadU32(1, 0x2000, true), "Signed U32");
                bus.F32(1, 0x2002, -123.25f); Equal(-123.25f, bus.ReadF32(1, 0x2002), "Float32");
            }
        }
        static void Compare(Scenario scenario, Dictionary<string, VirtualWire> wires)
        {
            foreach (var pair in scenario.Frames)
            {
                string[] actual = wires[pair.Key].Frames.Select(Hex).ToArray();
                Equal(pair.Value.Length, actual.Length, scenario.Name + " " + pair.Key + " frame count");
                for (int i = 0; i < actual.Length; i++) Equal(pair.Value[i], actual[i], scenario.Name + " " + pair.Key + " frame " + i);
            }
        }
        static void LifecycleTrace(Scenario scenario)
        {
            var c = new Configuration(Prepare(root)); var wires = new Dictionary<string, VirtualWire>();
            var engine = new Engine(c, true, b => { var w = new VirtualWire(b.Axes, scenario.High); wires[b.Port] = w; return w; });
            try { engine.Initialize(); } finally { engine.Dispose(); }
            Compare(scenario, wires);
        }
        static void LevelingTrace(Scenario scenario)
        {
            var c = new Configuration(Prepare(root)); var wires = new Dictionary<string, VirtualWire>(); var offsets = new double[7];
            var engine = new Engine(c, true);
            engine.Bus.Connect(b => {
                var w = new VirtualWire(b.Axes, scenario.High); wires[b.Port] = w;
                w.Override = f => {
                    int axis = f[0], fc = f[1], address = f[2] << 8 | f[3];
                    if (fc == 16 && address == 0x1106)
                    {
                        int word0 = f[7] << 8 | f[8], word1 = f[9] << 8 | f[10];
                        int value = unchecked((int)(((uint)(scenario.High ? word0 : word1) << 16) | (uint)(scenario.High ? word1 : word0)));
                        offsets[axis - 1] = value / c.UnitsPerMm(axis) - c.Axes[axis - 1].Zero;
                    }
                    if (fc == 3 && address == 0x4007 && axis >= 4)
                    { int load = (int)Math.Round(new[] { 100, 70, 80, 90 }[axis - 4] + offsets[axis - 1] * 100, MidpointRounding.ToEven); w.Set(axis, address, load); }
                    return null;
                };
                return w;
            });
            try { new Leveler(engine, _ => { }).Run(); Compare(scenario, wires); }
            finally { engine.Bus.Dispose(); }
        }
        static void Barrier()
        {
            using (var bus = new Modbus(config))
            {
                var wires = Wires(bus);
                foreach (VirtualWire w in wires.Values) w.Override = f => { if (f[1] == 16) Thread.Sleep(30); return null; };
                foreach (int a in config.Enabled()) bus.U32(a, 0x1106, 1000 * a, true, true);
                new MotionController(config, bus, _ => { }).Trigger();
                foreach (BusConfig bc in config.Buses)
                {
                    byte[][] frames = wires[bc.Port].Frames.ToArray();
                    int edge = Array.FindIndex(frames, f => f[0] == 0);
                    Equal(bc.Axes.Length, frames.Take(edge).Count(f => f[1] == 16), "All target writes before trigger: " + bc.Port);
                    Equal(Hex(Rtu.Write(0, 0x0415, 1)), Hex(frames[edge]), "Rising broadcast");
                    Equal(Hex(Rtu.Write(0, 0x0415, 0)), Hex(frames[edge + 1]), "Falling broadcast");
                }
                var bad = wires[config.Buses.First(b => b.Axes.Contains(1)).Port];
                bad.Override = f => { if (f[1] != 16) return null; var rx = Rtu.Seal(f.Take(6)); rx[rx.Length - 1] ^= 255; return rx; };
                int before = wires.Values.Sum(w => w.Frames.Count(f => f[0] == 0));
                bus.U32(1, 0x1106, 1234, true, true);
                Throws<CrcException>(() => new MotionController(config, bus).Trigger(), "Queued error must suppress broadcast");
                Equal(before, wires.Values.Sum(w => w.Frames.Count(f => f[0] == 0)), "No trigger on failed target");
                bad.Override = null;
            }
        }
        static void CsvAndPersistence()
        {
            var recordings = new Recordings(config); Directory.CreateDirectory(config.PathFor("SimHubData"));
            string header = "Timestamp;" + string.Join(";", Enumerable.Range(1, 7).Select(a => "Value" + a));
            string path = config.PathFor("SimHubData/legacy.csv");
            File.WriteAllText(path, header + "\n2026-11-02 12:00:00.000;0;65535;131071;0;65535;131071;0\ninvalid\n2026-11-02 12:00:02.000;131071;65535;0;131071;65535;0;131071\n");
            Sample[] samples = recordings.Read("legacy.csv").ToArray(); Equal(2, samples.Length, "Legacy rows"); Near(-100, samples[0].Target[0], "Legacy conversion"); Near(2, samples[1].Seconds, "CSV timestamps");
            Throws<ArgumentException>(() => recordings.Read("../INI/SimHub2SimRig.ini").ToArray(), "Path traversal");
            Throws<ArgumentException>(() => recordings.Read(path).ToArray(), "Absolute path");
            using (var recorder = new Recorder(config))
            {
                recorder.ReadActual(true); recorder.SetActual(1, 12.5); recorder.Stage(1, 20, 400, 100, 400, 78642); recorder.Trigger(); recorder.SetRunning(true); recorder.Tick(.025); recorder.SetRunning(false);
                Sample sample = recordings.Read(recorder.CurrentFile).Single(); Near(20 - config.Axes[0].Zero, sample.Target[0], "Modern target"); Near(12.5, sample.Actual[0], "Actual feedback");
                string[] columns = File.ReadAllLines(config.PathFor("SimHubData/" + recorder.CurrentFile))[0].TrimStart('\ufeff').Split(';'); Equal(58, columns.Length, "Recording schema");
                Check(columns.Contains("ActualPositionAgeMs7") && columns.Contains("GroundLoad7"), "Recording metadata");
                recorder.SaveLoads(new[] { 100.0, 200, 300, 400 });
                Near(-332, recorder.Center[0], "Load center front/rear"); Near(0, recorder.Center[1], "Load center lateral");
                Throws<InvalidDataException>(() => recorder.SaveLoads(new[] { 0.0, 1, 2, 3 }), "Invalid calibration");
            }
            using (var recorder = new Recorder(config)) Near(-332, recorder.Center[0], "Persistent calibration");
            string big = config.PathFor("SimHubData/large.csv");
            using (var writer = new StreamWriter(big))
            {
                writer.WriteLine(header);
                for (int i = 0; i < 25000; i++) writer.WriteLine(new DateTime(2026, 11, 2).AddMilliseconds(i * 10).ToString("yyyy-MM-dd HH:mm:ss.fff") + ";0;0;0;0;0;0;0");
            }
            Sample[] preview = recordings.Preview("large.csv"); Check(preview.Length <= 6000, "Preview memory bound"); Near(0, preview[0].Seconds, "Preview start"); Near(249.99, preview.Last().Seconds, "Preview end");
            for (int a = 1; a <= 7; a++) for (int raw = 0; raw <= config.RawMax; raw += 997)
            {
                double offset = -1.2345, mm = config.Axes[a - 1].Stroke / config.RawMax * raw - config.Axes[a - 1].Stroke / 2 + offset;
                Equal(raw, Playback.RawFromTarget(config, a, mm, offset), "Playback zero/leveling inverse");
            }
        }
        static void Integration()
        {
            // Shorten only test settling/measurement settings in the isolated INI.
            string iniPath = config.PathFor("INI/SimHub2SimRig.ini"); string text = File.ReadAllText(iniPath);
            text = text.Replace("load_rate_samples = 11", "load_rate_samples = 1").Replace("verify_batches = 5", "verify_batches = 1").Replace("settle_time_s = 2.0", "settle_time_s = 0.01").Replace("settle_check_delay_s = 0.4", "settle_check_delay_s = 0.01");
            File.WriteAllText(iniPath, text); var c = new Configuration(testRoot);
            var wires = new Dictionary<string, VirtualWire>();
            using (var engine = new Engine(c, true, b => { var w = new VirtualWire(b.Axes); lock (wires) wires[b.Port] = w; return w; }))
            using (var server = new PipeServer(engine, "SimHub2SimRig.Tests." + Guid.NewGuid().ToString("N")))
            {
                // Get the test pipe name via an independent known server below.
                engine.Start(); Until(() => engine.Snapshot().Initialized && !engine.Snapshot().Busy, "Engine startup", 35);
                string pipeName = "SimHub2SimRig.Tests." + Guid.NewGuid().ToString("N");
                using (var actualServer = new PipeServer(engine, pipeName))
                {
                    actualServer.Start(); var client = new ComClient(pipeName);
                    Check(client.Send(new Request { Operation = "status" }).Status.Initialized, "IPC status");
                    Check(!client.Send(new Request { Operation = "raw.write" }).Ok, "No raw register commands in HMI protocol");
                    Check(!client.Send(new Request { Operation = "move", FirstAxis = 1, LastAxis = 1, Position = 0 }).Ok, "Com enforces maintenance ownership");
                    Check(!client.Send(new Request { Operation = "mode", Mode = "invalid" }).Ok, "Mode validation");
                    Task[] polls = Enumerable.Range(0, 4).Select(_ => Task.Run(() => { for (int i = 0; i < 10; i++) Check(client.Send(new Request { Operation = "status" }).Ok, "Concurrent polling"); })).ToArray(); Task.WaitAll(polls);
                    using (var udp = new UdpClient())
                    {
                        var endpoint = new IPEndPoint(IPAddress.Loopback, c.Ini.Int("simhub", "udp_port"));
                        byte[] packet = System.Text.Encoding.ASCII.GetBytes("POSITIONS 65535 65535 65535 65535 65535 65535 65535"); udp.Send(packet, packet.Length, endpoint);
                        Until(() => engine.Snapshot().GameRunning, "UDP starts recording");
                        Check(!client.Send(new Request { Operation = "maintenance.open" }).Ok, "Maintenance blocked by active UDP");
                        engine.Dispatch("POSITIONS -1 1 2 3 4 5 6;POSITIONS 1 2");
                        byte[] end = System.Text.Encoding.ASCII.GetBytes("END"); udp.Send(end, end.Length, endpoint); Until(() => !engine.Snapshot().GameRunning, "END stops recording");
                        Until(() => !engine.Snapshot().Connected, "UDP disconnect timeout", 6);
                    }
                    Check(client.Send(new Request { Operation = "leveling.start" }).Ok, "Leveling request");
                    Until(() => !engine.Snapshot().Busy, "Leveling completes", 10); Check(string.IsNullOrEmpty(engine.Snapshot().LastError), "Leveling successful");
                    Check(File.Exists(c.PathFor("INI/leveling_offsets.json")), "Leveling persistence");
                    Check(client.Send(new Request { Operation = "loads.save" }).Ok, "Load capture request"); Until(() => !engine.Snapshot().Busy, "Load capture finishes");
                    Check(client.Send(new Request { Operation = "maintenance.open" }).Ok, "Maintenance open"); Until(() => !engine.Snapshot().Busy, "Maintenance ready");
                    engine.Dispatch("START;POSITIONS 1 1 1 1 1 1 1"); Check(!engine.Snapshot().GameRunning, "UDP blocked during maintenance");
                    Check(!client.Send(new Request { Operation = "analysis.open" }).Ok, "Exclusive analysis ownership");
                    Check(!client.Send(new Request { Operation = "move", FirstAxis = 1, LastAxis = 7, Position = 0 }).Ok, "Invalid group rejected");
                    Check(client.Send(new Request { Operation = "home", FirstAxis = 4, LastAxis = 7 }).Ok, "Shared homing"); Until(() => !engine.Snapshot().Busy, "Homing finished");
                    Check(client.Send(new Request { Operation = "maintenance.open" }).Ok, "Reattach to existing maintenance without changing planner ownership");
                    Check(client.Send(new Request { Operation = "move", FirstAxis = 4, LastAxis = 7, Position = 65535 }).Ok, "Hub move"); Until(() => !engine.Snapshot().Busy, "Hub move finished");
                    Check(string.IsNullOrEmpty(engine.Snapshot().LastError), "Maintenance movement successful");
                    foreach (int axis in c.Enabled(4, 7)) Equal(0, engine.Motion.ServoStatus(axis), "Hub brakes after movement");
                    Check(client.Send(new Request { Operation = "grease.start", FirstAxis = 1, LastAxis = 1 }).Ok, "Grease starts"); Until(() => engine.Snapshot().Greasing, "Greasing active");
                    Check(client.Send(new Request { Operation = "grease.stop" }).Ok, "Grease stop"); Until(() => !engine.Snapshot().Busy, "Grease centered and stopped");
                    Check(client.Send(new Request { Operation = "grease.reset", FirstAxis = 1, LastAxis = 1 }).Ok, "Grease reset"); Near(0, engine.Snapshot().Grease["1"].playtimeMinutes, "Reset counter");
                    Check(client.Send(new Request { Operation = "maintenance.close" }).Ok, "Close maintenance"); Until(() => !engine.Snapshot().Maintenance && !engine.Snapshot().Busy, "Planners restored");
                    int before = wires.Values.Sum(w => w.Frames.Count(f => f[1] == 16 && f[2] == 0x11 && f[3] == 6));
                    Thread.Sleep(120); Equal(before, wires.Values.Sum(w => w.Frames.Count(f => f[1] == 16 && f[2] == 0x11 && f[3] == 6)), "No stale SimHub targets after maintenance");
                    Check(client.Send(new Request { Operation = "analysis.open" }).Ok, "Analysis opens");
                    string path = c.PathFor("SimHubData/playback.csv");
                    File.WriteAllText(path, "Timestamp;" + string.Join(";", Enumerable.Range(1, 7).Select(a => "TargetPosition" + a)) + "\n2026-11-02 12:00:00.000;0;0;30;0;0;0;0\n2026-11-02 12:00:02.000;0;0;50;0;0;0;0\n2026-11-02 12:00:03.000;0;0;60;0;0;0;0\n");
                    Check(!client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 4 } }).Ok, "Partial lift playback rejected");
                    Check(client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 3 } }).Ok, "Playback selected axis 3");
                    Until(() => Math.Abs(engine.Previous[2] - 30) < .01, "Playback maps selected axis correctly");
                    Check(client.Send(new Request { Operation = "playback.pause" }).Ok, "Pause"); Thread.Sleep(2200);
                    Check(client.Send(new Request { Operation = "analysis.open" }).Ok, "Reattach to paused analysis");
                    double firstTarget = engine.TargetMm(3, Playback.RawFromTarget(c, 3, 30, engine.Offsets[2]));
                    Equal("paused", engine.Snapshot().Playback, "Paused during sample delay"); Near(firstTarget, engine.Previous[2], "No position during pause");
                    Check(!engine.Snapshot().GameRunning, "Playback does not record");
                    Check(client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 3 } }).Ok, "Resume");
                    Thread.Sleep(200); Near(firstTarget, engine.Previous[2], "Pause time excluded from deadline");
                    Check(client.Send(new Request { Operation = "playback.pause" }).Ok, "Pause for seeking");
                    Check(client.Send(new Request { Operation = "playback.seek", Seconds = 2 }).Ok, "Seek paused"); Near(2, engine.Snapshot().PlaybackSeconds, "Seek position");
                    Check(client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 3 } }).Ok, "Play from seek");
                    Until(() => Math.Abs(engine.Previous[2] - 50) < .01, "Seek applies correct sample");
                    Check(client.Send(new Request { Operation = "playback.stop" }).Ok, "Stop and center"); Until(() => !engine.Snapshot().Busy, "Center after stop");
                    Check(client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 3 } }).Ok, "Replay starts at selected seek point");
                    Until(() => Math.Abs(engine.Previous[2] - 50) < .01, "Replay first sample after seek");
                    Until(() => engine.Snapshot().Playback == "stopped", "Natural playback completion and center", 6);
                    Check(client.Send(new Request { Operation = "playback.play", FileName = "playback.csv", Axes = new[] { 3 } }).Ok, "Replay after natural completion");
                    Until(() => Math.Abs(engine.Previous[2] - 50) < .01, "Natural completion preserves selected start");
                    Check(client.Send(new Request { Operation = "playback.stop" }).Ok, "Stop replay"); Until(() => !engine.Snapshot().Busy, "Replay stopped");
                    Check(client.Send(new Request { Operation = "analysis.close" }).Ok, "Analysis closes"); Until(() => !engine.Snapshot().Analysis && !engine.Snapshot().Busy, "Analysis released");
                    Check(client.Send(new Request { Operation = "mode", Mode = "actual_positions" }).Ok, "Actual read mode");
                    Thread.Sleep(500); Check(engine.Snapshot().Axes.Any(a => a.Actual != 0), "Actual readings in Com");
                    Check(client.Send(new Request { Operation = "mode", Mode = "center" }).Ok, "Inactive mode centers"); Until(() => !engine.Snapshot().Busy, "Center mode complete");
                    Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can detach"); Until(() => !engine.Snapshot().Busy, "Detached without terminating Com");
                    Check(engine.Snapshot().Initialized, "Com runs without HMI");
                }
            }
        }
        static void DriveStates()
        {
            var context = new SimHub2SimRig.HMI.UiContext(testRoot, "SimHub2SimRig.StateTest." + Guid.NewGuid().ToString("N"));
            var status = new Status { Playback = "stopped" };
            UiStateCase[] expected = Json.Read<Oracle>(Path.Combine(root, "CSharp", "Tests", "reference.json")).UiStates;
            Check(expected != null && expected.Length > 0, "Python state reference is present");
            foreach (UiStateCase item in expected)
                foreach (int servo in new[] { 0, 1, 2 })
                {
                    var axis = new AxisStatus { Axis = 1, Enabled = true, Error = item.Reason, Servo = servo };
                    Equal(item.Text, SimHub2SimRig.HMI.DriveStateView.Text(context, axis, status), "Python U40.43 state " + item.Reason + ", servo " + servo);
                    Check(!SimHub2SimRig.HMI.DriveStateView.IsFault(axis), "Inhibit reason alone is not an alarm");
                }
            foreach (int reason in new[] { 0, 3, 5, 65535 })
            {
                var axis = new AxisStatus { Axis = 1, Enabled = true, Error = reason, Servo = 3 };
                Check(SimHub2SimRig.HMI.DriveStateView.IsFault(axis), "ServoStatus 3 remains a fault");
                Check(SimHub2SimRig.HMI.DriveStateView.Text(context, axis, status).Contains("Drive fault"), "True fault is not masked by reason " + reason);
            }
            status.Leveling = true;
            var hub = new AxisStatus { Axis = 4, Enabled = true, Error = 5, Servo = 1 };
            Equal(context.T("MainDialog", "state_leveling"), SimHub2SimRig.HMI.DriveStateView.Text(context, hub, status), "Leveling overrides normal state");
            status.Leveling = false; status.Greasing = true; status.GreaseFirst = 4; status.GreaseLast = 7; hub.Error = 0;
            Equal(context.T("MainDialog", "state_greasing"), SimHub2SimRig.HMI.DriveStateView.Text(context, hub, status), "Greasing matches Python state precedence");
        }
        static void RenderUi()
        {
            System.Windows.Forms.Application.EnableVisualStyles();
            System.Windows.Forms.Application.SetCompatibleTextRenderingDefault(false);
            using (var chart = new SimHub2SimRig.HMI.SenderIntervalChart(60))
            {
                foreach (double[] history in new[] { new double[0], Enumerable.Repeat(10.0, 25).ToArray(), Enumerable.Repeat(25.0, 6000).ToArray(), new[] { 10.0, 200.0, 10.0 } })
                {
                    chart.SetSamples(history);
                    var area = chart.ChartAreas[0];
                    Near(-60, area.AxisX.Minimum, "Sender x minimum stays fixed as history changes");
                    Near(0, area.AxisX.Maximum, "Sender x maximum stays at now");
                    Near(10, area.AxisX.Interval, "Sender x tick interval stays fixed");
                    Near(0, area.AxisY.Minimum, "Sender y minimum");
                    Near(100, area.AxisY.Maximum, "Sender y maximum is 100 ms");
                    Equal("", area.AxisX.Title, "No s title below sender chart");
                    if (chart.Series[0].Points.Count > 0)
                    {
                        Near(0, chart.Series[0].Points.Last().XValue, "Latest sender point is anchored at now");
                        Check(chart.Series[0].Points.All(p => p.XValue >= -60 && p.XValue <= 0), "Sender history stays inside fixed time window");
                    }
                }
            }
            string destination = Path.Combine(root, "CSharp", "Tests", "screenshots"); Directory.CreateDirectory(destination);
            var context = new SimHub2SimRig.HMI.UiContext(testRoot, "SimHub2SimRig.UiRender." + Guid.NewGuid().ToString("N"));
            var status = new Engine(config, true).Snapshot();
            status.Initialized = true; status.Simulated = true; status.Playback = "stopped"; status.Mode = "simhub";
            status.TriggerIntervals = Enumerable.Range(0, 400).Select(i => 10.0 + 2 * Math.Sin(i * .1)).ToArray();
            foreach (AxisStatus a in status.Axes) { a.Homed = true; a.Servo = 1; a.Error = 5; a.Load = 20 + a.Axis; a.Target = a.Axis * 10 - 40; }
            context.Status = status;
            Action<System.Windows.Forms.Form, string> capture = (form, name) => {
                using (form)
                {
                    // Show off-screen and transparent so child controls obtain
                    // real handles/layout without appearing on the desktop.
                    form.StartPosition = System.Windows.Forms.FormStartPosition.Manual;
                    form.Location = new System.Drawing.Point(-30000, -30000);
                    form.ShowInTaskbar = false; form.Opacity = 0; form.Show(); form.PerformLayout();
                    Check(form.Controls.Cast<System.Windows.Forms.Control>().All(c => c.IsHandleCreated), "Child control handles: " + name);
                    if (form is SimHub2SimRig.HMI.MainForm)
                    {
                        var mainGrid = Descendants(form).OfType<System.Windows.Forms.DataGridView>().Single();
                        Equal(System.Windows.Forms.DataGridViewContentAlignment.MiddleCenter, mainGrid.DefaultCellStyle.Alignment, "Motor values are centered");
                        Equal(System.Windows.Forms.DataGridViewContentAlignment.MiddleCenter, mainGrid.ColumnHeadersDefaultCellStyle.Alignment, "Motor headers are centered");
                        var mainButtons = Descendants(form).OfType<System.Windows.Forms.Button>().ToArray();
                        Action<string, string> below = (upperKey, lowerKey) => {
                            var upper = mainButtons.Single(b => b.Text == context.T("Panels", upperKey));
                            var lower = mainButtons.Single(b => b.Text == context.T("Panels", lowerKey));
                            var upperRect = upper.RectangleToScreen(upper.ClientRectangle);
                            var lowerRect = lower.RectangleToScreen(lower.ClientRectangle);
                            Equal(upperRect.Left, lowerRect.Left, "Buttons share column: " + lowerKey);
                            Check(lowerRect.Top >= upperRect.Bottom, "Button is below previous: " + lowerKey);
                        };
                        below("light_on", "light_off");
                        below("maintenance", "analysis");
                        below("leveling_start", "leveling_stop");
                        below("leveling_stop", "save_load_values");
                        var senderChart = Descendants(form).OfType<SimHub2SimRig.HMI.SenderIntervalChart>().Single();
                        Near(100, senderChart.ChartAreas[0].AxisY.Maximum, "Rendered sender y maximum");
                        Equal("", senderChart.ChartAreas[0].AxisX.Title, "Rendered sender has no x title");
                    }
                    foreach (var button in Descendants(form).OfType<System.Windows.Forms.Button>())
                    {
                        var rectangle = button.RectangleToScreen(button.ClientRectangle);
                        for (var parent = button.Parent; parent != null; parent = parent.Parent)
                            Check(parent.ClientRectangle.Contains(parent.RectangleToClient(rectangle)), "Unclipped button " + name + ": " + button.Text + " in " + parent.GetType().Name + " " + parent.ClientRectangle + " button " + parent.RectangleToClient(rectangle));
                    }
                    using (var bitmap = new System.Drawing.Bitmap(form.Width, form.Height))
                    { form.DrawToBitmap(bitmap, new System.Drawing.Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(Path.Combine(destination, name + ".png")); }
                    Check(form.Controls.Count > 0, "Rendered " + name);
                }
            };
            var main = new SimHub2SimRig.HMI.MainForm(context); for (int i = 0; i < 10; i++) main.UpdateStatus(status);
            var grid = Descendants(main).OfType<System.Windows.Forms.DataGridView>().Single();
            for (int i = 0; i < 7; i++)
            {
                Equal(context.T("Common", "status_ok"), Convert.ToString(grid.Rows[i].Cells[3].Value), "Reason 5 displays OK on axis " + (i + 1));
                Check(grid.Rows[i].DefaultCellStyle.BackColor != System.Drawing.Color.MistyRose, "Reason 5 does not color axis red");
            }
            status.Axes[0].Servo = 3; main.UpdateStatus(status);
            Check(Convert.ToString(grid.Rows[0].Cells[3].Value).Contains("Drive fault"), "Grid displays true servo fault even for reason 5");
            Equal(System.Drawing.Color.MistyRose, grid.Rows[0].DefaultCellStyle.BackColor, "True servo fault is red");
            status.Axes[0].Servo = 1; main.UpdateStatus(status); capture(main, "main");
            main = new SimHub2SimRig.HMI.MainForm(context); main.Size = main.MinimumSize; for (int i = 0; i < 10; i++) main.UpdateStatus(status); capture(main, "main-minimum");
            status.Maintenance = true; var maintenance = new SimHub2SimRig.HMI.MaintenanceForm(context); maintenance.UpdateStatus(status); capture(maintenance, "maintenance");
            maintenance = new SimHub2SimRig.HMI.MaintenanceForm(context); maintenance.Size = maintenance.MinimumSize; maintenance.UpdateStatus(status); capture(maintenance, "maintenance-minimum");
            status.Maintenance = false; status.Analysis = true; var analysis = new SimHub2SimRig.HMI.AnalysisForm(context);
            analysis.SetPreview(Enumerable.Range(0, 300).Select(i => new Sample { Seconds = i * .01, Target = Enumerable.Range(0, 7).Select(a => 25 * Math.Sin(i * .04 + a)).ToArray(), Calculated = new double[7], Actual = new double[7], Ground = new double[7] }).ToArray());
            analysis.UpdateStatus(status); capture(analysis, "analysis");
        }
        static IEnumerable<System.Windows.Forms.Control> Descendants(System.Windows.Forms.Control control)
        {
            foreach (System.Windows.Forms.Control child in control.Controls) { yield return child; foreach (var nested in Descendants(child)) yield return nested; }
        }
        static void ExecutableLifecycle()
        {
            string isolated = Prepare(root), exe = Path.Combine(binaryDirectory, "SimHub2SimRigCom.exe");
            var start = new ProcessStartInfo(exe, "--simulate --root \"" + isolated + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            var output = new System.Text.StringBuilder();
            using (var process = Process.Start(start))
            {
                process.OutputDataReceived += (s, e) => { if (e.Data != null) lock (output) output.AppendLine(e.Data); };
                process.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (output) output.AppendLine(e.Data); };
                process.BeginOutputReadLine(); process.BeginErrorReadLine();
                try
                {
                    var client = new ComClient();
                    Until(() => {
                        if (process.HasExited) throw new Exception("Com EXE failed: " + output);
                        try
                        {
                            Status s = client.Send(new Request { Operation = "status" }).Status;
                            int ownedWorker = WorkerPid(isolated);
                            if (ownedWorker == 0) return false;
                            if (s.ProcessId != ownedWorker || !s.Simulated) throw new InvalidOperationException("Another Com session is already running; test will not control it");
                            return s.Initialized && !s.Busy;
                        }
                        catch (TimeoutException) { return false; }
                    }, "External Com initializes", 35);
                    using (var duplicate = Process.Start(new ProcessStartInfo(exe, "--simulate --root \"" + isolated + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }))
                    { Check(duplicate.WaitForExit(5000), "Duplicate Com exits"); Equal(1, duplicate.ExitCode, "One Com instance per user"); }
                    using (var shutdown = Process.Start(new ProcessStartInfo(exe, "--shutdown --root \"" + isolated + "\"") { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }))
                    { Check(shutdown.WaitForExit(20000), "Shutdown client waits for cleanup"); Equal(0, shutdown.ExitCode, "Shutdown command succeeds"); }
                    Check(process.WaitForExit(5000), "Com process exits after shutdown"); Equal(0, process.ExitCode, "Com clean exit");
                }
                finally { if (!process.HasExited) { process.Kill(); process.WaitForExit(); } }
            }
        }
        static string Prepare(string repository)
        {
            string dir = Path.Combine(repository, "CSharp", "Tests", "work", Guid.NewGuid().ToString("N")); Directory.CreateDirectory(Path.Combine(dir, "INI"));
            string ini = File.ReadAllText(Path.Combine(repository, "INI", "SimHub2SimRig.ini"));
            int port; using (var udp = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0))) port = ((IPEndPoint)udp.Client.LocalEndPoint).Port;
            ini = ini.Replace("udp_port = 9999", "udp_port = " + port);
            File.WriteAllText(Path.Combine(dir, "INI", "SimHub2SimRig.ini"), ini);
            File.Copy(Path.Combine(repository, "INI", "Language.ini"), Path.Combine(dir, "INI", "Language.ini")); return dir;
        }
        [STAThread]
        public static int Main(string[] args)
        {
            root = args[0]; testRoot = Prepare(root); config = new Configuration(testRoot);
            int binAt = Array.IndexOf(args, "--bin");
            binaryDirectory = binAt >= 0 ? args[binAt + 1] : Path.Combine(root, "CSharp", "bin");
            if (args.Contains("--ui-only")) { Run("Python U40.43 state mapping and actual servo faults", DriveStates); Run("WinForms render and minimum-window smoke tests", RenderUi); return failures == 0 ? 0 : 1; }
            if (args.Contains("--console-only"))
            {
                Run("Actual Com launcher and awaited shutdown", ExecutableLifecycle);
                Run("Abrupt console-process exit finishes lowering", ConsoleProcessExit);
                Run("Native console WM_CLOSE finishes lowering", ConsoleWindowClose);
                WriteReport("console-test-results.json");
                return failures == 0 ? 0 : 1;
            }
            Oracle oracle = Json.Read<Oracle>(Path.Combine(root, "CSharp", "Tests", "reference.json"));
            Run("12,000 Python RTU frame vectors", () => {
                foreach (FrameCase c in oracle.Frames) Equal(c.Hex, Hex(c.Operation == "read" ? Rtu.Read(c.Axis, c.Address, c.Values[0]) : c.Operation == "write" ? Rtu.Write(c.Axis, c.Address, c.Values[0]) : Rtu.Multi(c.Axis, c.Address, c.Values)), "RTU " + c.Operation);
            });
            Run("Python motion conversions and target arithmetic", () => {
                foreach (ConversionCase c in oracle.Conversions) Equal(c.Units, config.Units(c.Axis, c.Mm), "Units");
                var engine = new Engine(config, true);
                foreach (TargetCase c in oracle.Targets) { engine.Offsets[c.Axis - 1] = c.Offset; Near(c.Mm, engine.TargetMm(c.Axis, c.Raw), "Target arithmetic"); }
            });
            Run("112 Python motion telegram traces (both word orders, all axes)", () => { foreach (Scenario s in oracle.Scenarios) Trace(s); });
            Run("Complete Python initialization and shutdown telegrams", () => { foreach (Scenario s in oracle.Lifecycle) LifecycleTrace(s); });
            Run("Python leveling corrections and rollback telegrams", () => { foreach (Scenario s in oracle.Leveling) LevelingTrace(s); });
            Run("Python motion simulator and ground geometry", () => {
                foreach (GeometryCase c in oracle.Geometry)
                {
                    double[][] result = Geometry.State(config, c.Positions, new[] { 0.0, 0.0 });
                    for (int i = 0; i < 4; i++) { Near(c.Ground[i], result[0][i], "Ground deviation"); Near(c.Loads[i], result[1][i], "Ground load"); }
                }
                foreach (DynamicsCase c in oracle.Dynamics)
                {
                    var a = new SimAxis { Target = c.Target, Position = c.Position, Velocity = c.Velocity, Speed = c.Speed, Accel = c.Accel, Decel = c.Decel };
                    SimAxis.Advance(a, c.Dt); Near(c.ExpectedPosition, a.Position, "Simulation position"); Near(c.ExpectedVelocity, a.Velocity, "Simulation velocity");
                }
            });
            Run("CRC retries, exception responses, signed and float reads", TransportErrors);
            Run("Three-bus queue barrier and failed-write trigger suppression", Barrier);
            Run("CSV compatibility, calibration, preview and playback conversion", CsvAndPersistence);
            Run("UDP, named pipe, maintenance, leveling, grease and playback integration", Integration);
            Run("Actual Com EXE, single instance and awaited shutdown", ExecutableLifecycle);
            Run("Abrupt console-process exit finishes lowering", ConsoleProcessExit);
            Run("Native console WM_CLOSE finishes lowering", ConsoleWindowClose);
            Run("HMI assembly isolation", () => {
                string path = Path.Combine(binaryDirectory, "SimHub2SimRigHMI.exe"); var assembly = Assembly.LoadFrom(path);
                Check(assembly.GetReferencedAssemblies().All(a => !a.Name.Contains("SimHub2SimRigCom")), "No engine reference in HMI");
                Check(assembly.GetTypes().All(t => t.Namespace != "SimHub2SimRig.Com"), "No Com types in HMI");
            });
            Run("WinForms render and minimum-window smoke tests", RenderUi);
            Run("Python U40.43 state mapping and actual servo faults", DriveStates);
            WriteReport("test-results.json");
            Console.WriteLine(checks + " checks; " + failures + " failed groups. Isolated test data: " + testRoot);
            return failures == 0 ? 0 : 1;
        }
        static void WriteReport(string name)
        { Json.Save(Path.Combine(root, "CSharp", "Tests", name), new { Passed = failures == 0, Checks = checks, Failures = failures, Results = results }); }
    }
}
