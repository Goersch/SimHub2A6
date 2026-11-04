using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public sealed class Engine : IDisposable
    {
        public readonly Configuration Config;
        public readonly Modbus Bus;
        public readonly MotionController Motion;
        public readonly Recorder Recorder;
        public readonly Recordings Recordings;
        public readonly double[] Offsets = new double[7], Previous = new double[7];
        public readonly object MotionGate = new object();
        public readonly ManualResetEventSlim Stop = new ManualResetEventSlim();
        public readonly ManualResetEventSlim OperationStop = new ManualResetEventSlim();
        readonly TaskCompletionSource<Reply> shutdownFinished = new TaskCompletionSource<Reply>();
        public readonly Leveler Leveler;
        readonly object stateGate = new object(), jobGate = new object(), latestGate = new object(), greaseGate = new object(), commandGate = new object();
        readonly Func<BusConfig, IWire> wireFactory;
        readonly HashSet<int> maintenancePlanners = new HashSet<int>(), faultAxes = new HashSet<int>();
        readonly Queue<double> intervals = new Queue<double>();
        readonly object hmiGate = new object();
        readonly Dictionary<Guid, double> hmiSessions = new Dictionary<Guid, double>();
        readonly List<Task> loops = new List<Task>();
        readonly AxisStatus[] telemetry = Enumerable.Range(1, 7).Select(a => new AxisStatus { Axis = a }).ToArray();
        readonly int[] latest = Enumerable.Repeat(65535, 7).ToArray();
        readonly List<IPEndPoint> shutdownReplies = new List<IPEndPoint>();
        Dictionary<string, GreaseEntry> grease;
        Task job;
        UdpClient udp;
        volatile bool initialized, initializing, connected, game, maintenance, analysis, shutdown, armed = true, busy;
        volatile bool leveling, greasing;
        volatile string mode = "simhub", activity = "", error = "";
        int greaseFirst, greaseLast, hubIndex;
        double lastHubCheck;
        readonly bool simulated;
        public readonly Playback Playback;
        public bool Initialized { get { return initialized; } }
        public bool GameRunning { get { return game; } }
        public void RequestShutdown()
        {
            shutdown = true; armed = false; OperationStop.Set(); Stop.Set();
        }
        public Engine(Configuration config, bool simulate, Func<BusConfig, IWire> factory = null)
        {
            Config = config; simulated = simulate;
            int center = (int)(((long)config.Ini.Int("simhub", "position_min") + config.Ini.Int("simhub", "position_max")) / 2);
            for (int i = 0; i < latest.Length; i++) latest[i] = center;
            wireFactory = factory ?? (simulate ? (Func<BusConfig, IWire>)(b => new VirtualWire(b.Axes)) : null);
            Bus = new Modbus(config); Motion = new MotionController(config, Bus); Recorder = new Recorder(config); Recordings = new Recordings(config);
            Leveler = new Leveler(this); Playback = new Playback(this);
            grease = LoadGrease();
        }
        public void Report(Exception e) { error = e.Message; Log.Write(e.ToString()); }
        public void Start()
        {
            // Reserve UDP before any drive command, so a second instance cannot
            // initialize hardware and only then discover the occupied endpoint.
            udp = new UdpClient();
            try { udp.Client.ExclusiveAddressUse = true; udp.Client.Bind(new IPEndPoint(IPAddress.Parse(Config.Ini.Get("simhub", "udp_ip")), Config.Ini.Int("simhub", "udp_port"))); udp.Client.ReceiveTimeout = 500; }
            catch { udp.Close(); throw; }
            Recorder.DeleteExpired();
            loops.Add(Task.Factory.StartNew(ReceiveLoop, TaskCreationOptions.LongRunning));
            loops.Add(Task.Factory.StartNew(SenderLoop, TaskCreationOptions.LongRunning));
            loops.Add(Task.Factory.StartNew(RecorderLoop, TaskCreationOptions.LongRunning));
            loops.Add(Task.Factory.StartNew(TelemetryLoop, TaskCreationOptions.LongRunning));
            Log.Write("Com ready - waiting for HMI INIT request");
        }
        void StartJob(string name, Action action)
        {
            lock (jobGate)
            {
                if (busy || Playback.State != "stopped") throw new InvalidOperationException("An operation is already running");
                if (shutdown) throw new InvalidOperationException("Shutdown in progress");
                busy = true; activity = name; error = ""; OperationStop.Reset();
                job = Task.Factory.StartNew(() => {
                    try { lock (MotionGate) action(); }
                    catch (OperationCanceledException) { Log.Write(name + " stopped"); }
                    catch (Exception e) { Report(e); }
                    finally { activity = ""; busy = false; }
                }, TaskCreationOptions.LongRunning);
            }
        }
        void EnsureIdle(bool requireDisconnected = false)
        {
            if (!initialized) throw new InvalidOperationException("A6 initialization not complete");
            if (busy || Playback.State != "stopped" || shutdown) throw new InvalidOperationException("An operation is already running");
            if (game || (requireDisconnected && connected)) throw new InvalidOperationException("SimHub is active");
        }
        public Reply Handle(Request request)
        {
            if (request != null && request.Operation != "hmi.detach") TouchHmi(request.HmiSession);
            if (request != null && (request.Operation == "status" || request.Operation == "files" || request.Operation == "preview")) return HandleCore(request);
            lock (commandGate) return HandleCore(request);
        }
        Reply HandleCore(Request request)
        {
            if (request == null || request.Operation == null) throw new ArgumentException("Missing IPC operation");
            if (!initialized && request.Operation != "status" && request.Operation != "initialize" && request.Operation != "hmi.detach" && request.Operation != "shutdown" && request.Operation != "shutdown.wait")
                throw new InvalidOperationException("A6 initialization not complete. Request INIT from the HMI first.");
            // All validation is repeated here; disabled HMI buttons are not an
            // authorization or a motion interlock.
            switch (request.Operation)
            {
                case "status": return new Reply { Ok = true, Status = Snapshot() };
                case "files": return new Reply { Ok = true, Files = Recordings.Files() };
                case "preview": return new Reply { Ok = true, Samples = Recordings.Preview(request.FileName) };
                case "initialize": EnsureIdleForInitialization(); Log.Write("INIT request received by HMI"); StartJob("Initialization", Initialize); break;
                case "mode":
                    if (request.Mode != "simhub" && request.Mode != "actual_positions" && request.Mode != "center") throw new ArgumentException("Invalid mode");
                    if (busy || Playback.State != "stopped" || maintenance || analysis) throw new InvalidOperationException("Motion mode is locked");
                    if (!initialized) throw new InvalidOperationException("A6 initialization not complete");
                    lock (MotionGate) { mode = request.Mode; Recorder.ReadActual(mode == "actual_positions"); }
                    if (mode == "center") StartJob("Center", Center); break;
                case "maintenance.open":
                    if (maintenance) break;
                    EnsureIdle(true); if (analysis) throw new InvalidOperationException("Analysis is open");
                    lock (MotionGate) { maintenance = true; armed = false; }
                    StartJob("Maintenance preparation", () => { foreach (int a in Config.Enabled()) maintenancePlanners.Add(a); }); break;
                case "maintenance.close":
                    if (!maintenance) break;
                    if (busy) throw new InvalidOperationException("Stop the maintenance operation first");
                    StartJob("Restore planners", () => { RestorePlanners(); maintenance = false; }); break;
                case "home":
                    RequireMaintenance(); ValidateGroup(request.FirstAxis, request.LastAxis);
                    StartJob("Homing", () => Home(request.FirstAxis, request.LastAxis)); break;
                case "move":
                    RequireMaintenance(); ValidateGroup(request.FirstAxis, request.LastAxis); ValidateRaw(request.Position);
                    StartJob("Maintenance movement", () => MaintenanceMove(request.FirstAxis, request.LastAxis, request.Position)); break;
                case "grease.start":
                    RequireMaintenance(); ValidateGroup(request.FirstAxis, request.LastAxis);
                    greaseFirst = request.FirstAxis; greaseLast = request.LastAxis;
                    StartJob("Greasing", () => Grease(request.FirstAxis, request.LastAxis)); break;
                case "grease.stop": if (greasing || activity == "Greasing") OperationStop.Set(); break;
                case "grease.reset":
                    EnsureIdle(); ValidateGroup(request.FirstAxis, request.LastAxis);
                    lock (greaseGate) { var entry = grease[GreaseKey(request.FirstAxis, request.LastAxis)]; entry.lastGreaseAt = DateTime.Now.ToString("s"); entry.playtimeMinutes = 0; SaveGrease(); } break;
                case "leveling.start":
                    EnsureIdle(true); if (analysis || maintenance || mode == "center") throw new InvalidOperationException("Leveling is locked");
                    StartJob("Leveling", () => { leveling = true; try { Leveler.Run(); } finally { leveling = false; } }); break;
                case "leveling.stop": if (leveling || activity == "Leveling") OperationStop.Set(); break;
                case "loads.save":
                    EnsureIdle(); if (maintenance || analysis) throw new InvalidOperationException("Close other dialogs first");
                    StartJob("Load measurement", CaptureLoads); break;
                case "analysis.open":
                    if (analysis) break;
                    EnsureIdle(true); if (maintenance) throw new InvalidOperationException("Maintenance is open"); lock (MotionGate) { analysis = true; armed = false; SetGame(false); } break;
                case "analysis.close":
                    if (!analysis) break;
                    Playback.Stop();
                    StartJob("Finish analysis", () => { Center(); analysis = false; armed = false; }); break;
                case "playback.play":
                    if (!analysis || maintenance || busy || !initialized || mode == "center" || shutdown) throw new InvalidOperationException("Playback is locked");
                    Recordings.Resolve(request.FileName);
                    Playback.Play(request.FileName, request.Axes); break;
                case "playback.pause": Playback.Pause(); break;
                case "playback.stop":
                    Playback.Stop();
                    if (analysis && !busy) StartJob("Center after playback", Center); break;
                case "playback.seek":
                    if (!analysis) throw new InvalidOperationException("Analysis is closed");
                    Playback.Seek(request.Seconds); break;
                case "hmi.detach":
                    // Compatibility with older HMI clients. HMI presence never
                    // changes Com's initialization, operations or motion state.
                    DisconnectHmi(request.HmiSession);
                    break;
                case "shutdown": RequestShutdown(); break;
                case "shutdown.wait":
                    RequestShutdown();
                    if (!shutdownFinished.Task.Wait(TimeSpan.FromSeconds(180))) throw new TimeoutException("Shutdown did not finish within 180 seconds");
                    return shutdownFinished.Task.Result;
                default: throw new ArgumentException("Unknown IPC operation: " + request.Operation);
            }
            return new Reply { Ok = true };
        }
        void EnsureIdleForInitialization() { if (initialized || busy || maintenance || analysis || game) throw new InvalidOperationException("Initialization unavailable"); }
        void TouchHmi(string session)
        {
            Guid id; if (!Guid.TryParse(session, out id)) return;
            lock (hmiGate)
            {
                if (!hmiSessions.ContainsKey(id)) Log.Write("HMI connected");
                hmiSessions[id] = Timing.Now;
            }
        }
        void DisconnectHmi(string session)
        {
            Guid id; if (!Guid.TryParse(session, out id)) return;
            lock (hmiGate) if (hmiSessions.Remove(id)) Log.Write("HMI disconnected");
        }
        void CheckHmiConnections()
        {
            lock (hmiGate)
                foreach (Guid id in hmiSessions.Where(p => Timing.Now - p.Value >= 5).Select(p => p.Key).ToArray())
                { hmiSessions.Remove(id); Log.Write("HMI disconnected"); }
        }
        void RequireMaintenance() { EnsureIdle(true); if (!maintenance) throw new InvalidOperationException("Open maintenance first"); }
        static void ValidateGroup(int first, int last)
        { if (!((first >= 1 && first <= 3 && first == last) || (first == 4 && last == 7))) throw new ArgumentException("Invalid axis group"); }
        void ValidateRaw(int raw) { if (raw < Config.Ini.Int("simhub", "position_min") || raw > Config.Ini.Int("simhub", "position_max")) throw new ArgumentOutOfRangeException("raw"); }
        public double TargetMm(int axis, int raw)
        {
            var a = Config.Axes[axis - 1];
            // Keep the Python operation order (multiplication/division changes
            // at half-unit boundaries would change the resulting RTU payload).
            return a.Stroke / Config.RawMax * raw - a.Stroke / 2 + a.Zero + Offsets[axis - 1];
        }
        public bool Go(int axis, int raw, bool trigger = true, bool queued = false)
        {
            if (!Config.Axes[axis - 1].Enabled) return false;
            double mm = TargetMm(axis, raw);
            if (Math.Abs(Previous[axis - 1] - mm) <= 1) return false;
            Previous[axis - 1] = mm; Motion.Position(axis, mm, trigger, queued); return true;
        }
        public bool HandlePosition(int axis, int raw, bool trigger = false, bool playback = false)
        {
            if (mode == "center" || maintenance || shutdown || !initialized || initializing || greasing || leveling || !Config.Axes[axis - 1].Enabled) return false;
            if (axis >= 4 && !HubAccepts()) return false;
            // Operational motion interlocks do not suspend hub monitoring.
            if (!armed) return false;
            double mm = TargetMm(axis, raw); if (Math.Abs(Previous[axis - 1] - mm) <= 1) return false;
            AxisConfig a = Config.Axes[axis - 1]; int rpm = Config.SpeedRpm(axis, a.Speed);
            if (!Motion.ParametersMatch(axis, rpm, a.Accel, a.Decel)) Motion.Parameters(axis, rpm, a.Accel, a.Decel, true);
            if (!Go(axis, raw, trigger, true)) return false;
            Recorder.Stage(axis, mm, a.Accel, a.Speed, a.Decel, raw); return true;
        }
        bool HubAccepts()
        {
            int[] axes = Config.Enabled(4, 7); if (axes.Length == 0) return true;
            double now = Timing.Now;
            if (now - lastHubCheck < Config.Ini.Number("control", "hub_status_poll_interval_s") / axes.Length) return faultAxes.Count == 0;
            lastHubCheck = now; int axis = axes[hubIndex++ % axes.Length]; bool was = faultAxes.Count > 0;
            try { if (Motion.ServoStatus(axis) == 3) faultAxes.Add(axis); else faultAxes.Remove(axis); }
            catch (Exception e) { faultAxes.Add(axis); Log.Write(e.Message); }
            bool fault = faultAxes.Count > 0;
            if (fault && !was)
            {
                Log.Write("Hub fault: block SimHub and center hub axes"); bool moved = false;
                foreach (int a in axes) try { CenterParameters(a); moved |= Go(a, Config.RawMax / 2, false); } catch (Exception e) { Report(e); }
                if (moved) Motion.Trigger();
            }
            return !fault;
        }
        void CenterParameters(int axis) { Motion.Parameters(axis, Config.Ini.Int("control", "center_rpm"), Config.Ini.Int("control", "center_accel_ms"), Config.Ini.Int("control", "center_decel_ms")); }
        public void Center() { foreach (int axis in Config.Enabled()) CenterParameters(axis); AxesTo(1, 7, Config.RawMax / 2); }
        void AxesTo(int first, int last, int raw)
        {
            int[] axes = Config.Enabled(first, last); if (axes.Length == 0) return;
            foreach (int axis in axes) { Go(axis, raw, first == last); Timing.Sleep(.1); }
            if (first != last) Motion.Trigger(); Timing.Sleep(1); WaitAxes(first, last, false);
        }
        void WaitAxes(int first, int last, bool homing)
        {
            double deadline = Timing.Now + Config.Ini.Number("control", homing ? "homing_wait_timeout_s" : "axis_wait_timeout_s");
            while (Timing.Now < deadline)
            {
                // Materialize every check; don't short-circuit remaining axes.
                bool[] done = Config.Enabled(first, last).Select(a => homing ? Motion.HomingComplete(a) : Motion.PositionReached(a, true)).ToArray();
                if (done.All(v => v)) return;
                Timing.Sleep(Config.Ini.Number("control", "wait_poll_interval_s"));
            }
            throw new TimeoutException(homing ? "Homing timeout" : "Position timeout");
        }
        void RetryPortOpen(BusConfig bus, Exception failure)
        {
            activity = "COM Error (" + bus.Port + ") - I try again in 10 seconds";
            error = failure.Message;
            Log.Write(activity + ": " + failure.Message);
            // Closing Com must interrupt the delay and release any ports that
            // were already opened before this port failed.
            if (Stop.Wait(TimeSpan.FromSeconds(10))) throw new OperationCanceledException();
            activity = "Initialization"; error = "";
            Log.Write("Retrying COM port " + bus.Port);
        }
        public void Initialize()
        {
            initializing = true; initialized = false;
            try
            {
                if (Stop.IsSet) throw new OperationCanceledException();
                Motion.Reset(); if (!Bus.Connected) Bus.Connect(wireFactory, RetryPortOpen);
                if (Stop.IsSet) throw new OperationCanceledException();
                // Python's connection diagnostics read these in this order.
                foreach (int axis in Config.Enabled())
                {
                    Bus.Read(axis, 0x4004); Bus.Read(axis, 0x4005); Bus.Read(axis, 0x410a); Bus.Read(axis, 0x4043); Bus.ReadI16(axis, 0x4001); Motion.Current(axis); Motion.Load(axis);
                }
                maintenancePlanners.Clear();
                foreach (int axis in Config.Enabled()) { Motion.Initialize(axis); Motion.PlannerStop(axis); }
                int[] home = Config.Enabled().Where(a => !Motion.Homed(a)).ToArray();
                if (home.Length > 0)
                {
                    foreach (int a in home) Motion.StartHoming(a, true, true, false);
                    Timing.Sleep(1); Motion.TriggerHoming(0); WaitAxes(1, 7, true);
                }
                if (Leveler.Load()) Leveler.Apply(); Timing.Sleep(.2);
                foreach (int a in Config.Enabled(4, 7)) { Motion.PlannerStart(a); Motion.Parameters(a, 50, 500, 500); }
                AxesTo(4, 7, 0); Timing.Sleep(1); WaitAxes(4, 7, false);
                foreach (int a in Config.Enabled(4, 7)) Motion.Parameters(a, 200, 300, 300);
                Timing.Sleep(.2); foreach (int a in Config.Enabled(1, 3)) Motion.PlannerStart(a);
                if (home.Length > 0) AxesTo(1, 7, Config.RawMax);
                AxesTo(1, 7, Config.RawMax / 2);
                foreach (int a in Config.Enabled()) Motion.Parameters(a, Config.SpeedRpm(a, Config.Axes[a - 1].Speed), 300, 300);
                initialized = true; Log.Write("Initialization complete");
            }
            catch { Motion.Disconnect(); throw; }
            finally { initializing = false; }
        }
        void RequireHomed(int[] axes)
        { if (axes.Any(a => !Config.Axes[a - 1].Enabled || !Motion.Homed(a))) throw new InvalidOperationException("All requested axes must be enabled and homed"); }
        public void EnsureMaintenancePlanners(int[] axes)
        {
            RequireHomed(axes);
            foreach (int a in axes) if (!maintenancePlanners.Contains(a))
            { double current = Motion.PositionMm(a); Motion.PlannerStart(a, current); maintenancePlanners.Add(a); Previous[a - 1] = current; }
        }
        void ServoGroup(int[] axes, bool enabled)
        {
            Exception error = null;
            foreach (int a in axes) try { Motion.Servo(a, enabled); } catch (Exception e) { error = error ?? e; }
            if (error != null) throw error;
        }
        void MaintenanceMove(int first, int last, int raw)
        {
            int[] axes = Enumerable.Range(first, last - first + 1).ToArray(); RequireHomed(axes);
            foreach (int a in axes)
            {
                double current = Motion.PositionMm(a);
                if (!maintenancePlanners.Contains(a)) { Motion.PlannerStart(a, current); maintenancePlanners.Add(a); }
                Motion.Parameters(a, Config.Ini.Int("control", a <= 3 ? "maintenance_middle_rpm" : "maintenance_hub_rpm"), Config.Axes[a - 1].Accel, Config.Axes[a - 1].Decel); Previous[a - 1] = current;
            }
            if (first == 4 && last == 7) { try { ServoGroup(axes, true); AxesTo(first, last, raw); } finally { ServoGroup(axes, false); } }
            else AxesTo(first, last, raw);
        }
        void Home(int first, int last)
        {
            int[] axes = Enumerable.Range(first, last - first + 1).ToArray();
            if (axes.Any(a => !Config.Axes[a - 1].Enabled)) throw new InvalidOperationException("Axes disabled");
            foreach (int a in axes) { maintenancePlanners.Remove(a); Motion.PlannerStop(a); }
            bool shared = first == 4 && last == 7;
            foreach (int a in axes) Motion.StartHoming(a, true, true, !shared);
            if (shared) { Timing.Sleep(1); Motion.TriggerHoming(0); }
            WaitAxes(first, last, true);
            foreach (int a in axes) Previous[a - 1] = Config.Ini.Number("motion_limits", "homing_mm");
        }
        void RestorePlanners()
        {
            int[] axes = Config.Enabled(); var positions = axes.ToDictionary(a => a, a => Motion.PositionMm(a));
            foreach (int a in axes) { if (maintenancePlanners.Contains(a)) Motion.Servo(a, true); else Motion.PlannerStart(a, positions[a]); Previous[a - 1] = positions[a]; }
            maintenancePlanners.Clear();
        }
        public void SetGame(bool value) { Recorder.SetRunning(value); game = value; }
        public void Dispatch(string message, IPEndPoint reply = null)
        {
            if (!initialized || initializing || analysis) return;
            foreach (string part in message.Replace("\r", "").Split(';').Select(s => s.Trim()).Where(s => s.Length != 0))
            {
                string[] tokens = part.Split((char[])null, StringSplitOptions.RemoveEmptyEntries); string cmd = tokens[0].ToUpperInvariant();
                if (shutdown || (cmd != "SHUTDOWN" && maintenance)) continue;
                switch (cmd)
                {
                    case "POSITIONS":
                        int[] values = new int[7]; bool valid = tokens.Length == 8;
                        if (valid) for (int i = 0; i < 7; i++) if (!int.TryParse(tokens[i + 1], out values[i]) || values[i] < Config.Ini.Int("simhub", "position_min") || values[i] > Config.Ini.Int("simhub", "position_max")) { valid = false; break; }
                        if (!valid) { Log.Write("Invalid POSITIONS packet"); break; }
                        if (!game) SetGame(true);
                        lock (latestGate) Array.Copy(values, latest, 7);
                        lock (MotionGate) if (!maintenance && !shutdown) armed = true; break;
                    case "START": SetGame(true); break;
                    case "END": SetGame(false); break;
                    case "SHUTDOWN":
                        if (reply != null) { lock (stateGate) shutdownReplies.Add(reply); SendShutdown("SHUTDOWN_ACCEPTED"); }
                        RequestShutdown(); break;
                    default: Log.Write("Unknown UDP command: " + cmd); break;
                }
            }
        }
        void ReceiveLoop()
        {
            double last = 0;
            while (!Stop.IsSet)
            {
                try
                {
                    IPEndPoint from = new IPEndPoint(IPAddress.Any, 0); byte[] bytes = udp.Receive(ref from);
                    // Drain and discard pre-INIT datagrams, including START and
                    // SHUTDOWN. They must not set connection/recording state or
                    // become stale motion targets once initialization finishes.
                    if (!initialized || initializing) continue;
                    last = Timing.Now; connected = true; Dispatch(System.Text.Encoding.UTF8.GetString(bytes), from);
                }
                catch (SocketException e)
                {
                    if (e.SocketErrorCode != SocketError.TimedOut) { if (!Stop.IsSet) Report(e); break; }
                    if (connected && Timing.Now - last >= Config.Ini.Number("simhub", "disconnect_timeout_s")) { connected = false; SetGame(false); }
                }
                catch (ObjectDisposedException) { break; }
                catch (Exception e) { Report(e); }
            }
            connected = false; SetGame(false);
        }
        void SenderLoop()
        {
            double next = Timing.Now, previous = 0, cycle = Config.Ini.Number("simhub", "sender_cycle_s");
            while (!Stop.IsSet)
            {
                if (!analysis && !maintenance && !shutdown && initialized && !busy)
                {
                    try
                    {
                        int[] values; lock (latestGate) values = (int[])latest.Clone();
                        lock (MotionGate)
                        {
                            bool moved = false; for (int a = 1; a <= 7; a++) moved |= HandlePosition(a, values[a - 1]);
                            double now = Timing.Now;
                            if (previous != 0) lock (stateGate) { intervals.Enqueue((now - previous) * 1000); while (intervals.Count > 6000) intervals.Dequeue(); }
                            previous = now;
                            if (moved) { Motion.Trigger(); Recorder.Trigger(); }
                        }
                    }
                    catch (Exception e) { Report(e); Stop.Wait(100); }
                }
                else previous = 0;
                next += cycle; double rest = next - Timing.Now;
                if (rest > 0) Stop.Wait(TimeSpan.FromSeconds(rest)); else next = Timing.Now;
            }
        }
        void RecorderLoop()
        {
            double interval = Config.Ini.Number("simulation", "simulation_interval_s"), previous = Timing.Now, next = previous, lastSave = previous, playtimeAt = previous;
            bool was = false;
            while (!Stop.IsSet)
            {
                double now = Timing.Now;
                try
                {
                    CheckHmiConnections();
                    Recorder.Tick(Math.Min(Math.Max(now - previous, 0), interval * 2));
                    lock (greaseGate)
                    {
                        if (game) foreach (var group in Groups) if (Config.Enabled(group[0], group[1]).Length > 0) grease[GreaseKey(group[0], group[1])].playtimeMinutes += Math.Max(0, now - playtimeAt) / 60;
                        if ((game && now - lastSave >= 60) || (was && !game)) { SaveGrease(); lastSave = now; }
                    }
                    was = game; playtimeAt = now;
                }
                catch (Exception e) { Report(e); }
                previous = now; next += interval; double rest = next - Timing.Now;
                if (rest > 0) Stop.Wait(TimeSpan.FromSeconds(rest)); else next = Timing.Now;
            }
        }
        void TelemetryLoop()
        {
            int[] axes = Config.Enabled(); int index = 0; double nextStatus = 0;
            while (!Stop.IsSet)
            {
                if (Bus.Connected && !initializing)
                {
                    int axis = axes[index++ % axes.Length];
                    try
                    {
                        if (mode == "actual_positions") Recorder.SetActual(axis, Motion.PositionMm(axis) - Config.Axes[axis - 1].Zero);
                        if (Timing.Now >= nextStatus)
                        {
                            // Match Python's motor table: do not spend bus time on
                            // load measurements while SimHub is running a game.
                            int? load = game ? (int?)null : Motion.Load(axis);
                            int fault = Motion.Error(axis), servo = Motion.ServoStatus(axis); bool homed = Motion.Homed(axis);
                            lock (stateGate) { var t = telemetry[axis - 1]; if (load.HasValue) t.Load = load.Value / Config.Ini.Number("leveling", "load_rate_units_per_percent"); t.Error = fault; t.Servo = servo; t.Homed = homed; }
                            nextStatus = Timing.Now + Config.Ini.Number("ui", "axis_status_refresh_ms") / 1000 / axes.Length;
                        }
                    }
                    catch (Exception e) { Log.Write("Telemetry axis " + axis + ": " + e.Message); }
                }
                Stop.Wait(TimeSpan.FromSeconds(Config.Ini.Number("control", "actual_position_poll_interval_s")));
            }
        }
        public Status Snapshot()
        {
            AxisStatus[] states;
            lock (stateGate) states = telemetry.Select(t => new AxisStatus { Axis = t.Axis, Enabled = Config.Axes[t.Axis - 1].Enabled, Offset = Offsets[t.Axis - 1], Load = t.Load, Error = t.Error, Servo = t.Servo, Homed = t.Homed }).ToArray();
            double[] actual = Recorder.ActualSnapshot();
            lock (Recorder.Gate) for (int i = 0; i < 7; i++) { states[i].Target = Recorder.Axes[i].Target - Config.Axes[i].Zero; states[i].Calculated = Recorder.Axes[i].Position - Config.Axes[i].Zero; states[i].Velocity = Recorder.Axes[i].Velocity; states[i].Actual = actual[i]; }
            Dictionary<string, GreaseEntry> g; lock (greaseGate) g = grease.ToDictionary(p => p.Key, p => new GreaseEntry { lastGreaseAt = p.Value.lastGreaseAt, playtimeMinutes = p.Value.playtimeMinutes });
            double[] timing; lock (stateGate) timing = intervals.ToArray();
            int[] positions; lock (latestGate) positions = (int[])latest.Clone();
            double[] center, loads; lock (Recorder.Gate) { center = (double[])Recorder.Center.Clone(); loads = Recorder.CalibrationLoads == null ? null : (double[])Recorder.CalibrationLoads.Clone(); }
            string[] messages = Log.Recent();
            return new Status { ProcessId = System.Diagnostics.Process.GetCurrentProcess().Id, Simulated = simulated, Connected = connected, GameRunning = game, Initialized = initialized, Maintenance = maintenance, Analysis = analysis, Busy = busy || shutdown, Activity = activity, LastError = error, Messages = messages, Mode = mode, Leveling = leveling, FixedAxis = Leveler.FixedAxis, Greasing = greasing, GreaseFirst = greaseFirst, GreaseLast = greaseLast, Playback = Playback.State, PlaybackFile = Playback.FileName, PlaybackSeconds = Playback.Seconds, Axes = states, Grease = g, TriggerIntervals = timing, LatestPositions = positions, LoadCenter = center, CalibrationLoads = loads };
        }
        public void ArmPlayback() { armed = true; SetGame(false); }
        static readonly int[][] Groups = { new[] { 1, 1 }, new[] { 2, 2 }, new[] { 3, 3 }, new[] { 4, 7 } };
        public static string GreaseKey(int first, int last) { return first == last ? first.ToString() : first + "-" + last; }
        Dictionary<string, GreaseEntry> LoadGrease()
        {
            Dictionary<string, GreaseEntry> data;
            try { data = Json.Read<Dictionary<string, GreaseEntry>>(Config.PathFor("INI/grease_data.json")) ?? new Dictionary<string, GreaseEntry>(); }
            catch (Exception e) { Log.Write("Grease data: " + e.Message); data = new Dictionary<string, GreaseEntry>(); }
            foreach (int[] g in Groups) { string key = GreaseKey(g[0], g[1]); if (!data.ContainsKey(key) || data[key] == null) data[key] = new GreaseEntry { lastGreaseAt = "" }; }
            return data;
        }
        void SaveGrease() { Json.Save(Config.PathFor("INI/grease_data.json"), grease); }
        void Grease(int first, int last)
        {
            int[] axes = Config.Enabled(first, last); greasing = true;
            try
            {
                if (axes.Length != last - first + 1) throw new InvalidOperationException("Axes disabled");
                EnsureMaintenancePlanners(axes); foreach (int a in axes) Motion.Servo(a, true);
                foreach (int a in axes) Motion.Parameters(a, Config.Ini.Int("grease", "rpm"), Config.Ini.Int("grease", "accel_ms"), Config.Ini.Int("grease", "decel_ms"));
                double negative = Config.Ini.Number("grease", "negative_mm"), positive = Config.Ini.Number("grease", "positive_mm"), target = negative;
                while (!OperationStop.IsSet) { GreaseMove(axes, target, false); target = target == negative ? positive : negative; }
                GreaseMove(axes, Config.Ini.Number("grease", "center_mm"), true);
            }
            finally { try { if (first == 4 && last == 7) ServoGroup(axes, false); } finally { greasing = false; } }
        }
        void GreaseMove(int[] axes, double target, bool waitOnStop)
        {
            foreach (int a in axes) { double mm = Config.Axes[a - 1].Zero + target; Previous[a - 1] = mm; Motion.Position(a, mm); }
            double end = Timing.Now + Config.Ini.Number("grease", "move_timeout_s");
            while (Timing.Now < end)
            {
                if (OperationStop.IsSet && !waitOnStop) return;
                if (axes.Select(a => Motion.PositionReached(a, true)).ToArray().All(v => v)) return;
                Timing.Sleep(Config.Ini.Number("grease", "position_poll_interval_s"));
            }
            throw new TimeoutException("Grease movement timed out");
        }
        void CaptureLoads()
        {
            var readings = Enumerable.Range(0, 4).Select(_ => new List<double>()).ToArray();
            for (int sample = 0; sample < 11; sample++)
            {
                if (game) throw new InvalidOperationException("SimHub started during measurement");
                for (int i = 0; i < 4; i++) readings[i].Add(Motion.Load(i + 4));
                if (sample < 10) Timing.Sleep(.06);
            }
            if (game) throw new InvalidOperationException("SimHub started during measurement");
            Recorder.SaveLoads(readings.Select(v => v.OrderBy(n => n).ElementAt(5)).ToArray());
        }
        void SendShutdown(string text)
        {
            byte[] data = System.Text.Encoding.UTF8.GetBytes(text);
            lock (stateGate) using (var reply = new UdpClient()) foreach (var endpoint in shutdownReplies) try { reply.Send(data, data.Length, endpoint); } catch (SocketException e) { Log.Write(e.Message); }
        }
        public void Dispose()
        {
            RequestShutdown(); activity = "Shutdown";
            Log.Write("Shutdown requested; stopping active operations");
            Exception failure = null;
            try
            {
                Playback.Stop();
                if (job != null && !job.Wait(TimeSpan.FromSeconds(Config.Ini.Number("control", "homing_wait_timeout_s") + 10))) throw new TimeoutException("Operation did not stop before shutdown");
                if (udp != null) udp.Close();
                if (!Task.WaitAll(loops.ToArray(), TimeSpan.FromSeconds(15))) throw new TimeoutException("Background workers did not stop");
                Recorder.SetRunning(false); Recorder.Dispose(); lock (greaseGate) SaveGrease();
                lock (MotionGate)
                {
                    try
                    {
                        if (initialized)
                        {
                            activity = "Shutdown: centering"; Log.Write("Shutdown: centering all axes");
                            Center(); Timing.Sleep(2);
                            foreach (int a in Config.Enabled()) Motion.PlannerStop(a);
                            activity = "Shutdown: lowering"; Log.Write("Shutdown: lowering hub axes");
                            int[] hubs = Config.Enabled(4, 7);
                            foreach (int a in hubs) Motion.StartHoming(a, true, false, false);
                            Timing.Sleep(1); if (hubs.Length > 0) Motion.TriggerHoming(0); WaitAxes(4, 7, true);
                            Log.Write("Shutdown: hub axes lowered");
                        }
                    }
                    finally { initialized = false; activity = "Shutdown: closing ports"; Motion.Disconnect(); Log.Write("Shutdown: all Modbus ports closed"); }
                }
            }
            catch (Exception e) { failure = e; Report(e); try { Motion.Disconnect(); } catch (Exception close) { Report(close); } }
            finally
            {
                Recorder.Dispose(); if (udp != null) udp.Close(); SendShutdown(failure == null ? "SHUTDOWN_COMPLETE" : "SHUTDOWN_ERROR " + failure.Message);
                shutdownFinished.TrySetResult(new Reply { Ok = failure == null, Error = failure == null ? null : failure.Message });
                Log.Write(failure == null ? "SHUTDOWN_COMPLETE" : "SHUTDOWN_ERROR " + failure.Message);
            }
            if (failure != null) throw failure;
        }
    }
}
