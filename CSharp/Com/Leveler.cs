using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    // Port of the active Python leveling4/level2 workflow, including independent
    // verification, best-position rollback and fixed highest-load reference.
    public sealed class Leveler
    {
        readonly Engine engine;
        readonly Ini ini;
        readonly Action<double> testDelay;
        readonly Dictionary<int, double> bias = new Dictionary<int, double>();
        int[] axes;
        public int FixedAxis { get; private set; }
        public Leveler(Engine engine, Action<double> delay = null)
        {
            this.engine = engine; ini = engine.Config.Ini; testDelay = delay;
            foreach (string part in ini.Get("leveling", "load_bias_by_axis").Split(',').Where(s => s.Trim().Length > 0))
            { string[] p = part.Split(':'); bias[int.Parse(p[0])] = double.Parse(p[1], System.Globalization.CultureInfo.InvariantCulture); }
        }
        void Check() { if (engine.OperationStop.IsSet) throw new OperationCanceledException(); }
        void Wait(double seconds) { Check(); if (testDelay != null) { testDelay(seconds); Check(); } else if (engine.OperationStop.Wait(TimeSpan.FromSeconds(seconds))) throw new OperationCanceledException(); }
        void Write(string message)
        {
            Log.Write("[LEVELING] " + message);
            string path = engine.Config.PathFor("LOG/leveling.log"); Directory.CreateDirectory(Path.GetDirectoryName(path)); File.AppendAllText(path, DateTime.Now.ToString("HH:mm:ss") + " " + message + Environment.NewLine);
        }
        string OffsetPath { get { return engine.Config.PathFor("INI/leveling_offsets.json"); } }
        public bool Load()
        {
            if (!File.Exists(OffsetPath)) return false;
            try
            {
                string raw = File.ReadAllText(OffsetPath); double[] values;
                if (raw.TrimStart().StartsWith("[")) values = Json.Decode<double[]>(raw);
                else values = Json.Decode<OffsetData>(raw).offsets;
                if (values == null || values.Any(v => double.IsNaN(v) || double.IsInfinity(v))) throw new InvalidDataException("Invalid leveling offsets");
                foreach (int a in ini.Ints("leveling", "offset_axes")) if (a - 1 < values.Length) engine.Offsets[a - 1] = values[a - 1];
                return true;
            }
            catch (Exception e) { Write("Offsets could not be loaded: " + e.Message); return false; }
        }
        public sealed class OffsetData { public double[] offsets { get; set; } public int[] axes { get; set; } public string savedAt { get; set; } }
        public void Save() { Json.Save(OffsetPath, new OffsetData { offsets = engine.Offsets.Select(v => Math.Round(v, 4, MidpointRounding.ToEven)).ToArray(), axes = ini.Ints("leveling", "offset_axes"), savedAt = DateTime.Now.ToString("s") }); }
        void Move(int axis, double offset)
        {
            Check(); engine.Offsets[axis - 1] = offset;
            double target = engine.Config.Axes[axis - 1].Zero + offset;
            engine.Previous[axis - 1] = target; engine.Motion.Position(axis, target);
        }
        public void Apply()
        {
            foreach (int axis in ini.Ints("leveling", "offset_axes")) if (engine.Config.Axes[axis - 1].Enabled)
            {
                double target = engine.Config.Axes[axis - 1].Zero + engine.Offsets[axis - 1];
                engine.Previous[axis - 1] = target; engine.Motion.Position(axis, target);
            }
            if (testDelay != null) testDelay(1); else Timing.Sleep(1);
        }
        public double[] ReadLoads(int[] requested = null)
        {
            Check(); requested = requested ?? axes;
            int count = ini.Int("leveling", "load_rate_samples");
            var samples = requested.Select(_ => new List<double>()).ToArray();
            for (int n = 0; n < count; n++)
            {
                Check(); for (int i = 0; i < requested.Length; i++) { Check(); samples[i].Add(engine.Motion.Load(requested[i])); }
                if (n < count - 1) Wait(ini.Number("leveling", "load_rate_sample_delay_s"));
            }
            return samples.Select(list => {
                list.Sort(); int m = list.Count / 2;
                return Math.Round(list.Count % 2 == 1 ? list[m] : (list[m - 1] + list[m]) / 2, MidpointRounding.ToEven);
            }).ToArray();
        }
        bool Settle()
        {
            Wait(ini.Number("leveling", "settle_time_s")); double[] previous = ReadLoads();
            for (int n = 0; n < ini.Int("leveling", "settle_checks"); n++)
            {
                Wait(ini.Number("leveling", "settle_check_delay_s")); double[] current = ReadLoads();
                double delta = current.Select((v, i) => Math.Abs(v - previous[i])).Max();
                if (delta <= ini.Number("leveling", "settle_load_delta")) return true;
                previous = current;
            }
            Write("Loads did not settle"); return false;
        }
        double[] Deviations(double[] values)
        {
            int at = Array.IndexOf(axes, FixedAxis); double target = at >= 0 ? values[at] : values.Average();
            return values.Select((v, i) => { double b; return axes[i] == FixedAxis ? 0 : v - target - (bias.TryGetValue(axes[i], out b) ? b : 0); }).ToArray();
        }
        double Tolerance(double target) { return Math.Max(target * ini.Number("leveling", "load_rate_units_per_percent"), ini.Number("leveling", "minimum_tolerance")); }
        sealed class Verification { public double[] Loads; public double Score; public bool Passed, Stable; }
        Verification Verify(double target)
        {
            double[][] batches = Enumerable.Range(0, ini.Int("leveling", "verify_batches")).Select(_ => ReadLoads()).ToArray();
            var loads = new double[axes.Length]; double range = 0;
            for (int i = 0; i < axes.Length; i++)
            { double[] values = batches.Select(b => b[i]).OrderBy(v => v).ToArray(); loads[i] = values[values.Length / 2]; range = Math.Max(range, values.Last() - values.First()); }
            double score = Deviations(loads).Select(Math.Abs).Max(), tolerance = Tolerance(target);
            bool stable = range <= Math.Max(tolerance * 2, 20), passed = stable && score <= tolerance;
            Write(string.Format(System.Globalization.CultureInfo.InvariantCulture, "Verify {0}, score={1:F2}, range={2:F2}, loads=[{3}]", passed ? "OK" : stable ? "WARNING" : "UNSTABLE", score, range, string.Join(",", loads)));
            return new Verification { Loads = loads, Score = score, Passed = passed, Stable = stable };
        }
        void Restore(double[] offsets, bool all)
        {
            if (all)
            {
                Array.Copy(offsets, engine.Offsets, 7); Apply(); Settle(); return;
            }
            for (int i = 0; i < axes.Length; i++) if (axes[i] != FixedAxis && engine.Offsets[axes[i] - 1] != offsets[axes[i] - 1]) Move(axes[i], offsets[axes[i] - 1]);
            Settle();
        }
        double[] Stage(string name, double[] initial)
        {
            string s = "leveling_stage_" + name;
            double step = ini.Number(s, "step_mm"), kp = ini.Number(s, "kp"), alpha = ini.Number(s, "filter_alpha"), lower = ini.Number(s, "lower_step_factor"), weight = ini.Number(s, "raw_weight"), integralGain = ini.Number(s, "integral_gain"), target = ini.Number(s, "target_tolerance"), tolerance = Tolerance(target), minimum = Math.Max(1, step * 20);
            bool lowering = ini.Bool(s, "allow_lowering");
            double[] filtered = initial == null ? null : (double[])initial.Clone(), bestOffsets = (double[])engine.Offsets.Clone(), integral = new double[axes.Length];
            double best = double.PositiveInfinity, improvement = double.PositiveInfinity; int noImprovement = 0;
            for (int n = 1; n <= ini.Int(s, "max_iterations"); n++)
            {
                Check(); double[] current = ReadLoads();
                filtered = filtered == null ? (double[])current.Clone() : current.Select((v, i) => alpha * v + (1 - alpha) * filtered[i]).ToArray();
                double[] dev = Deviations(filtered), raw = Deviations(current);
                double score = dev.Select(Math.Abs).Max() * (1 - weight) + raw.Select(Math.Abs).Max() * weight;
                if (score < best) { best = score; bestOffsets = (double[])engine.Offsets.Clone(); }
                if (score < improvement - minimum) { improvement = score; noImprovement = 0; } else noImprovement++;
                Write(name + " " + n + ": score=" + score.ToString("F3", System.Globalization.CultureInfo.InvariantCulture));
                if (dev.All(v => Math.Abs(v) <= tolerance) && raw.All(v => Math.Abs(v) <= Math.Max(tolerance * 2, 20))) break;
                if (noImprovement >= 7) break;
                double[] corrections = dev.Select((v, i) => (1 - weight) * v + weight * raw[i]).ToArray();
                if (integralGain != 0) for (int i = 0; i < axes.Length; i++) { integral[i] = Math.Max(-80, Math.Min(80, .85 * integral[i] + corrections[i])); corrections[i] += integralGain * integral[i]; }
                bool severe = corrections.Any(v => v < -Math.Max(tolerance * 2, 15));
                var deltas = new Dictionary<int, double>();
                for (int i = 0; i < axes.Length; i++)
                {
                    if (axes[i] == FixedAxis || Math.Abs(corrections[i]) <= tolerance) continue;
                    double delta;
                    if (corrections[i] < 0) delta = Math.Min(-kp * corrections[i], step);
                    else if (lowering) delta = Math.Max(-kp * corrections[i], -step * lower * (severe ? .5 : 1));
                    else continue;
                    if (Math.Abs(delta) >= .001) deltas[axes[i]] = delta;
                }
                if (lowering && deltas.Count > 0 && FixedAxis == 0)
                { double average = deltas.Values.Sum() / axes.Length; foreach (int a in deltas.Keys.ToArray()) deltas[a] -= average; }
                bool moved = false;
                foreach (var pair in deltas) if (Math.Abs(pair.Value) >= .001)
                { Move(pair.Key, Math.Max(-6, Math.Min(6, engine.Offsets[pair.Key - 1] + pair.Value))); moved = true; }
                if (!moved) break;
                Settle();
            }
            if (!double.IsPositiveInfinity(best) && !bestOffsets.SequenceEqual(engine.Offsets)) Restore(bestOffsets, false);
            Verification verified = Verify(target);
            for (int n = 0; n < ini.Int("leveling", "verify_corrections"); n++)
            {
                if (verified.Passed || !verified.Stable) break;
                double[] dev = Deviations(verified.Loads), saved = (double[])engine.Offsets.Clone(); bool moved = false;
                for (int i = 0; i < axes.Length; i++)
                {
                    Check(); if (axes[i] == FixedAxis || Math.Abs(dev[i]) <= tolerance) continue;
                    double delta;
                    if (dev[i] < 0) delta = Math.Min(-kp * dev[i], step);
                    else if (lowering) delta = Math.Max(-kp * dev[i], -step * lower); else continue;
                    if (Math.Abs(delta) < .001) continue;
                    Move(axes[i], Math.Max(-6, Math.Min(6, engine.Offsets[axes[i] - 1] + delta))); moved = true;
                }
                if (!moved) break;
                Settle(); Verification corrected = Verify(target);
                if (corrected.Stable && corrected.Score < verified.Score) { verified = corrected; continue; }
                Restore(saved, false); break;
            }
            return verified.Loads;
        }
        public void Run()
        {
            FixedAxis = 0;
            try
            {
                Check(); bool loaded = Load(); if (loaded) Apply();
                axes = engine.Config.Enabled(4, 7); if (axes.Length == 0) return;
                double[] start = ReadLoads(); FixedAxis = axes[Array.IndexOf(start, start.Max())]; Write("Fixed axis: " + FixedAxis);
                Verification accepted = Verify(ini.Number("leveling_stage_coarse", "target_tolerance")); double[] acceptedOffsets = (double[])engine.Offsets.Clone();
                if (accepted.Stable && accepted.Passed && !loaded) Save();
                double[] filtered = accepted.Loads; int index = 0;
                foreach (string name in new[] { "coarse", "medium", "fine" })
                {
                    Stage(name, index++ == 0 ? null : filtered); Check();
                    Verification current = Verify(ini.Number("leveling_stage_" + name, "target_tolerance"));
                    if (current.Stable && (current.Score < accepted.Score || !accepted.Stable))
                    { acceptedOffsets = (double[])engine.Offsets.Clone(); accepted = current; filtered = current.Loads; Save(); Write(name + " accepted"); }
                    else
                    { Write(name + " rejected"); if (!engine.Offsets.SequenceEqual(acceptedOffsets)) Restore(acceptedOffsets, true); filtered = null; }
                }
                Write(accepted.Stable && accepted.Passed ? "Completed" : "Completed with warning");
            }
            catch (OperationCanceledException) { Save(); Write("Stopped; current offsets saved"); throw; }
            finally { FixedAxis = 0; }
        }
    }
}
