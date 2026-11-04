using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Ports;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public static class Timing
    {
        public static double Now { get { return Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency; } }
        public static void Sleep(double seconds)
        {
            if (seconds <= 0) return;
            double end = Now + seconds;
            if (seconds >= .002) Thread.Sleep(Math.Max(1, (int)(seconds * 1000) - 1));
            while (Now < end) Thread.SpinWait(30);
        }
    }
    public static class Log
    {
        static readonly object gate = new object();
        static readonly Queue<string> recent = new Queue<string>();
        public static string[] Recent() { lock (gate) return recent.ToArray(); }
        public static string DirectoryPath;
        public static bool ConsoleEnabled = true;
        public static void Write(string message)
        {
            lock (gate)
            {
                string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + message;
                recent.Enqueue(line); while (recent.Count > 3) recent.Dequeue();
                if (ConsoleEnabled) SafeConsole.WriteLine(line);
                if (DirectoryPath != null)
                    try { Directory.CreateDirectory(DirectoryPath); File.AppendAllText(Path.Combine(DirectoryPath, "simhub2simrig-com.log"), line + Environment.NewLine); }
                    catch (IOException e) { if (ConsoleEnabled) SafeConsole.WriteLine("Logging: " + e.Message); }
                    catch (UnauthorizedAccessException e) { if (ConsoleEnabled) SafeConsole.WriteLine("Logging: " + e.Message); }
            }
        }
    }
    public sealed class CrcException : IOException { public CrcException() : base("Modbus CRC error") { } }
    public static class Rtu
    {
        public static ushort Crc(byte[] bytes, int length = -1)
        {
            int crc = 0xffff;
            for (int i = 0; i < (length < 0 ? bytes.Length : length); i++)
            { crc ^= bytes[i]; for (int b = 0; b < 8; b++) crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xa001 : crc >> 1; }
            return (ushort)crc;
        }
        public static byte[] Seal(IEnumerable<byte> data)
        {
            var result = data.ToList(); ushort crc = Crc(result.ToArray());
            result.Add((byte)crc); result.Add((byte)(crc >> 8)); return result.ToArray();
        }
        public static byte[] Read(int axis, int address, int count = 1) { return Seal(new byte[] { checked((byte)axis), 3, (byte)(address >> 8), (byte)address, (byte)(count >> 8), (byte)count }); }
        public static byte[] Write(int axis, int address, int value) { return Seal(new byte[] { checked((byte)axis), 6, (byte)(address >> 8), (byte)address, (byte)(value >> 8), (byte)value }); }
        public static byte[] Multi(int axis, int address, int[] values)
        {
            if (values.Length == 0 || values.Length > 123) throw new ArgumentOutOfRangeException("values");
            var data = new List<byte> { checked((byte)axis), 16, (byte)(address >> 8), (byte)address, (byte)(values.Length >> 8), (byte)values.Length, (byte)(values.Length * 2) };
            foreach (int value in values) { data.Add((byte)(value >> 8)); data.Add((byte)value); } return Seal(data);
        }
        public static int[] Words(long value, bool highFirst)
        {
            uint bits = unchecked((uint)value); int high = (int)(bits >> 16), low = (int)(bits & 65535);
            return highFirst ? new[] { high, low } : new[] { low, high };
        }
        public static void Check(byte[] response)
        {
            if (response.Length < 3) throw new IOException("Modbus response too short");
            if (Crc(response, response.Length - 2) != (response[response.Length - 2] | response[response.Length - 1] << 8)) throw new CrcException();
        }
        public static void CheckError(byte[] rx)
        {
            if (rx.Length == 5 && (rx[1] & 128) != 0) throw new IOException(string.Format("Modbus exception 0x{0:X2} (function 0x{1:X2})", rx[2], rx[1] & 127));
            if (rx.Length >= 8 && rx[1] == 0x90) throw new IOException("Drive-Error " + BitConverter.ToString(rx, 2, 4));
        }
    }
    public interface IWire : IDisposable { byte[] Exchange(byte[] frame, int expected, double timeout, bool wait); }
    public sealed class SerialWire : IWire
    {
        readonly SerialPort port;
        readonly double gap, poll;
        double next;
        public SerialWire(BusConfig bus, Configuration config)
        {
            var ini = config.Ini;
            port = new SerialPort(bus.Port, ini.Int("modbus", "baud"), Parity.None, 8, StopBits.One);
            port.Handshake = Handshake.None;
            // Match pyserial's default line states, including adapters that
            // derive their enable/power state from RTS or DTR.
            port.DtrEnable = true; port.RtsEnable = true;
            port.ReadTimeout = port.WriteTimeout = Math.Max(1, (int)(ini.Number("modbus", "serial_timeout_s") * 1000));
            gap = ini.Number("modbus", "inter_frame_delay_s"); poll = ini.Number("modbus", "response_poll_delay_s");
            try { port.Open(); } catch { port.Dispose(); throw; }
        }
        public byte[] Exchange(byte[] frame, int expected, double timeout, bool wait)
        {
            Timing.Sleep(next - Timing.Now);
            port.DiscardInBuffer();
            port.Write(frame, 0, frame.Length);
            port.BaseStream.Flush();
            var bytes = new List<byte>(); double start = Timing.Now;
            if (wait) while (Timing.Now - start < timeout)
            {
                int available = port.BytesToRead;
                if (available > 0)
                {
                    byte[] part = new byte[available]; int read = port.Read(part, 0, available); bytes.AddRange(part.Take(read));
                    if (expected > 0 && bytes.Count >= expected) break;
                }
                else Timing.Sleep(poll);
            }
            next = Timing.Now + gap; return bytes.ToArray();
        }
        public void Dispose() { port.Dispose(); }
    }
    // A bus worker owns its serial port. Broadcasts drain every worker first.
    public sealed class Modbus : IDisposable
    {
        sealed class Work
        {
            public byte[] Frame; public int Expected; public double Timeout; public bool Wait;
            public Action<byte[]> Validate;
            public readonly TaskCompletionSource<byte[]> Completion = new TaskCompletionSource<byte[]>();
            public volatile bool Cancelled;
        }
        sealed class Bus
        {
            public IWire Wire;
            public readonly BlockingCollection<Work> Queue = new BlockingCollection<Work>();
            public Task Worker;
            public void Start()
            {
                Worker = Task.Factory.StartNew(() => {
                    foreach (Work w in Queue.GetConsumingEnumerable())
                    {
                        if (w.Cancelled) { w.Completion.TrySetCanceled(); continue; }
                        try { byte[] rx = Wire.Exchange(w.Frame, w.Expected, w.Timeout, w.Wait); if (w.Validate != null) w.Validate(rx); w.Completion.TrySetResult(rx); }
                        catch (Exception e) { w.Completion.TrySetException(e); }
                    }
                }, TaskCreationOptions.LongRunning);
            }
        }
        readonly Configuration config;
        readonly object gate = new object(), orderGate = new object();
        readonly Dictionary<int, Bus> routes = new Dictionary<int, Bus>();
        readonly List<Bus> buses = new List<Bus>();
        readonly List<Work> queued = new List<Work>();
        bool? highFirst;
        public bool Connected { get { lock (gate) return buses.Count != 0; } }
        public Modbus(Configuration config) { this.config = config; }
        public void Connect(Func<BusConfig, IWire> factory, Action<BusConfig, Exception> retryOpen = null)
        {
            lock (gate)
            {
                if (buses.Count != 0) throw new InvalidOperationException("Already connected");
                try
                {
                    foreach (BusConfig bc in config.Buses)
                    {
                        if (factory == null && buses.Count > 0) Timing.Sleep(1);
                        var b = new Bus { Wire = OpenWire(bc, factory, retryOpen) };
                        buses.Add(b); foreach (int axis in bc.Axes) routes.Add(axis, b); b.Start();
                    }
                    highFirst = null;
                }
                catch { Dispose(); throw; }
            }
        }
        IWire OpenWire(BusConfig bus, Func<BusConfig, IWire> factory, Action<BusConfig, Exception> retryOpen)
        {
            bool failed = false;
            while (true)
            {
                try
                {
                    IWire wire = factory == null ? new SerialWire(bus, config) : factory(bus);
                    if (failed) Log.Write("COM port " + bus.Port + " opened successfully");
                    return wire;
                }
                catch (IOException e) { if (retryOpen == null) throw; retryOpen(bus, e); }
                catch (UnauthorizedAccessException e) { if (retryOpen == null) throw; retryOpen(bus, e); }
                failed = true;
            }
        }
        byte[] Await(Work w, double budget)
        {
            try
            {
                if (!w.Completion.Task.Wait(TimeSpan.FromSeconds(budget))) { w.Cancelled = true; throw new TimeoutException("Modbus worker timeout"); }
                return w.Completion.Task.Result;
            }
            catch (AggregateException e) { throw e.GetBaseException(); }
        }
        double Budget(Work w) { return Math.Max(config.Ini.Number("modbus", "task_wait_timeout_s"), w.Wait ? w.Timeout + config.Ini.Number("modbus", "serial_timeout_s") + 1 : 0); }
        void Drain()
        {
            // Track all asynchronous operations; synchronous operations are also
            // submitted under the gate and tracked in pending until complete.
            double end = Timing.Now + config.Ini.Number("modbus", "queue_drain_timeout_s");
            try { foreach (Work w in pending.ToArray()) Await(w, Math.Max(0, end - Timing.Now)); }
            finally { pending.RemoveAll(w => w.Completion.Task.IsCompleted); queued.Clear(); }
        }
        readonly List<Work> pending = new List<Work>();
        byte[] Transfer(byte[] frame, int expected, double timeout, bool wait, bool queue = false, Action<byte[]> validate = null)
        {
            var w = new Work { Frame = frame, Expected = expected, Timeout = timeout, Wait = wait, Validate = validate };
            lock (gate)
            {
                if (frame[0] == 0)
                {
                    if (queue) throw new ArgumentException("Broadcast cannot be queued");
                    if (buses.Count == 0) throw new InvalidOperationException("Modbus disconnected");
                    Drain();
                    var all = new List<Work>();
                    foreach (Bus bus in buses) { var b = new Work { Frame = frame, Expected = expected, Timeout = timeout, Wait = wait }; all.Add(b); bus.Queue.Add(b); }
                    Exception error = null; byte[] first = null;
                    double deadline = Timing.Now + Budget(w);
                    foreach (Work b in all) try { byte[] rx = Await(b, Math.Max(0, deadline - Timing.Now)); if (first == null) first = rx; } catch (Exception e) { error = error ?? e; }
                    if (error != null) { foreach (Work b in all) b.Cancelled = true; throw error; }
                    return first;
                }
                Bus target;
                if (!routes.TryGetValue(frame[0], out target)) throw new InvalidOperationException("No Modbus route for axis " + frame[0]);
                // Preserve asynchronous errors until the next drain; completed
                // synchronous tasks need not accumulate over hours of motion.
                pending.RemoveAll(p => p.Completion.Task.Status == TaskStatus.RanToCompletion);
                target.Queue.Add(w); pending.Add(w);
                if (queue) { queued.Add(w); return null; }
            }
            try { return Await(w, Budget(w)); }
            finally { lock (gate) pending.Remove(w); }
        }
        public int[] Read(int axis, int address, int count = 1)
        {
            byte[] rx = Transfer(Rtu.Read(axis, address, count), 5 + count * 2, 1 + .01 * count, true);
            if (rx.Length == 0) throw new IOException("Read: no response");
            Rtu.Check(rx); Rtu.CheckError(rx);
            if (rx[0] != axis || rx[1] != 3 || rx.Length < 5 + count * 2 || rx[2] < count * 2) throw new IOException("Unexpected read response");
            return Enumerable.Range(0, count).Select(i => (rx[3 + i * 2] << 8) | rx[4 + i * 2]).ToArray();
        }
        public bool HighFirst(int axis)
        {
            lock (orderGate) { if (!highFirst.HasValue) highFirst = (Read(axis == 0 ? 1 : axis, 0x0a06)[0] & 1) == 1; return highFirst.Value; }
        }
        public int ReadI16(int axis, int address) { return unchecked((short)Read(axis, address)[0]); }
        public long ReadU32(int axis, int address, bool signed = false)
        {
            int[] words = Read(axis, address, 2); bool high = HighFirst(axis);
            uint value = ((uint)words[high ? 0 : 1] << 16) | (uint)words[high ? 1 : 0];
            return signed ? (long)unchecked((int)value) : value;
        }
        public float ReadF32(int axis, int address) { return BitConverter.ToSingle(BitConverter.GetBytes((uint)ReadU32(axis, address)), 0); }
        static void ValidateWrite(byte[] rx, int axis, int function)
        {
            // Preserve the legacy tolerance for missing write replies.
            if (rx.Length == 0 || (function == 16 && rx.Length < 5)) { Log.Write("Modbus write: missing/short reply on axis " + axis); return; }
            Rtu.Check(rx); Rtu.CheckError(rx);
            if (rx.Length < 8 || rx[0] != axis || rx[1] != function) throw new IOException("Unexpected write response");
        }
        public void Write(int axis, int address, int value, bool check = true, bool queue = false)
        {
            bool wait = check && axis != 0;
            Action<byte[]> validator = rx => { if (wait) ValidateWrite(rx, axis, 6); };
            if (queue) { Transfer(Rtu.Write(axis, address, value), 8, .2, wait, true, validator); return; }
            for (int attempt = 0; ; attempt++)
            {
                byte[] rx = Transfer(Rtu.Write(axis, address, value), 8, .2, wait);
                try { validator(rx); return; } catch (CrcException) { if (attempt == 3) throw; Timing.Sleep(.01 * (attempt + 1)); }
            }
        }
        public void Multi(int axis, int address, int[] values, bool check = true, bool queue = false)
        {
            bool wait = check && axis != 0;
            Action<byte[]> validator = rx => { if (wait) ValidateWrite(rx, axis, 16); };
            byte[] response = Transfer(Rtu.Multi(axis, address, values), 8, 1, wait, queue, queue ? validator : null);
            if (!queue) validator(response);
        }
        public void U32(int axis, int address, long value, bool check = true, bool queue = false) { Multi(axis, address, Rtu.Words(value, HighFirst(axis)), check, queue); }
        public void TwoU32(int axis, int address, long first, long second, bool check = true, bool queue = false) { Multi(axis, address, Rtu.Words(first, HighFirst(axis)).Concat(Rtu.Words(second, HighFirst(axis))).ToArray(), check, queue); }
        public void F32(int axis, int address, float value) { U32(axis, address, BitConverter.ToUInt32(BitConverter.GetBytes(value), 0)); }
        public void Dispose()
        {
            lock (gate)
            {
                Exception error = null;
                try { Drain(); } catch (Exception e) { error = e; }
                foreach (Bus bus in buses) bus.Queue.CompleteAdding();
                foreach (Bus bus in buses)
                {
                    try
                    {
                        if (error != null) bus.Wire.Dispose();
                        if (!bus.Worker.Wait(TimeSpan.FromSeconds(config.Ini.Number("modbus", "worker_join_timeout_s")))) error = error ?? new TimeoutException("Modbus worker did not stop");
                    }
                    catch (Exception e) { error = error ?? e; }
                    finally { try { bus.Wire.Dispose(); } catch (Exception e) { error = error ?? e; } }
                }
                buses.Clear(); routes.Clear(); pending.Clear(); queued.Clear(); highFirst = null;
                if (error != null) throw error;
            }
        }
    }
}
