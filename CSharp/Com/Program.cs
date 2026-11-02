using System;
using System.IO;
using System.IO.Pipes;
using System.Collections.Generic;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public sealed class PipeServer : IDisposable
    {
        readonly Engine engine;
        readonly string name;
        readonly object commandGate = new object();
        readonly object listenerGate = new object();
        readonly object clientsGate = new object();
        readonly List<Task> clients = new List<Task>();
        readonly ManualResetEventSlim stop = new ManualResetEventSlim();
        NamedPipeServerStream listener;
        Task task;
        public PipeServer(Engine engine, string name = null) { this.engine = engine; this.name = name ?? Protocol.PipeName; }
        public void Start() { task = Task.Factory.StartNew(Listen, TaskCreationOptions.LongRunning); }
        void Listen()
        {
            while (!stop.IsSet)
            {
                var security = new PipeSecurity();
                security.SetAccessRuleProtection(true, false);
                security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User, PipeAccessRights.FullControl, AccessControlType.Allow));
                NamedPipeServerStream pipe;
                try
                {
                    lock (listenerGate)
                    {
                        if (stop.IsSet) return;
                        pipe = listener = new NamedPipeServerStream(name, PipeDirection.InOut, 8, PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536, security);
                    }
                }
                catch (IOException) { stop.Wait(50); continue; }
                try { pipe.WaitForConnection(); }
                catch (ObjectDisposedException) { return; }
                catch (IOException) { pipe.Dispose(); if (stop.IsSet) return; continue; }
                lock (listenerGate) listener = null;
                if (stop.IsSet) { pipe.Dispose(); return; }
                lock (clientsGate)
                {
                    clients.RemoveAll(t => t.IsCompleted);
                    clients.Add(Task.Factory.StartNew(() => {
                        try { Serve(pipe); } catch (IOException) { } catch (ObjectDisposedException) { }
                    }));
                }
            }
        }
        void Serve(NamedPipeServerStream pipe)
        {
            using (pipe)
            using (var reader = new StreamReader(pipe, Encoding.UTF8, false, 4096, true))
            using (var writer = new StreamWriter(pipe, new UTF8Encoding(false), 4096, true) { AutoFlush = true })
            {
                using (var timeout = new Timer(_ => pipe.Dispose(), null, 5000, Timeout.Infinite))
                try
                {
                    Reply reply;
                    try
                    {
                        string line = Protocol.ReadLine(reader); if (line == null) return;
                        timeout.Change(Timeout.Infinite, Timeout.Infinite);
                        Request request = Json.Decode<Request>(line);
                        if (request != null && (request.Operation == "status" || request.Operation == "files" || request.Operation == "preview")) reply = engine.Handle(request);
                        else lock (commandGate) reply = engine.Handle(request);
                    }
                    catch (Exception e) { reply = new Reply { Ok = false, Error = e.Message }; }
                    writer.WriteLine(Json.Encode(reply));
                }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
            }
        }
        public void Dispose()
        {
            stop.Set(); lock (listenerGate) if (listener != null) listener.Dispose();
            if (task != null && !task.Wait(3000)) throw new TimeoutException("IPC listener did not stop");
            Task[] active; lock (clientsGate) active = clients.ToArray();
            if (!Task.WaitAll(active, 6000)) throw new TimeoutException("IPC clients did not stop");
        }
    }
    static class Program
    {
        static int Main(string[] args)
        {
            try
            {
                string root = Configuration.FindRoot(args); Log.DirectoryPath = Path.Combine(root, "LOG");
                bool simulate = Array.IndexOf(args, "--simulate") >= 0;
                if (Array.IndexOf(args, "--shutdown") >= 0)
                { Reply reply = new ComClient().Send(new Request { Operation = "shutdown.wait" }, 185000); if (!reply.Ok) throw new Exception(reply.Error); return 0; }
                if (Array.IndexOf(args, "--worker") < 0)
                    return ConsoleLauncher.Run(typeof(Program).Assembly.Location, root, simulate);
                Log.ConsoleEnabled = false;
                int lifetimeAt = Array.IndexOf(args, "--lifetime-pipe");
                if (lifetimeAt < 0 || lifetimeAt + 1 >= args.Length)
                    throw new ArgumentException("The background worker requires its console lifetime pipe");
                bool created;
                using (var single = new Mutex(true, "Local\\" + Protocol.PipeName, out created))
                {
                    if (!created) throw new InvalidOperationException("SimHub2SimRigCom is already running");
                    try
                    {
                        Log.Write("Communication worker PID: " + System.Diagnostics.Process.GetCurrentProcess().Id);
                        var engine = new Engine(new Configuration(root), simulate);
                        var ipc = new PipeServer(engine);
                        using (var lifetime = new WorkerLifetime(args[lifetimeAt + 1], () => {
                            Log.Write("Console closed; requesting orderly shutdown"); engine.RequestShutdown();
                        }))
                        {
                            try
                            {
                                Log.Write(simulate ? "SIMULATION: no real serial ports" : "Starting hardware communication");
                                if (!engine.Stop.IsSet) engine.Start();
                                ipc.Start(); engine.Stop.Wait();
                            }
                            finally { try { engine.Dispose(); } finally { ipc.Dispose(); } }
                        }
                    }
                    finally { single.ReleaseMutex(); }
                }
                return 0;
            }
            catch (Exception e) { Log.Write(e.ToString()); return 1; }
        }
    }
}
