using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        // Model reply/transmission time and the configured RTU gap without
        // opening real ports. This measures scheduling/contention, not USB latency.
        sealed class TimedWire : IWire
        {
            readonly VirtualWire inner;
            readonly double gap, baud;
            readonly PrecisionWait delay = new PrecisionWait();
            double next;
            public TimedWire(BusConfig bus, Configuration c) { inner = new VirtualWire(bus.Axes); gap = c.Ini.Number("modbus", "inter_frame_delay_s"); baud = c.Ini.Number("modbus", "baud"); }
            public byte[] Exchange(byte[] frame, int expected, double timeout, bool wait)
            {
                delay.Wait(next - Timing.Now);
                delay.Wait((frame.Length + (wait ? expected : 0)) * 10.0 / baud + (wait ? .001 : 0));
                byte[] reply = inner.Exchange(frame, expected, timeout, wait);
                next = Timing.Now + gap;
                return reply;
            }
            public void Dispose() { delay.Dispose(); inner.Dispose(); }
        }
        static object SenderBenchmark(bool moving, bool recording)
        {
            var c = new Configuration(Prepare(root));
            var engine = new Engine(c, true, b => new TimedWire(b, c));
            Task producer = null;
            var initializedField = typeof(Engine).GetField("initialized", BindingFlags.Instance | BindingFlags.NonPublic);
            try
            {
                engine.Bus.Connect(b => new TimedWire(b, c));
                // Exercise production loops directly, excluding slow homing and
                // touching only simulated wires in an isolated data directory.
                engine.Start(); initializedField.SetValue(engine, true);
                var latest = (int[])typeof(Engine).GetField("latest", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(engine);
                var latestGate = typeof(Engine).GetField("latestGate", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(engine);
                if (recording) engine.SetGame(true);
                if (moving) producer = Task.Factory.StartNew(() => {
                    int tick = 0;
                    while (!engine.Stop.IsSet)
                    {
                        int raw = (++tick % 2 == 0) ? 50000 : 80000;
                        lock (latestGate) for (int i = 0; i < latest.Length; i++) latest[i] = raw;
                        engine.Stop.Wait(10);
                    }
                }, TaskCreationOptions.LongRunning);
                Thread.Sleep(1000);
                double[] before = engine.Snapshot().TriggerIntervals;
                int skip = before.Length;
                double cpu = Process.GetCurrentProcess().TotalProcessorTime.TotalSeconds;
                var elapsed = Stopwatch.StartNew(); Thread.Sleep(6000);
                double[] samples = engine.Snapshot().TriggerIntervals.Skip(skip).OrderBy(v => v).ToArray();
                cpu = Process.GetCurrentProcess().TotalProcessorTime.TotalSeconds - cpu;
                Check(samples.Length > 50, "Sender benchmark collects samples");
                var result = new {
                    Scenario = recording ? "moving-recording" : moving ? "moving" : "stationary",
                    Samples = samples.Length, MeanMs = samples.Average(),
                    P95Ms = samples[(int)((samples.Length - 1) * .95)],
                    P99Ms = samples[(int)((samples.Length - 1) * .99)], MaxMs = samples.Last(),
                    Over50Ms = samples.Count(v => v > 50), CpuSeconds = cpu, WallSeconds = elapsed.Elapsed.TotalSeconds
                };
                Console.WriteLine(Json.Encode(result)); return result;
            }
            finally
            {
                engine.RequestShutdown(); if (producer != null) producer.Wait();
                initializedField.SetValue(engine, false); engine.Dispose();
            }
        }
        static void PerformanceBenchmarks(string report)
        {
            var measurements = new List<object>();
            measurements.Add(SenderBenchmark(false, false));
            measurements.Add(SenderBenchmark(true, false));
            measurements.Add(SenderBenchmark(true, true));
            var serialWaits = new List<object>();
            foreach (double seconds in new[] { .001, .00175 })
                foreach (bool legacy in new[] { true, false })
                    using (var wait = new PrecisionWait())
                    {
                        double cpu = Process.GetCurrentProcess().TotalProcessorTime.TotalSeconds;
                        var elapsed = Stopwatch.StartNew();
                        for (int i = 0; i < 500; i++)
                        {
                            if (legacy) Timing.Sleep(seconds);
                            else wait.WaitUntil(Timing.Now + seconds, .0005);
                        }
                        var result = new { Mode = legacy ? "previous-spin" : "timer-with-bounded-spin", RequestedMs = seconds * 1000,
                            MeanMs = elapsed.Elapsed.TotalMilliseconds / 500, CpuSeconds = Process.GetCurrentProcess().TotalProcessorTime.TotalSeconds - cpu };
                        Console.WriteLine(Json.Encode(result)); serialWaits.Add(result);
                    }
            Json.Save(report, new { MeasuredAt = DateTime.Now.ToString("s"), Note = "Simulated serial timing; no real USB adapters or drives", Measurements = measurements, SerialWaits = serialWaits });
        }
        sealed class BlockingWriter : StreamWriter
        {
            public readonly ManualResetEventSlim Entered = new ManualResetEventSlim(), Release = new ManualResetEventSlim();
            public BlockingWriter() : base(new MemoryStream()) { }
            public override void WriteLine(string value) { Entered.Set(); Release.Wait(); base.WriteLine(value); }
        }
        static void RecordingDoesNotBlockMotion()
        {
            using (var recorder = new Recorder(new Configuration(Prepare(root))))
            using (var stalled = new BlockingWriter())
            {
                recorder.SetRunning(true); recorder.ReadActual(true); recorder.Tick(.025);
                var field = typeof(Recorder).GetField("writer", BindingFlags.Instance | BindingFlags.NonPublic);
                var original = (StreamWriter)field.GetValue(recorder);
                string recordingPath = ((FileStream)original.BaseStream).Name;
                field.SetValue(recorder, stalled);
                Task tick = null, motion = null;
                try
                {
                    tick = Task.Factory.StartNew(() => recorder.Tick(.025), TaskCreationOptions.LongRunning);
                    Check(stalled.Entered.Wait(2000), "Recording reaches blocked disk write");
                    motion = Task.Factory.StartNew(() => {
                        recorder.Stage(1, 25, 400, 100, 400, 80000); recorder.Trigger();
                        recorder.SetActual(1, 12); recorder.ActualSnapshot();
                        lock (recorder.Gate) Equal(25.0, recorder.Axes[0].Target, "New target is committed while CSV write is blocked");
                    }, TaskCreationOptions.LongRunning);
                    Check(motion.Wait(1000), "Motion and HMI state remain accessible during an indefinitely blocked CSV write");
                }
                finally
                {
                    stalled.Release.Set(); if (tick != null) tick.Wait(); if (motion != null) motion.Wait();
                    field.SetValue(recorder, original); recorder.SetRunning(false);
                }
                string[] lines = File.ReadAllLines(recordingPath);
                Equal(2, lines.Length, "Stopping flushes the buffered CSV row");
                Equal(58, lines[1].Split(';').Length, "Buffered recording preserves the complete CSV schema");
            }
        }
        static void PrecisionTiming()
        {
            using (var stop = new ManualResetEventSlim())
            using (var wait = new PrecisionWait(stop))
            {
                double start = Timing.Now; wait.Wait(.01);
                Check(Timing.Now - start >= .009, "Timer waits until its deadline");
                double deadline = Timing.Now + .00175; wait.WaitUntil(deadline, .0005);
                Check(Timing.Now >= deadline, "Serial timing preserves the full inter-frame gap");
                wait.WaitUntil(Timing.Now - 1, .0005);
                var waiting = Task.Factory.StartNew(() => wait.Wait(30), TaskCreationOptions.LongRunning);
                Thread.Sleep(20); stop.Set();
                Check(waiting.Wait(1000), "Shutdown interrupts an active high-resolution wait");
                wait.Wait(.01); wait.Wait(0);
            }
        }
        static void GameTelemetryBudget()
        {
            var c = new Configuration(Prepare(root));
            int[] homes = new int[7], loads = new int[7], states = new int[7], servos = new int[7];
            var initializedField = typeof(Engine).GetField("initialized", BindingFlags.Instance | BindingFlags.NonPublic);
            Func<BusConfig, IWire> factory = b => {
                var wire = new VirtualWire(b.Axes);
                wire.Override = frame => {
                    if (frame[1] == 3)
                    {
                        int axis = frame[0] - 1, address = frame[2] << 8 | frame[3];
                        if (address == 0x0438 || address == 0x0439 || address == 0x4005) Interlocked.Increment(ref homes[axis]);
                        if (address == 0x4007) Interlocked.Increment(ref loads[axis]);
                        if (address == 0x4043) Interlocked.Increment(ref states[axis]);
                        if (address == 0x410a) Interlocked.Increment(ref servos[axis]);
                    }
                    return null;
                };
                return wire;
            };
            var engine = new Engine(c, true, factory);
            try
            {
                // No real initialization or port access: start directly with a
                // simulated game so no idle homing/load requests are in flight.
                engine.SetGame(true); engine.Bus.Connect(factory);
                engine.Start(); initializedField.SetValue(engine, true);
                Until(() => Enumerable.Range(0, 7).All(i => Volatile.Read(ref states[i]) > 0 && Volatile.Read(ref servos[i]) > 0), "Fault and servo polling continue on all axes during games", 4);
                for (int i = 0; i < 7; i++)
                {
                    Equal(0, Volatile.Read(ref homes[i]), "No maintenance homing reads during games, axis " + (i + 1));
                    Equal(0, Volatile.Read(ref loads[i]), "No load reads during games, axis " + (i + 1));
                }
                engine.SetGame(false);
                Until(() => Enumerable.Range(0, 7).All(i => Volatile.Read(ref homes[i]) >= 3 && Volatile.Read(ref loads[i]) > 0), "Homing and load polling resume on all axes after games", 4);
            }
            finally { initializedField.SetValue(engine, false); engine.Dispose(); }
        }
    }
}
