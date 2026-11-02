using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public sealed class Recordings
    {
        readonly Configuration config;
        public Recordings(Configuration config) { this.config = config; }
        public string Resolve(string name)
        {
            if (string.IsNullOrWhiteSpace(name) || name != Path.GetFileName(name) || !name.EndsWith(".csv", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Select a CSV file name from SimHubData");
            string dir = Path.GetFullPath(config.PathFor("SimHubData")) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(dir, name));
            if (!path.StartsWith(dir, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Invalid recording path");
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new ArgumentException("Recording links are not supported");
            return path;
        }
        public string[] Files()
        {
            string dir = config.PathFor("SimHubData"); Directory.CreateDirectory(dir);
            return Directory.GetFiles(dir, "*.csv").OrderByDescending(p => {
                DateTime date; string n = Path.GetFileNameWithoutExtension(p);
                return n.Length >= 19 && DateTime.TryParseExact(n.Substring(0, 19), "yyyy-MM-dd_HH-mm-ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out date) ? date : File.GetLastWriteTime(p);
            }).ThenByDescending(p => p, StringComparer.OrdinalIgnoreCase).Select(Path.GetFileName).ToArray();
        }
        static string[] Csv(string line)
        {
            var fields = new List<string>(); var field = new StringBuilder(); bool quoted = false;
            for (int i = 0; i < line.Length; i++)
            {
                char c = line[i];
                if (c == '"') { if (quoted && i + 1 < line.Length && line[i + 1] == '"') { field.Append('"'); i++; } else quoted = !quoted; }
                else if (c == ';' && !quoted) { fields.Add(field.ToString()); field.Clear(); }
                else field.Append(c);
            }
            if (quoted) throw new InvalidDataException("Unterminated CSV quote");
            fields.Add(field.ToString()); return fields.ToArray();
        }
        static double Value(Dictionary<string, string> row, string key, double fallback)
        {
            string raw; double v;
            return row.TryGetValue(key, out raw) && double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out v) && !double.IsNaN(v) && !double.IsInfinity(v) ? v : fallback;
        }
        public IEnumerable<Sample> Read(string name)
        {
            using (var file = new StreamReader(new FileStream(Resolve(name), FileMode.Open, FileAccess.Read, FileShare.ReadWrite), Encoding.UTF8, true))
            {
                string header = file.ReadLine(); if (header == null) yield break;
                string[] keys = Csv(header); string line; DateTime? start = null;
                while ((line = file.ReadLine()) != null)
                {
                    string[] fields; try { fields = Csv(line); } catch (InvalidDataException) { continue; }
                    if (fields.Length != keys.Length) continue;
                    var row = new Dictionary<string, string>(); for (int i = 0; i < keys.Length; i++) row[keys[i]] = fields[i];
                    string stamp; DateTime date;
                    if (!row.TryGetValue("Timestamp", out stamp) || !DateTime.TryParse(stamp, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out date)) continue;
                    var sample = new Sample { Target = new double[7], Calculated = new double[7], Actual = new double[7], Ground = new double[7] };
                    bool modern = row.ContainsKey("CalculatedActualPosition1"), valid = true;
                    for (int i = 0; i < 7; i++)
                    {
                        int a = i + 1;
                        sample.Target[i] = Value(row, "TargetPosition" + a, Value(row, "Value" + a, double.NaN) / config.Ini.Int("simhub", "position_max") * 200 - 100);
                        if (double.IsNaN(sample.Target[i])) { valid = false; break; }
                        sample.Calculated[i] = Value(row, (modern ? "CalculatedActualPosition" : "ActualPosition") + a, sample.Target[i]);
                        sample.Actual[i] = modern ? Value(row, "ActualPosition" + a, 0) : 0;
                        sample.Ground[i] = i >= 3 ? Value(row, "GroundDeviation" + a, Value(row, "GroundDistance" + a, 0)) : 0;
                    }
                    if (!valid) continue;
                    if (!start.HasValue) start = date;
                    sample.Seconds = (date - start.Value).TotalSeconds;
                    yield return sample;
                }
            }
        }
        public Sample[] Preview(string name, int limit = 6000)
        {
            // Bounded memory even for recordings of many hours; retain endpoints.
            var samples = new List<Sample>(); int stride = 1; long index = 0; Sample last = null;
            foreach (Sample s in Read(name))
            {
                last = s;
                if (index++ % stride == 0) samples.Add(s);
                if (samples.Count >= limit * 2) { samples = samples.Where((s2, i) => i % 2 == 0).ToList(); stride *= 2; }
            }
            while (samples.Count > limit - 1) samples = samples.Where((s, i) => i % 2 == 0).ToList();
            if (last != null && (samples.Count == 0 || samples[samples.Count - 1] != last)) samples.Add(last);
            return samples.ToArray();
        }
    }
}
