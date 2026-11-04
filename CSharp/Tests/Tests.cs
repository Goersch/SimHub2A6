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
        sealed class TrackedWire : IWire
        {
            public readonly VirtualWire Inner;
            public volatile bool Closed;
            public TrackedWire(int[] axes) { Inner = new VirtualWire(axes); }
            public byte[] Exchange(byte[] frame, int expected, double timeout, bool wait) { return Inner.Exchange(frame, expected, timeout, wait); }
            public void Dispose() { Closed = true; Inner.Dispose(); }
        }
        static void HmiStatusEvents()
        {
            var c = new Configuration(Prepare(root)); int opened = 0;
            using (var engine = new Engine(c, true, bus => { Interlocked.Increment(ref opened); return new VirtualWire(bus.Axes); }))
            {
                engine.Start(); string session = Guid.NewGuid().ToString("N");
                Status first = engine.Handle(new Request { Operation = "status", HmiSession = session }).Status;
                string connected = first.Messages.Last();
                Check(connected.EndsWith("HMI connected"), "Com reports first HMI connection");
                Status repeated = engine.Handle(new Request { Operation = "status", HmiSession = session }).Status;
                Check(first.Messages.SequenceEqual(repeated.Messages), "Repeated heartbeat does not duplicate connection output");
                Until(() => engine.Snapshot().Messages.Last().EndsWith("HMI disconnected"), "Missing heartbeat reports HMI disconnection", 8);
                Check(!engine.Snapshot().Initialized && !engine.Snapshot().Busy && !engine.OperationStop.IsSet, "HMI tracking does not change Com operation state");
                Equal(0, Volatile.Read(ref opened), "HMI heartbeat never initializes drives");
                Check(engine.Handle(new Request { Operation = "status", HmiSession = session }).Status.Messages.Last().EndsWith("HMI connected"), "Returning HMI reports connection again");
                engine.Handle(new Request { Operation = "hmi.detach", HmiSession = session });
                Check(engine.Snapshot().Messages.Last().EndsWith("HMI disconnected"), "Explicit old-client detach only reports disconnection");
                Equal(3, engine.Snapshot().Messages.Length, "Com exposes only its latest three outputs");
            }
        }
        static void PortOpenRecovery()
        {
            var c = new Configuration(Prepare(root)); string blockedPort = c.Buses[1].Port;
            int attempts = 0; var attemptTimes = new List<double>();
            using (var engine = new Engine(c, true, bus => {
                if (bus.Port == blockedPort)
                {
                    lock (attemptTimes) attemptTimes.Add(Timing.Now);
                    int attempt = Interlocked.Increment(ref attempts);
                    if (attempt == 1) throw new IOException("Test port unavailable");
                    if (attempt == 2) throw new UnauthorizedAccessException("Test port in use");
                }
                return new VirtualWire(bus.Axes);
            }))
            {
                string pipeName = "SimHub2SimRig.PortRetry." + Guid.NewGuid().ToString("N");
                using (var server = new PipeServer(engine, pipeName))
                {
                    engine.Start(); server.Start(); var client = new ComClient(pipeName);
                    Check(client.Send(new Request { Operation = "initialize" }).Ok, "HMI explicitly requests initialization before port recovery");
                    Until(() => engine.Snapshot().Activity.Contains("I try again in 10 seconds"), "Port error is visible while waiting");
                    Check(engine.Snapshot().Messages.Any(m => m.EndsWith("INIT request received by HMI")), "Com reports accepted HMI INIT request");
                    var watch = Stopwatch.StartNew(); Status status = client.Send(new Request { Operation = "status" }).Status;
                    Check(watch.Elapsed.TotalSeconds < 2, "IPC remains responsive during port retry delay");
                    Check(status.Busy && !status.Initialized, "Initialization stays pending during port failure");
                    Check(status.Activity.Contains(blockedPort) && status.Activity.Contains("COM Error"), "HMI status identifies failed port and retry");
                    Equal("Test port unavailable", status.LastError, "HMI status includes port error details");
                    Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can leave after requesting INIT");
                    Equal(status.Activity, engine.Snapshot().Activity, "Closing HMI does not change pending initialization");
                    Check(!engine.OperationStop.IsSet, "Closing HMI does not cancel initialization");
                    Thread.Sleep(200); Equal(1, Volatile.Read(ref attempts), "Port retry does not spin immediately");
                    Until(() => engine.Snapshot().LastError == "Test port in use", "Access-denied errors are retried too", 13);
                    Until(() => engine.Snapshot().Initialized && !engine.Snapshot().Busy, "Initialization resumes after the port becomes available", 30);
                    Equal(3, Volatile.Read(ref attempts), "Failed port is retried until it opens");
                    lock (attemptTimes)
                        for (int i = 1; i < attemptTimes.Count; i++) Check(attemptTimes[i] - attemptTimes[i - 1] >= 10, "Each failed port open waits a full ten seconds");
                    Equal("", engine.Snapshot().LastError, "Port error clears after recovery");
                }
            }
        }
        static void PortOpenCancellation()
        {
            var c = new Configuration(Prepare(root)); TrackedWire opened = null; int attempts = 0;
            Stopwatch shutdownWatch = null;
            using (var engine = new Engine(c, true, bus => {
                if (bus.Port == c.Buses[1].Port) { Interlocked.Increment(ref attempts); throw new UnauthorizedAccessException("Test permanently blocked port"); }
                opened = new TrackedWire(bus.Axes); return opened;
            }))
            {
                engine.Start();
                Check(engine.Handle(new Request { Operation = "initialize" }).Ok, "Explicit INIT before cancellation test");
                Until(() => engine.Snapshot().Activity.Contains("I try again in 10 seconds"), "Blocked port enters retry delay");
                Check(opened != null && !opened.Closed, "Previously opened port is owned during connection setup");
                Equal(0, opened.Inner.Frames.Count, "No drive commands before every port is accessible");
                shutdownWatch = Stopwatch.StartNew(); engine.RequestShutdown();
            }
            Check(shutdownWatch.Elapsed.TotalSeconds < 3, "Shutdown interrupts ten-second retry delay promptly");
            Equal(1, Volatile.Read(ref attempts), "No further port retries after shutdown");
            Check(opened.Closed, "Shutdown releases ports opened before the failure");
            using (var bus = new Modbus(c))
            {
                int retries = 0;
                Throws<ArgumentException>(() => bus.Connect(_ => { throw new ArgumentException("Invalid port configuration"); }, (port, failure) => retries++), "Configuration errors are not retried");
                Equal(0, retries, "Only port access failures trigger retry");
                Check(!bus.Connected, "Failed connection leaves no open routes");
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
            int[] loadReads = new int[7], stateReads = new int[7], actualReads = new int[7], servoReads = new int[7];
            int targetWrites = 0, triggerWrites = 0;
            Func<int[], int[]> counts = values => Enumerable.Range(0, 7).Select(i => Volatile.Read(ref values[i])).ToArray();
            using (var engine = new Engine(c, true, b => {
                var w = new VirtualWire(b.Axes);
                w.Override = frame => {
                    if (frame[1] == 3)
                    {
                        int axis = frame[0] - 1, address = frame[2] << 8 | frame[3];
                        if (address == 0x4007) Interlocked.Increment(ref loadReads[axis]);
                        if (address == 0x4043) Interlocked.Increment(ref stateReads[axis]);
                        if (address == 0x4016) Interlocked.Increment(ref actualReads[axis]);
                        if (address == 0x410a) Interlocked.Increment(ref servoReads[axis]);
                    }
                    if (frame[1] == 16 && frame[2] == 0x11 && frame[3] == 6) Interlocked.Increment(ref targetWrites);
                    if (frame[1] == 6 && frame[2] == 4 && frame[3] == 0x15) Interlocked.Increment(ref triggerWrites);
                    return null;
                };
                lock (wires) wires[b.Port] = w; return w;
            }))
            using (var server = new PipeServer(engine, "SimHub2SimRig.Tests." + Guid.NewGuid().ToString("N")))
            {
                // Get the test pipe name via an independent known server below.
                engine.Start();
                string pipeName = "SimHub2SimRig.Tests." + Guid.NewGuid().ToString("N");
                using (var actualServer = new PipeServer(engine, pipeName))
                {
                    actualServer.Start(); var client = new ComClient(pipeName);
                    Status waiting = client.Send(new Request { Operation = "status" }).Status;
                    int recordingsBeforeInit = Directory.GetFiles(c.PathFor("SimHubData"), "*.csv").Length;
                    Check(!waiting.Initialized && !waiting.Busy, "Com starts idle and awaits HMI INIT");
                    Thread.Sleep(150); lock (wires) Equal(0, wires.Count, "Starting Com does not open serial ports automatically");
                    foreach (string command in new[] { "mode", "leveling.start", "loads.save", "maintenance.open", "analysis.open", "grease.reset", "playback.play" })
                        Check(!client.Send(new Request { Operation = command }).Ok, "Com rejects pre-INIT function " + command);
                    using (var earlyUdp = new UdpClient())
                    {
                        var endpoint = new IPEndPoint(IPAddress.Loopback, c.Ini.Int("simhub", "udp_port"));
                        byte[] early = System.Text.Encoding.ASCII.GetBytes("START;POSITIONS 1 1 1 1 1 1 1;END;SHUTDOWN");
                        earlyUdp.Send(early, early.Length, endpoint); engine.Dispatch("START;POSITIONS 1 1 1 1 1 1 1;SHUTDOWN");
                        Thread.Sleep(200); Status ignored = engine.Snapshot();
                        Check(!ignored.Connected && !ignored.GameRunning && !engine.Stop.IsSet, "All SimHub telegrams are ignored before INIT");
                        Check(ignored.LatestPositions.SequenceEqual(waiting.LatestPositions), "Pre-INIT targets are not cached");
                        Check(client.Send(new Request { Operation = "initialize" }).Ok, "HMI requests INIT through IPC");
                        Check(!client.Send(new Request { Operation = "initialize" }).Ok, "Repeated INIT rejected while initialization runs");
                        earlyUdp.Send(early, early.Length, endpoint); Thread.Sleep(200);
                        ignored = engine.Snapshot();
                        Check(!ignored.Connected && !ignored.GameRunning && !engine.Stop.IsSet, "SimHub remains ignored throughout initialization");
                        Check(ignored.LatestPositions.SequenceEqual(waiting.LatestPositions), "Targets arriving during INIT are not cached");
                    }
                    Until(() => engine.Snapshot().Initialized && !engine.Snapshot().Busy, "HMI-requested initialization finishes", 35);
                    Check(!engine.Snapshot().GameRunning && !engine.Snapshot().Connected, "Finishing INIT does not replay discarded SimHub telegrams");
                    Equal(recordingsBeforeInit, Directory.GetFiles(c.PathFor("SimHubData"), "*.csv").Length, "Pre-INIT telegrams do not start recordings");
                    Check(client.Send(new Request { Operation = "status" }).Status.Initialized, "IPC status");
                    Check(!client.Send(new Request { Operation = "raw.write" }).Ok, "No raw register commands in HMI protocol");
                    Check(!client.Send(new Request { Operation = "move", FirstAxis = 1, LastAxis = 1, Position = 0 }).Ok, "Com enforces maintenance ownership");
                    Check(!client.Send(new Request { Operation = "mode", Mode = "invalid" }).Ok, "Mode validation");
                    Task[] polls = Enumerable.Range(0, 4).Select(_ => Task.Run(() => { for (int i = 0; i < 10; i++) Check(client.Send(new Request { Operation = "status" }).Ok, "Concurrent polling"); })).ToArray(); Task.WaitAll(polls);
                    using (var udp = new UdpClient())
                    {
                        var endpoint = new IPEndPoint(IPAddress.Loopback, c.Ini.Int("simhub", "udp_port"));
                        int[] idleLoads = counts(loadReads);
                        Until(() => counts(loadReads).Zip(idleLoads, (now, baseline) => now > baseline).All(v => v), "Idle telemetry polls loads on every drive");
                        Check(client.Send(new Request { Operation = "mode", Mode = "actual_positions" }).Ok, "Actual feedback enabled for polling regression");
                        byte[] packet = System.Text.Encoding.ASCII.GetBytes("POSITIONS 65535 65535 65535 65535 65535 65535 65535"); udp.Send(packet, packet.Length, endpoint);
                        Until(() => engine.Snapshot().GameRunning, "UDP starts recording");
                        Check(!client.Send(new Request { Operation = "maintenance.open" }).Ok, "Maintenance blocked by active UDP");
                        // Let any read issued before START finish before checking
                        // the wire traffic across multiple complete status cycles.
                        Thread.Sleep(100);
                        int[] activeLoads = counts(loadReads), activeStates = counts(stateReads), activeActuals = counts(actualReads);
                        double activeUntil = Timing.Now + 2 * c.Ini.Number("ui", "axis_status_refresh_ms") / 1000 + .5;
                        while (Timing.Now < activeUntil) { udp.Send(packet, packet.Length, endpoint); Thread.Sleep(50); }
                        Check(engine.Snapshot().GameRunning, "SimHub stays active throughout polling check");
                        for (int i = 0; i < 7; i++)
                        {
                            Equal(activeLoads[i], Volatile.Read(ref loadReads[i]), "No load register reads while SimHub plays, axis " + (i + 1));
                            Check(Volatile.Read(ref stateReads[i]) > activeStates[i], "Drive status still polled while SimHub plays, axis " + (i + 1));
                            Check(Volatile.Read(ref actualReads[i]) > activeActuals[i], "Actual feedback still polled while SimHub plays, axis " + (i + 1));
                        }
                        engine.Dispatch("POSITIONS -1 1 2 3 4 5 6;POSITIONS 1 2");
                        byte[] end = System.Text.Encoding.ASCII.GetBytes("END"); udp.Send(end, end.Length, endpoint); Until(() => !engine.Snapshot().GameRunning, "END stops recording");
                        Until(() => counts(loadReads).Zip(activeLoads, (now, baseline) => now > baseline).All(v => v), "Load polling resumes on every drive after END");
                        Check(engine.Snapshot().Connected, "Load polling resumes while SimHub is still connected but idle");
                        Check(client.Send(new Request { Operation = "mode", Mode = "simhub" }).Ok, "Restore SimHub mode after polling regression");
                        udp.Send(packet, packet.Length, endpoint); Until(() => engine.Snapshot().GameRunning, "SimHub restarts before disconnect test");
                        Until(() => !engine.Snapshot().Connected && !engine.Snapshot().GameRunning, "UDP disconnect ends connection and game state", 6);
                        int[] disconnectedLoads = counts(loadReads);
                        Until(() => counts(loadReads).Zip(disconnectedLoads, (now, baseline) => now > baseline).All(v => v), "Load polling resumes on every drive after disconnect");
                    }
                    Check(client.Send(new Request { Operation = "leveling.start" }).Ok, "Leveling request");
                    Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can leave during leveling");
                    Check(!engine.OperationStop.IsSet, "HMI detach does not stop leveling");
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
                    Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can leave during greasing");
                    Thread.Sleep(100);
                    Check(engine.Snapshot().Greasing && engine.Snapshot().Maintenance && engine.Snapshot().Busy && !engine.OperationStop.IsSet, "Greasing and maintenance remain active without HMI");
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
                    Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can leave during playback");
                    Check(engine.Snapshot().Analysis && engine.Snapshot().Playback == "playing", "Playback continues without HMI");
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
                    Check(client.Send(new Request { Operation = "mode", Mode = "simhub" }).Ok, "Restore active mode for HMI restart regression");
                    int pendingTargets = Volatile.Read(ref targetWrites), pendingTriggers = Volatile.Read(ref triggerWrites);
                    lock (engine.MotionGate)
                    {
                        // HMI departure must not discard already accepted targets.
                        engine.Dispatch("POSITIONS 10000 10000 10000 10000 10000 10000 10000;END");
                        Check(client.Send(new Request { Operation = "hmi.detach" }).Ok, "HMI can detach");
                    }
                    Until(() => !engine.Snapshot().Busy, "Detached without terminating Com");
                    Check(engine.Snapshot().Initialized, "Com runs without HMI");
                    Until(() => Volatile.Read(ref targetWrites) > pendingTargets && Volatile.Read(ref triggerWrites) > pendingTriggers, "Accepted SimHub targets execute without HMI or another packet");
                    Check(!engine.OperationStop.IsSet && engine.Snapshot().Mode == "simhub", "HMI departure preserves operation and mode state");
                    Thread.Sleep(100);
                    int detachedTargets = Volatile.Read(ref targetWrites), detachedTriggers = Volatile.Read(ref triggerWrites);
                    int[] detachedServos = counts(servoReads); int detachedSamples = engine.Snapshot().TriggerIntervals.Length;
                    Thread.Sleep(1800);
                    for (int axis = 4; axis <= 7; axis++) Check(Volatile.Read(ref servoReads[axis - 1]) - detachedServos[axis - 1] >= 3, "Hub monitoring continues at sender rate without HMI, axis " + axis);
                    Check(engine.Snapshot().TriggerIntervals.Length > detachedSamples, "Com continues collecting sender timings without HMI");
                    Equal(detachedTargets, Volatile.Read(ref targetWrites), "Stationary targets are not repeatedly rewritten without HMI");
                    Equal(detachedTriggers, Volatile.Read(ref triggerWrites), "Idle sender does not emit movement triggers after detach");
                    Status reattached = new ComClient(pipeName).Send(new Request { Operation = "status" }).Status;
                    Check(reattached.Initialized && reattached.TriggerIntervals.Length > 0, "Restarted HMI receives existing initialization and timing history");
                    int[] reattachedServos = counts(servoReads); Thread.Sleep(1800);
                    for (int axis = 4; axis <= 7; axis++) Check(Volatile.Read(ref servoReads[axis - 1]) - reattachedServos[axis - 1] >= 3, "Hub monitoring remains active after HMI reconnect, axis " + axis);
                    Equal(detachedTargets, Volatile.Read(ref targetWrites), "Reconnecting HMI does not change movement targets");
                    engine.Dispatch("POSITIONS 20000 20000 20000 20000 20000 20000 20000;END");
                    Until(() => Volatile.Read(ref targetWrites) > detachedTargets && Volatile.Read(ref triggerWrites) > detachedTriggers, "SimHub motion continues after HMI restart");
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
        static void ComStartWorkflow()
        {
            int queries = 0, launched = 0, initialized = 0;
            var watch = Stopwatch.StartNew();
            SimHub2SimRig.HMI.ComStartup.Run(() => {
                int attempt = ++queries;
                if (attempt < 4) { var failed = new TaskCompletionSource<Status>(); failed.SetException(new TimeoutException("Not connected yet")); return failed.Task; }
                return Task.FromResult(new Status());
            }, () => launched++, () => { Check(queries >= 4, "INIT waits for confirmed IPC connection"); initialized++; return Task.FromResult(0); }, () => false).GetAwaiter().GetResult();
            Equal(1, launched, "START launches Com once"); Equal(1, initialized, "START requests INIT once");
            Check(watch.Elapsed.TotalSeconds < 5, "Connected Com initializes without a fixed five-second delay");
            SimHub2SimRig.HMI.ComStartup.Run(() => Task.FromResult(new Status { Initialized = true }), () => launched++, () => { initialized++; return Task.FromResult(0); }, () => false).GetAwaiter().GetResult();
            Equal(1, launched, "Existing connection does not launch another Com"); Equal(1, initialized, "Initialized Com is not initialized again");
            bool canceled = false;
            Throws<OperationCanceledException>(() => SimHub2SimRig.HMI.ComStartup.Run(() => { var failed = new TaskCompletionSource<Status>(); failed.SetException(new IOException("Offline")); return failed.Task; }, () => canceled = true, () => { initialized++; return Task.FromResult(0); }, () => canceled).GetAwaiter().GetResult(), "Closing HMI cancels pending startup wait");
            Equal(1, initialized, "No INIT after HMI closes before connection");
            Throws<FileNotFoundException>(() => SimHub2SimRig.HMI.ComStartup.Run(() => { var failed = new TaskCompletionSource<Status>(); failed.SetException(new IOException("Offline")); return failed.Task; }, () => { throw new FileNotFoundException("Com executable missing"); }, () => { initialized++; return Task.FromResult(0); }, () => false).GetAwaiter().GetResult(), "Launch errors are reported without initializing");
            Equal(1, initialized, "Failed process launch never sends INIT");
            SimHub2SimRig.HMI.ComStartup.Run(() => Task.FromResult(new Status { Busy = true }), () => launched++, () => { initialized++; return Task.FromResult(0); }, () => false).GetAwaiter().GetResult();
            Equal(1, initialized, "Already busy Com does not receive a duplicate INIT");
        }
        static void RenderUi()
        {
            System.Windows.Forms.Application.EnableVisualStyles();
            System.Windows.Forms.Application.SetCompatibleTextRenderingDefault(false);
            var offlineContext = new SimHub2SimRig.HMI.UiContext(testRoot, "SimHub2SimRig.OfflineClose." + Guid.NewGuid().ToString("N"));
            foreach (System.Windows.Forms.Form form in new System.Windows.Forms.Form[] { new SimHub2SimRig.HMI.MainForm(offlineContext), new SimHub2SimRig.HMI.MaintenanceForm(offlineContext), new SimHub2SimRig.HMI.AnalysisForm(offlineContext) })
                using (form)
                {
                    var closing = new System.Windows.Forms.FormClosingEventArgs(System.Windows.Forms.CloseReason.FormOwnerClosing, false);
                    typeof(System.Windows.Forms.Form).GetMethod("OnFormClosing", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(form, new object[] { closing });
                    Check(!closing.Cancel, "HMI exit closes local windows without waiting for Com: " + form.GetType().Name);
                }
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
                        var mainGrid = Descendants(form).OfType<System.Windows.Forms.DataGridView>().Single(g => !(g is SimHub2SimRig.HMI.LogTable));
                        Equal(System.Windows.Forms.DataGridViewContentAlignment.MiddleCenter, mainGrid.DefaultCellStyle.Alignment, "Motor values are centered");
                        Equal(System.Windows.Forms.DataGridViewContentAlignment.MiddleCenter, mainGrid.ColumnHeadersDefaultCellStyle.Alignment, "Motor headers are centered");
                        var mainButtons = Descendants(form).OfType<System.Windows.Forms.Button>().ToArray();
                        var init = mainButtons.Single(b => b.Text == "INIT");
                        var start = mainButtons.Single(b => b.Text == "START");
                        Equal(!context.Status.Initialized && !start.Visible, init.Visible, "INIT shown unless START overlays it or initialization is complete");
                        if (!context.Status.Initialized)
                        {
                            var active = start.Visible ? start : init;
                            Check(active.Font.Size >= 24 && active.Width >= 200 && active.Height >= 96, "INIT/START is a large prominent button");
                            Equal(!context.Status.Busy, active.Enabled, "INIT/START enabled only while waiting for a request");
                            Check(mainButtons.Where(b => b != active).All(b => !b.Enabled), "Every other button disabled before INIT completes");
                            Check(Descendants(form).OfType<System.Windows.Forms.RadioButton>().All(r => !r.Enabled), "Motion modes disabled before INIT completes");
                        }
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
            status.Initialized = false;
            var offline = new SimHub2SimRig.HMI.MainForm(context); offline.UpdateStatus(status);
            typeof(SimHub2SimRig.HMI.MainForm).GetField("comUnavailable", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(offline, true);
            typeof(SimHub2SimRig.HMI.MainForm).GetMethod("UpdateInitializationControls", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(offline, new object[] { false, false });
            capture(offline, "main-start");
            var main = new SimHub2SimRig.HMI.MainForm(context);
            Check(Descendants(main).OfType<System.Windows.Forms.Button>().All(b => !b.Enabled), "HMI opens with controls disabled until Com status is known");
            main.UpdateStatus(status); capture(main, "main-init");
            main = new SimHub2SimRig.HMI.MainForm(context); main.Size = main.MinimumSize; main.UpdateStatus(status); capture(main, "main-init-minimum");
            status.Busy = true; status.Activity = "Initialization";
            main = new SimHub2SimRig.HMI.MainForm(context); main.UpdateStatus(status);
            Check(Descendants(main).OfType<System.Windows.Forms.Button>().All(b => !b.Enabled), "INIT and every action disabled during initialization");
            status.Busy = false; status.Activity = ""; status.LastError = "Test initialization failure"; main.UpdateStatus(status);
            Check(Descendants(main).OfType<System.Windows.Forms.Button>().Single(b => b.Text == "INIT").Enabled, "Failed initialization can be retried with INIT");
            Check(Descendants(main).OfType<System.Windows.Forms.Button>().Where(b => b.Text != "INIT").All(b => !b.Enabled), "Failure keeps other actions disabled");
            using (var child = new System.Windows.Forms.Form())
            {
                child.Owner = main; main.UpdateStatus(status);
                Check(!child.Enabled, "Open child dialogs also disabled when initialization is missing");
                status.Initialized = true; main.UpdateStatus(status);
                Check(child.Enabled, "Child dialogs enabled again after successful initialization");
            }
            main.Dispose(); status.LastError = ""; status.Initialized = true;
            main = new SimHub2SimRig.HMI.MainForm(context); for (int i = 0; i < 10; i++) main.UpdateStatus(status);
            var grid = Descendants(main).OfType<System.Windows.Forms.DataGridView>().Single(g => !(g is SimHub2SimRig.HMI.LogTable));
            for (int i = 0; i < 7; i++)
            {
                Equal(context.T("Common", "status_ok"), Convert.ToString(grid.Rows[i].Cells[3].Value), "Reason 5 displays OK on axis " + (i + 1));
                Check(grid.Rows[i].DefaultCellStyle.BackColor != System.Drawing.Color.MistyRose, "Reason 5 does not color axis red");
            }
            status.Connected = true; status.GameRunning = true; main.UpdateStatus(status);
            for (int i = 0; i < 7; i++) Equal("", Convert.ToString(grid.Rows[i].Cells[2].Value), "Load display is blank during SimHub play, axis " + (i + 1));
            status.GameRunning = false; main.UpdateStatus(status);
            for (int i = 0; i < 7; i++) Equal(status.Axes[i].Load.ToString("F1") + " %", Convert.ToString(grid.Rows[i].Cells[2].Value), "Load display returns when SimHub is idle, axis " + (i + 1));
            status.Connected = false; main.UpdateStatus(status);
            status.Axes[0].Servo = 3; main.UpdateStatus(status);
            Check(Convert.ToString(grid.Rows[0].Cells[3].Value).Contains("Drive fault"), "Grid displays true servo fault even for reason 5");
            Equal(System.Drawing.Color.MistyRose, grid.Rows[0].DefaultCellStyle.BackColor, "True servo fault is red");
            status.Axes[0].Servo = 1;
            status.Messages = new[] { "2026-11-04 12:00:00.000 HMI connected", "2026-11-04 12:00:01.000 INIT request received by HMI", "2026-11-04 12:00:12.000 Initialization complete" };
            main.UpdateStatus(status);
            var statusHistory = Descendants(main).OfType<SimHub2SimRig.HMI.LogTable>().Single(l => l.Name == "statusHistory");
            Check(statusHistory.Rows.Cast<System.Windows.Forms.DataGridViewRow>().Select(r => (string)r.Tag).SequenceEqual(status.Messages.Select(m => "[COM] " + m)), "Footer shows the latest three Com outputs with their source in order");
            Equal("04.11.2026", Convert.ToString(statusHistory.Rows[0].Cells[0].Value), "Log date has its own column");
            Equal("12:00:00.000", Convert.ToString(statusHistory.Rows[0].Cells[1].Value), "Log time has its own column");
            Equal("COM", Convert.ToString(statusHistory.Rows[0].Cells[2].Value), "Process has its own column");
            Equal("HMI connected", Convert.ToString(statusHistory.Rows[0].Cells[3].Value), "Message excludes process and timestamp");
            for (int i = 0; i < 10; i++) main.UpdateStatus(status);
            Check(statusHistory.Rows.Cast<System.Windows.Forms.DataGridViewRow>().Select(r => (string)r.Tag).SequenceEqual(status.Messages.Select(m => "[COM] " + m)), "Status polling does not duplicate footer history");
            var retainedMessages = (List<string>)typeof(SimHub2SimRig.HMI.MainForm).GetField("statusMessages", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(main);
            Check(retainedMessages.Count > 3, "Full session history retains outputs removed from the three-line footer");
            var historyDialog = new SimHub2SimRig.HMI.StatusHistoryForm(retainedMessages);
            var historyList = Descendants(historyDialog).OfType<SimHub2SimRig.HMI.LogTable>().Single();
            Check(historyList.Rows.Cast<System.Windows.Forms.DataGridViewRow>().Select(r => (string)r.Tag).SequenceEqual(retainedMessages.SelectMany(m => m.Replace("\r", "").Split('\n'))), "History dialog contains all retained messages in order");
            int historyLines = historyList.Rows.Count;
            historyDialog.AddMessage("[COM] Live-Meldung\nWeitere Fehlerdetails");
            Equal(historyLines + 2, historyList.Rows.Count, "Live updates include multiline COM output");
            Check(historyList.ScrollBars == System.Windows.Forms.ScrollBars.Both, "History supports scrolling for long outputs");
            for (int i = 0; i < 150; i++) historyDialog.AddMessage("[COM] Testmeldung " + i.ToString("D3") + " - Statusausgabe seit HMI-Start");
            historyDialog.AddMessage("[COM] " + string.Concat(Enumerable.Repeat("Lange Meldung mit zusätzlichen Details. ", 8)));
            capture(historyDialog, "status-history");
            capture(main, "main");
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
                    var client = new ComClient(); bool initRequested = false;
                    Until(() => {
                        if (process.HasExited) throw new Exception("Com EXE failed: " + output);
                        try
                        {
                            Status s = client.Send(new Request { Operation = "status" }).Status;
                            int ownedWorker = WorkerPid(isolated);
                            if (ownedWorker == 0) return false;
                            if (s.ProcessId != ownedWorker || !s.Simulated) throw new InvalidOperationException("Another Com session is already running; test will not control it");
                            if (!initRequested)
                            {
                                Check(!s.Initialized && !s.Busy, "External Com awaits explicit INIT");
                                Check(client.Send(new Request { Operation = "initialize" }).Ok, "External HMI client requests INIT");
                                initRequested = true; return false;
                            }
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
            if (args.Contains("--ui-only")) { Run("START waits for IPC connection before INIT", ComStartWorkflow); Run("Python U40.43 state mapping and actual servo faults", DriveStates); Run("WinForms render and minimum-window smoke tests", RenderUi); return failures == 0 ? 0 : 1; }
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
            Run("HMI connection events, timeout and three-output history", HmiStatusEvents);
            Run("COM access errors retry every ten seconds and recover", PortOpenRecovery);
            Run("COM retry cancellation and partial-port cleanup", PortOpenCancellation);
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
