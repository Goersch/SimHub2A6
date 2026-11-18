using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static void SenderModes()
        {
            string isolated = Prepare(root), path = Path.Combine(isolated, "INI", "SimHub2SimRigCom.ini");
            // A long polling period makes an accidental timeout-driven send
            // distinguishable from a real event wake-up.
            File.WriteAllText(path, File.ReadAllText(path).Replace("sender_cycle_s = 0.01", "sender_cycle_s = 0.5"));
            var c = new Configuration(isolated);
            var initializedField = typeof(Engine).GetField("initialized", BindingFlags.Instance | BindingFlags.NonPublic);
            var busyField = typeof(Engine).GetField("busy", BindingFlags.Instance | BindingFlags.NonPublic);
            int targets = 0, triggers = 0, hubReads = 0;
            Func<BusConfig, IWire> factory = b => {
                var wire = new VirtualWire(b.Axes);
                wire.Override = frame => {
                    int address = frame[2] << 8 | frame[3];
                    if (frame[1] == 16 && address == 0x1106) Interlocked.Increment(ref targets);
                    if (frame[1] == 6 && address == 0x0415) Interlocked.Increment(ref triggers);
                    if (frame[1] == 3 && address == 0x410a && frame[0] >= 4) Interlocked.Increment(ref hubReads);
                    return null;
                };
                return wire;
            };
            var engine = new Engine(c, true, factory);
            string pipeName = "SimHub2SimRig.SenderTest." + Guid.NewGuid().ToString("N");
            try
            {
                Equal("oversampling", engine.Snapshot().SenderMode, "Oversampling remains the default");
                Throws<InvalidOperationException>(() => engine.Handle(new Request { Operation = "sender.mode", SenderMode = "simhub_cycle" }), "Sender choice requires INIT");
                // Exercise production loops and IPC without slow homing; all
                // bus traffic goes exclusively to virtual drives.
                initializedField.SetValue(engine, true); engine.Bus.Connect(factory);
                engine.Handle(new Request { Operation = "sender.mode", SenderMode = "simhub_cycle" });
                engine.Start();
                using (var server = new PipeServer(engine, pipeName))
                using (var udp = new UdpClient())
                {
                    server.Start(); var client = new ComClient(pipeName);
                    Equal("simhub_cycle", client.Send(new Request { Operation = "status" }).Status.SenderMode, "IPC confirms the selected sender mode");
                    Check(!client.Send(new Request { Operation = "sender.mode", SenderMode = "invalid" }).Ok, "IPC rejects unknown sender modes");
                    Thread.Sleep(80);
                    Equal(0, engine.Snapshot().TriggerIntervals.Length, "No event-mode samples before positions arrive");
                    Equal(0, Volatile.Read(ref targets), "Selecting event mode does not replay initial positions");
                    var endpoint = new IPEndPoint(IPAddress.Loopback, c.Ini.Int("simhub", "udp_port"));
                    Action<int> send = raw => {
                        byte[] data = System.Text.Encoding.ASCII.GetBytes("POSITIONS " + string.Join(" ", Enumerable.Repeat(raw.ToString(), 7)));
                        udp.Send(data, data.Length, endpoint);
                    };
                    send(90000);
                    Until(() => engine.Snapshot().Axes.All(a => Math.Abs(a.Target - (engine.TargetMm(a.Axis, 90000) - c.Axes[a.Axis - 1].Zero)) < .001), "UDP event sends all seven targets before the 500-ms polling deadline", .3);
                    Until(() => Volatile.Read(ref triggers) > 0, "Event cycle triggers queued targets", .3);
                    Thread.Sleep(80); send(90000);
                    Until(() => engine.Snapshot().TriggerIntervals.Length == 1, "Identical valid positions still produce a measured cycle", .3);
                    int targetCount = Volatile.Read(ref targets), triggerCount = Volatile.Read(ref triggers);
                    int received = engine.Snapshot().SimHubIntervals.Length;
                    Check(received > 0, "UDP receive timing remains active in event mode");
                    byte[] invalid = System.Text.Encoding.ASCII.GetBytes("START;POSITIONS -1 1 1 1 1 1 1;POSITIONS 1 2;END");
                    udp.Send(invalid, invalid.Length, endpoint);
                    int reads = Volatile.Read(ref hubReads);
                    Thread.Sleep(650);
                    Equal(1, engine.Snapshot().TriggerIntervals.Length, "Idle time, invalid positions and control commands do not create sender cycles");
                    Equal(targetCount, Volatile.Read(ref targets), "Stationary targets are not rewritten");
                    Equal(triggerCount, Volatile.Read(ref triggers), "Stationary data does not retrigger axes");
                    Check(Volatile.Read(ref hubReads) > reads, "Hub monitoring continues without position events");

                    busyField.SetValue(engine, true);
                    engine.Dispatch("POSITIONS 80000 80000 80000 80000 80000 80000 80000");
                    engine.Dispatch("POSITIONS 100000 100000 100000 100000 100000 100000 100000");
                    Thread.Sleep(80);
                    Equal(targetCount, Volatile.Read(ref targets), "Busy interlock blocks event motion");
                    Check(!client.Send(new Request { Operation = "sender.mode", SenderMode = "oversampling" }).Ok, "Sender mode is locked while busy");
                    busyField.SetValue(engine, false);
                    Until(() => engine.Snapshot().Axes.All(a => Math.Abs(a.Target - (engine.TargetMm(a.Axis, 100000) - c.Axes[a.Axis - 1].Zero)) < .001), "Pending wake survives a busy interlock and sends the newest complete sample", 1);
                    Equal(targetCount + c.Enabled().Length, Volatile.Read(ref targets), "Coalesced updates write one target per enabled axis");
                    Equal(1, engine.Snapshot().TriggerIntervals.Length, "First cycle after a busy operation restarts timing");
                    Check(client.Send(new Request { Operation = "sender.mode", SenderMode = "oversampling" }).Ok, "Switch to oversampling through IPC");
                    int samples = engine.Snapshot().TriggerIntervals.Length;
                    Until(() => engine.Snapshot().TriggerIntervals.Length >= samples + 2, "Oversampling continues cycles without input", 2);
                    Check(client.Send(new Request { Operation = "sender.mode", SenderMode = "simhub_cycle" }).Ok, "Switch back to event mode through IPC");
                    Thread.Sleep(600); samples = engine.Snapshot().TriggerIntervals.Length;
                    Thread.Sleep(600); Equal(samples, engine.Snapshot().TriggerIntervals.Length, "Switching modes creates no synthetic event cycles");
                    send(70000);
                    Until(() => engine.Snapshot().Axes[0].Target < 10, "Position events resume after a live mode change", .3);
                    Equal(samples, engine.Snapshot().TriggerIntervals.Length, "First cycle after a mode change starts a new timing interval");
                    engine.Handle(new Request { Operation = "hmi.detach" });
                    send(110000);
                    Until(() => engine.Snapshot().TriggerIntervals.Length == samples + 1, "Event sender and timing continue without HMI", .3);
                    Check(string.IsNullOrEmpty(engine.Snapshot().LastError), "Event sender reports no error");
                }
                engine.RequestShutdown();
                var sender = ((System.Collections.Generic.List<System.Threading.Tasks.Task>)typeof(Engine).GetField("loops", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(engine))[1];
                Check(sender.Wait(200), "Shutdown interrupts the sender event wait immediately");
                initializedField.SetValue(engine, false);
                engine.Dispose();
                engine = null;
            }
            finally
            {
                if (engine != null) { busyField.SetValue(engine, false); initializedField.SetValue(engine, false); engine.Dispose(); }
            }
        }
    }
}
