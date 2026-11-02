using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public static class Geometry
    {
        public static double[] Solve(double[,] matrix, double[] vector)
        {
            double[,] a = new double[3, 4];
            for (int r = 0; r < 3; r++) { for (int c = 0; c < 3; c++) a[r, c] = matrix[r, c]; a[r, 3] = vector[r]; }
            for (int c = 0; c < 3; c++)
            {
                int pivot = c; for (int r = c; r < 3; r++) if (Math.Abs(a[r, c]) > Math.Abs(a[pivot, c])) pivot = r;
                if (Math.Abs(a[pivot, c]) < 1e-12) throw new InvalidDataException("Degenerate rig geometry");
                for (int k = 0; k < 4; k++) { double temp = a[c, k]; a[c, k] = a[pivot, k]; a[pivot, k] = temp; }
                double divisor = a[c, c]; for (int k = 0; k < 4; k++) a[c, k] /= divisor;
                for (int r = 0; r < 3; r++) if (r != c) { double factor = a[r, c]; for (int k = 0; k < 4; k++) a[r, k] -= factor * a[c, k]; }
            }
            return new[] { a[0, 3], a[1, 3], a[2, 3] };
        }
        public static double[,] Points(Configuration config)
        {
            double front = config.Ini.Number("rig", "DistanceFrontDrivesLeft2Right") / 2,
                rear = config.Ini.Number("rig", "DistanceRearDrivesLeft2Right") / 2,
                length = config.Ini.Number("rig", "DistanceFront2RearDrives") / 2;
            return new double[,] { { length, -front }, { length, front }, { -length, rear }, { -length, -rear } };
        }
        public static double[][] State(Configuration config, double[] positions, double[] center)
        {
            var p = Points(config); double half = config.Ini.Number("rig", "DistanceFront2RearDrives") / 2;
            double[] plane = Solve(new double[,] { { 1, p[0, 0], p[0, 1] }, { 1, p[1, 0], p[1, 1] }, { 1, -half, 0 } }, new[] { positions[0], positions[1], (positions[2] + positions[3]) / 2 });
            var deviations = new double[4]; for (int i = 2; i < 4; i++) deviations[i] = plane[0] + plane[1] * p[i, 0] + plane[2] * p[i, 1] - positions[i];
            double[] loads = new double[4];
            if (Math.Max(deviations[2], deviations[3]) > 1e-6)
            {
                int lifted = deviations[2] >= deviations[3] ? 2 : 3;
                int[] support = Enumerable.Range(0, 4).Where(i => i != lifted).ToArray();
                var m = new double[3, 3]; for (int j = 0; j < 3; j++) { m[0, j] = 1; m[1, j] = p[support[j], 0]; m[2, j] = p[support[j], 1]; }
                double[] l = Solve(m, new[] { 1.0, center[0], center[1] }); for (int j = 0; j < 3; j++) loads[support[j]] = l[j];
            }
            else
            {
                var rows = new double[3, 4]; for (int j = 0; j < 4; j++) { rows[0, j] = 1; rows[1, j] = p[j, 0]; rows[2, j] = p[j, 1]; }
                var gram = new double[3, 3]; for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 4; k++) gram[i, j] += rows[i, k] * rows[j, k];
                double[] mult = Solve(gram, new[] { 1.0, center[0], center[1] });
                for (int i = 0; i < 4; i++) for (int j = 0; j < 3; j++) loads[i] += mult[j] * rows[j, i];
            }
            return new[] { deviations, loads };
        }
    }
    public sealed class SimAxis
    {
        public double Target, Position, Velocity, Speed;
        public int Accel = 400, Decel = 400, Raw = 65535;
        public static void Advance(SimAxis a, double dt)
        {
            double distance = a.Target - a.Position;
            if (Math.Abs(distance) < 1e-9 || a.Speed <= 0) { a.Position = a.Target; a.Velocity = 0; return; }
            double direction = distance > 0 ? 1 : -1, accel = a.Speed / (a.Accel / 1000.0), decel = a.Speed / (a.Decel / 1000.0);
            bool toward = a.Velocity * direction > 0;
            double stopping = toward ? a.Velocity * a.Velocity / (2 * decel) : 0, velocity;
            if (toward && stopping >= Math.Abs(distance)) velocity = direction * Math.Max(0, Math.Abs(a.Velocity) - decel * dt);
            else
            {
                double desired = direction * a.Speed, delta = accel * dt;
                velocity = a.Velocity < desired ? Math.Min(desired, a.Velocity + delta) : Math.Max(desired, a.Velocity - delta);
            }
            double position = a.Position + (a.Velocity + velocity) * .5 * dt;
            if ((a.Target - position) * direction <= 0) { position = a.Target; velocity = 0; }
            a.Position = position; a.Velocity = velocity;
        }
    }
    public sealed class Recorder : IDisposable
    {
        readonly Configuration config;
        public readonly object Gate = new object();
        public readonly SimAxis[] Axes;
        readonly Dictionary<int, SimAxis> pending = new Dictionary<int, SimAxis>();
        readonly double[] actual = new double[7], actualAt = new double[7];
        readonly DateTime?[] actualDates = new DateTime?[7];
        public double[] Center;
        public double[] CalibrationLoads;
        StreamWriter writer;
        bool running, readActual;
        public string CurrentFile { get; private set; }
        public Recorder(Configuration config)
        {
            this.config = config; Axes = Enumerable.Range(0, 7).Select(_ => new SimAxis()).ToArray();
            int center = (int)(((long)config.Ini.Int("simhub", "position_min") + config.Ini.Int("simhub", "position_max")) / 2);
            foreach (SimAxis axis in Axes) axis.Raw = center;
            Center = new[] { config.Ini.Number("rig", "CenterOfGravityFront2Rear"), config.Ini.Number("rig", "CenterOfGravityLeft2Right") };
            try
            {
                var calibration = Json.Read<Calibration>(config.PathFor("INI/simrig_load_values.json"));
                Center = LoadCenter(config, Enumerable.Range(4, 4).Select(a => calibration.loadRates[a.ToString()]).ToArray());
                CalibrationLoads = Enumerable.Range(4, 4).Select(a => calibration.loadRates[a.ToString()]).ToArray();
            }
            catch (FileNotFoundException) { } catch (Exception e) { Log.Write("Load calibration ignored: " + e.Message); }
        }
        public void Stage(int axis, double target, int accel, double speed, int decel, int raw)
        { lock (Gate) pending[axis] = new SimAxis { Target = target, Accel = Math.Max(1, accel), Speed = Math.Max(0, speed), Decel = Math.Max(1, decel), Raw = raw }; }
        public void Trigger()
        {
            lock (Gate)
            {
                foreach (var pair in pending)
                { SimAxis old = Axes[pair.Key - 1], next = pair.Value; next.Position = old.Position; next.Velocity = old.Velocity; Axes[pair.Key - 1] = next; }
                pending.Clear();
            }
        }
        public void ReadActual(bool enabled)
        { lock (Gate) { readActual = enabled; if (!enabled) { Array.Clear(actual, 0, 7); Array.Clear(actualAt, 0, 7); Array.Clear(actualDates, 0, 7); } } }
        public void SetActual(int axis, double mm)
        { lock (Gate) if (readActual) { actual[axis - 1] = mm; actualAt[axis - 1] = Timing.Now; actualDates[axis - 1] = DateTime.Now; } }
        public double[] ActualSnapshot() { lock (Gate) return (double[])actual.Clone(); }
        public void SetRunning(bool value)
        {
            lock (Gate)
            {
                if (value && !running)
                {
                    foreach (SimAxis a in Axes) { a.Position = a.Target; a.Velocity = 0; }
                    Start();
                }
                else if (!value && running) Close();
                running = value;
            }
        }
        public void DeleteExpired()
        {
            string dir = config.PathFor("SimHubData"); Directory.CreateDirectory(dir);
            DateTime cutoff = DateTime.UtcNow.AddDays(-config.Ini.Number("simhub", "data_retention_days"));
            foreach (string path in Directory.GetFiles(dir, "*.csv"))
                try { if (File.GetLastWriteTimeUtc(path) < cutoff) File.Delete(path); } catch (IOException e) { Log.Write(e.Message); }
        }
        void Start()
        {
            DeleteExpired(); string name = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss"), path = config.PathFor("SimHubData/" + name + ".csv"); int suffix = 1;
            while (File.Exists(path)) path = config.PathFor("SimHubData/" + name + "_" + (suffix++).ToString("00") + ".csv");
            writer = new StreamWriter(new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(true)) { AutoFlush = true, NewLine = "\n" };
            CurrentFile = Path.GetFileName(path);
            var header = new List<string> { "Timestamp" };
            foreach (string prefix in new[] { "Value", "TargetPosition", "CalculatedActualPosition", "ActualPosition", "ActualPositionTimestamp", "ActualPositionAgeMs", "ActualVelocity" })
                header.AddRange(Enumerable.Range(1, 7).Select(a => prefix + a));
            foreach (string prefix in new[] { "GroundDeviation", "GroundLoad" }) header.AddRange(Enumerable.Range(4, 4).Select(a => prefix + a));
            writer.WriteLine(string.Join(";", header)); Log.Write("Recording " + CurrentFile);
        }
        static string F(double v, string format = "F6") { return v.ToString(format, CultureInfo.InvariantCulture); }
        public void Tick(double dt)
        {
            lock (Gate)
            {
                if (!running) return;
                foreach (SimAxis axis in Axes) SimAxis.Advance(axis, dt);
                double[][] ground = Geometry.State(config, Enumerable.Range(3, 4).Select(i => Axes[i].Position - config.Axes[i].Zero).ToArray(), Center);
                var row = new List<string> { DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture) };
                row.AddRange(Axes.Select(a => a.Raw.ToString(CultureInfo.InvariantCulture)));
                row.AddRange(Axes.Select((a, i) => F(a.Target - config.Axes[i].Zero)));
                row.AddRange(Axes.Select((a, i) => F(a.Position - config.Axes[i].Zero)));
                row.AddRange(actual.Select(v => F(v)));
                row.AddRange(actualDates.Select(v => v.HasValue ? v.Value.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture) : ""));
                double now = Timing.Now;
                row.AddRange(actualDates.Select((v, i) => v.HasValue ? F(Math.Max(0, (now - actualAt[i]) * 1000), "F3") : ""));
                row.AddRange(Axes.Select(a => F(a.Velocity))); row.AddRange(ground[0].Select(v => F(v))); row.AddRange(ground[1].Select(v => F(v, "F9")));
                writer.WriteLine(string.Join(";", row));
            }
        }
        public static double[] LoadCenter(Configuration config, double[] loads)
        {
            if (loads.Length != 4 || loads.Any(v => v <= 0 || double.IsNaN(v) || double.IsInfinity(v))) throw new InvalidDataException("Four positive load values required");
            var p = Geometry.Points(config); double sum = loads.Sum();
            return new[] { Enumerable.Range(0, 4).Sum(i => loads[i] / sum * p[i, 0]), Enumerable.Range(0, 4).Sum(i => loads[i] / sum * p[i, 1]) };
        }
        public void SaveLoads(double[] loads)
        {
            double[] center = LoadCenter(config, loads);
            var data = new Calibration { recordedAt = DateTime.Now.ToString("s"), loadRates = Enumerable.Range(4, 4).ToDictionary(a => a.ToString(), a => loads[a - 4]), normalizedLoads = Enumerable.Range(4, 4).ToDictionary(a => a.ToString(), a => loads[a - 4] / loads.Sum()), centerOfGravityFront2Rear = center[0], centerOfGravityLeft2Right = center[1] };
            Json.Save(config.PathFor("INI/simrig_load_values.json"), data);
            lock (Gate) { Center = center; CalibrationLoads = (double[])loads.Clone(); }
        }
        public sealed class Calibration
        {
            public string recordedAt { get; set; }
            public Dictionary<string, double> loadRates { get; set; }
            public Dictionary<string, double> normalizedLoads { get; set; }
            public double centerOfGravityFront2Rear { get; set; }
            public double centerOfGravityLeft2Right { get; set; }
        }
        void Close() { if (writer != null) { writer.Dispose(); writer = null; Log.Write("Recording stopped"); } }
        public void Dispose() { lock (Gate) { Close(); running = false; } }
    }
    // Simulated drive replies travel through the same RTU encoder and workers.
    // No SerialPort is constructed in --simulate mode.
    public sealed class VirtualWire : IWire
    {
        readonly Dictionary<int, Dictionary<int, int>> registers = new Dictionary<int, Dictionary<int, int>>();
        readonly HashSet<int> homed = new HashSet<int>();
        readonly object gate = new object();
        public readonly List<byte[]> Frames = new List<byte[]>();
        public Func<byte[], byte[]> Override;
        public VirtualWire(IEnumerable<int> axes, bool highFirst = false)
        {
            foreach (int axis in axes) registers[axis] = new Dictionary<int, int> { { 0x0a06, highFirst ? 1 : 0 }, { 0x0438, 9 }, { 0x0439, 0 }, { 0x0401, 1 }, { 0x410a, 1 }, { 0x4005, 16 }, { 0x4007, 100 }, { 0x0312, 10 } };
        }
        public void Set(int axis, int address, int value) { lock (gate) registers[axis][address] = value; }
        int Get(int axis, int address) { int v; return registers[axis].TryGetValue(address, out v) ? v : 0; }
        public byte[] Exchange(byte[] frame, int expected, double timeout, bool wait)
        {
            lock (gate)
            {
                Frames.Add((byte[])frame.Clone());
                if (Override != null) { byte[] result = Override(frame); if (result != null) return result; }
                Rtu.Check(frame); int axis = frame[0], fc = frame[1], address = frame[2] << 8 | frame[3], value = frame[4] << 8 | frame[5];
                if (fc == 3)
                {
                    var reply = new List<byte> { (byte)axis, 3, (byte)(value * 2) };
                    for (int i = 0; i < value; i++) { int v = Get(axis, address + i); reply.Add((byte)(v >> 8)); reply.Add((byte)v); }
                    return Rtu.Seal(reply);
                }
                foreach (int id in axis == 0 ? registers.Keys.ToArray() : new[] { axis })
                {
                    if (fc == 6) registers[id][address] = value;
                    else if (fc == 16) for (int i = 0; i < value; i++) registers[id][address + i] = frame[7 + i * 2] << 8 | frame[8 + i * 2];
                    if (fc == 6 && address == 0x0411) registers[id][0x410a] = value == 0 ? 0 : 1;
                    if (fc == 6 && address == 0x0419 && value == 1 && Get(id, 0x0411) == 1)
                    {
                        homed.Add(id); registers[id][0x4005] = 0; registers[id][0x410a] = 1;
                        registers[id][0x4016] = Get(id, 0x100b); registers[id][0x4017] = Get(id, 0x100c);
                    }
                    if (fc == 6 && address == 0x0415 && value == 1)
                    { registers[id][0x4016] = Get(id, 0x1106); registers[id][0x4017] = Get(id, 0x1107); }
                }
                if (!wait || axis == 0) return new byte[0];
                return fc == 6 ? (byte[])frame.Clone() : Rtu.Seal(frame.Take(6));
            }
        }
        public void Dispose() { }
    }
}
