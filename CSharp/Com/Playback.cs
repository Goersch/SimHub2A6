using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public sealed class Playback
    {
        readonly Engine engine;
        readonly object gate = new object();
        readonly ManualResetEventSlim stop = new ManualResetEventSlim();
        Task worker;
        volatile string state = "stopped", file = "";
        double seconds, seek, pausedTotal, pauseStart;
        public string State { get { return state; } }
        public string FileName { get { return file; } }
        public double Seconds { get { lock (gate) return seconds; } }
        public Playback(Engine engine) { this.engine = engine; }
        public void Play(string name, int[] selected)
        {
            lock (gate)
            {
                if (state == "paused" && file == name)
                { pausedTotal += Timing.Now - pauseStart; state = "playing"; return; }
                if (state != "stopped") throw new InvalidOperationException("Playback already active");
                if (worker != null && !worker.IsCompleted) throw new InvalidOperationException("Playback is stopping");
                if (!engine.Recordings.Read(name).Any()) throw new InvalidDataException("Recording contains no valid samples");
                int[] axes = selected ?? Enumerable.Range(1, 7).ToArray();
                if (axes.Length == 0 || axes.Distinct().Count() != axes.Length || axes.Any(a => a < 1 || a > 7)) throw new ArgumentException("Invalid playback axes");
                int hubs = axes.Count(a => a >= 4); if (hubs != 0 && hubs != 4) throw new ArgumentException("Select all four lift axes together");
                axes = axes.OrderBy(a => a).ToArray();
                if (file != name) { seek = 0; seconds = 0; }
                file = name; stop.Reset(); pausedTotal = 0; state = "playing";
                worker = Task.Factory.StartNew(() => Run(name, axes), TaskCreationOptions.LongRunning);
            }
        }
        public void Pause() { lock (gate) if (state == "playing") { pauseStart = Timing.Now; state = "paused"; } }
        public void Stop()
        {
            Task task; lock (gate) { stop.Set(); task = worker; }
            if (task != null && !task.Wait(TimeSpan.FromSeconds(15))) throw new TimeoutException("Playback worker did not stop");
            lock (gate) { state = "stopped"; worker = null; }
        }
        public void Seek(double delta)
        {
            if (double.IsNaN(delta) || double.IsInfinity(delta)) throw new ArgumentException("Invalid seek offset");
            if (state == "playing") throw new InvalidOperationException("Pause playback before seeking");
            double current = Seconds; Stop();
            double end = engine.Recordings.Read(file).Select(s => s.Seconds).DefaultIfEmpty(0).Max();
            lock (gate) { seek = Math.Max(0, Math.Min(end, current + delta)); seconds = seek; }
        }
        public static int RawFromTarget(Configuration config, int axis, double target, double offset)
        {
            var a = config.Axes[axis - 1]; double value = Math.Round((target - offset + a.Stroke / 2) / a.Stroke * config.RawMax, MidpointRounding.ToEven);
            return (int)Math.Max(0, Math.Min(config.RawMax, value));
        }
        void Run(string name, int[] axes)
        {
            bool natural = false;
            try
            {
                engine.ArmPlayback(); double start = Timing.Now; double? first = null; double startAt;
                lock (gate) startAt = seek;
                foreach (Sample sample in engine.Recordings.Read(name))
                {
                    if (sample.Seconds < startAt) continue;
                    if (!first.HasValue) first = sample.Seconds;
                    // Recompute the deadline after every pause. A pause received
                    // during a long inter-sample delay must also take effect.
                    while (!stop.IsSet)
                    {
                        double delay; bool paused;
                        lock (gate) { paused = state == "paused"; delay = start + sample.Seconds - first.Value + pausedTotal - Timing.Now; }
                        if (!paused && delay <= 0) break;
                        stop.Wait(TimeSpan.FromSeconds(paused ? .01 : Math.Min(.01, Math.Max(.001, delay))));
                    }
                    if (stop.IsSet) return;
                    lock (engine.MotionGate)
                    {
                        if (stop.IsSet) return;
                        foreach (int axis in axes)
                        {
                            int raw = RawFromTarget(engine.Config, axis, sample.Target[axis - 1], engine.Offsets[axis - 1]);
                            engine.HandlePosition(axis, raw, false, true);
                        }
                        // Legacy playback triggers for every sample, including
                        // samples suppressed by the one-millimetre deadband.
                        engine.Motion.Trigger(); engine.Recorder.Trigger();
                    }
                    lock (gate) seconds = sample.Seconds;
                }
                natural = true;
            }
            catch (Exception e) { engine.Report(e); natural = true; }
            finally
            {
                if (natural && !stop.IsSet) try { lock (engine.MotionGate) engine.Center(); } catch (Exception e) { engine.Report(e); }
                lock (gate) state = "stopped";
            }
        }
    }
}
