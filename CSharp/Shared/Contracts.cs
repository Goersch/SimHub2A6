using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;

namespace SimHub2SimRig.Shared
{
    public static class Json
    {
        public static string Encode(object value) { return new JavaScriptSerializer { MaxJsonLength = 16000000 }.Serialize(value); }
        public static T Decode<T>(string value) { return new JavaScriptSerializer { MaxJsonLength = 16000000 }.Deserialize<T>(value); }
        public static T Read<T>(string path) { return Decode<T>(File.ReadAllText(path, Encoding.UTF8)); }
        public static void Save(string path, object value)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, Encode(value), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
        }
    }
    public sealed class Request
    {
        public string HmiSession { get; set; }
        public string Operation { get; set; }
        public string FileName { get; set; }
        public string Mode { get; set; }
        public int FirstAxis { get; set; }
        public int LastAxis { get; set; }
        public int Position { get; set; }
        public int[] Axes { get; set; }
        public double Seconds { get; set; }
    }
    public sealed class Reply
    {
        public bool Ok { get; set; }
        public string Error { get; set; }
        public Status Status { get; set; }
        public string[] Files { get; set; }
        public Sample[] Samples { get; set; }
    }
    public sealed class AxisStatus
    {
        public int Axis { get; set; }
        public bool Enabled { get; set; }
        public bool Homed { get; set; }
        public double Offset { get; set; }
        public double Load { get; set; }
        public int Error { get; set; }
        public int Servo { get; set; }
        public double Target { get; set; }
        public double Calculated { get; set; }
        public double Actual { get; set; }
        public double Velocity { get; set; }
    }
    public sealed class GreaseEntry
    {
        public string lastGreaseAt { get; set; }
        public double playtimeMinutes { get; set; }
    }
    public sealed class Status
    {
        public int ProcessId { get; set; }
        public bool Simulated { get; set; }
        public bool Connected { get; set; }
        public bool GameRunning { get; set; }
        public bool Initialized { get; set; }
        public bool Maintenance { get; set; }
        public bool Analysis { get; set; }
        public bool Busy { get; set; }
        public string Activity { get; set; }
        public string LastError { get; set; }
        public string[] Messages { get; set; }
        public string Mode { get; set; }
        public bool Leveling { get; set; }
        public int FixedAxis { get; set; }
        public bool Greasing { get; set; }
        public int GreaseFirst { get; set; }
        public int GreaseLast { get; set; }
        public string Playback { get; set; }
        public string PlaybackFile { get; set; }
        public double PlaybackSeconds { get; set; }
        public AxisStatus[] Axes { get; set; }
        public Dictionary<string, GreaseEntry> Grease { get; set; }
        public double[] TriggerIntervals { get; set; }
        public int[] LatestPositions { get; set; }
        public double[] LoadCenter { get; set; }
        public double[] CalibrationLoads { get; set; }
    }
    public sealed class Sample
    {
        public double Seconds { get; set; }
        public double[] Target { get; set; }
        public double[] Calculated { get; set; }
        public double[] Actual { get; set; }
        public double[] Ground { get; set; }
    }
    public static class Protocol
    {
        public static string PipeName { get { return "SimHub2SimRig.v1." + WindowsIdentity.GetCurrent().User.Value; } }
        public static string ReadLine(TextReader reader)
        {
            var result = new StringBuilder();
            int c;
            while ((c = reader.Read()) != -1)
            {
                if (c == '\n') return result.ToString();
                if (result.Length >= 16000000) throw new InvalidDataException("IPC message too large");
                if (c != '\r') result.Append((char)c);
            }
            if (result.Length != 0) throw new EndOfStreamException("Incomplete IPC message");
            return null;
        }
    }
    // This assembly has no dependency on the motion engine or serial ports.
    public sealed class ComClient
    {
        readonly string pipeName;
        public ComClient(string name = null) { pipeName = name ?? Protocol.PipeName; }
        public Reply Send(Request request, int timeoutMilliseconds = 15000)
        {
            using (var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous))
            {
                pipe.Connect(1200);
                using (var writer = new StreamWriter(pipe, new UTF8Encoding(false), 4096, true) { AutoFlush = true })
                using (var reader = new StreamReader(pipe, Encoding.UTF8, false, 4096, true))
                {
                    writer.WriteLine(Json.Encode(request));
                    // Bound both failed backend calls and stalled peers.
                    var read = System.Threading.Tasks.Task.Factory.StartNew(() => Protocol.ReadLine(reader));
                    if (!read.Wait(timeoutMilliseconds)) { pipe.Dispose(); throw new TimeoutException("SimHub2SimRigCom antwortet nicht."); }
                    string line = read.Result;
                    if (line == null) throw new EndOfStreamException("SimHub2SimRigCom disconnected");
                    return Json.Decode<Reply>(line);
                }
            }
        }
    }
}
