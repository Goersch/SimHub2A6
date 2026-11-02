using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;

namespace SimHub2SimRig.Com
{
    // The console is only a launcher. The drive owner has no console and can
    // finish homing even after Windows terminates the closing console host.
    public static class ConsoleLauncher
    {
        public static string WindowTitle(int processId) { return "SimHub2SimRigCom - PID " + processId; }

        public static string Quote(string value)
        {
            var result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
                result.Append(c); slashes = 0;
            }
            result.Append('\\', slashes * 2); result.Append('"'); return result.ToString();
        }

        public static int Run(string executable, string root, bool simulate)
        {
            try { Console.Title = WindowTitle(Process.GetCurrentProcess().Id); } catch (IOException) { }
            string logPath = Path.Combine(root, "LOG", "simhub2simrig-com.log");
            long logOffset = File.Exists(logPath) ? new FileInfo(logPath).Length : 0;
            using (var lifetime = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable))
            using (var worker = new Process())
            {
                Action requestStop = () => { try { lifetime.Dispose(); } catch (IOException) { } };
                worker.StartInfo = new ProcessStartInfo(executable,
                    "--worker --lifetime-pipe " + Quote(lifetime.GetClientHandleAsString()) +
                    " --root " + Quote(root) + (simulate ? " --simulate" : ""))
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                worker.Start();
                lifetime.DisposeLocalCopyOfClientHandle();
                // Useful when diagnosing ownership of a port after the visible
                // window has closed; the HMI's ProcessId is this worker PID.
                SafeConsole.WriteLine("Communication process started (PID " + worker.Id + ").");
                SafeConsole.WriteLine("Closing this window requests lowering and a clean shutdown.");
                using (var controls = new ConsoleControl(() => requestStop(), () => {
                    try { worker.WaitForExit(2000); } catch (InvalidOperationException) { }
                }))
                {
                    try
                    {
                        using (var tail = new ConsoleLogTail(logPath, logOffset))
                        {
                            while (!worker.WaitForExit(200)) tail.Pump();
                            tail.Pump();
                        }
                        return worker.ExitCode;
                    }
                    finally { requestStop(); }
                }
            }
        }
    }

    public static class SafeConsole
    {
        public static void WriteLine(string message)
        {
            try { Console.WriteLine(message); }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
        }
    }

    sealed class ConsoleLogTail : IDisposable
    {
        readonly string path;
        readonly long initialOffset;
        StreamReader reader;
        public ConsoleLogTail(string path, long initialOffset) { this.path = path; this.initialOffset = initialOffset; }
        public void Pump()
        {
            try
            {
                if (reader == null)
                {
                    if (!File.Exists(path)) return;
                    var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                    file.Seek(Math.Min(initialOffset, file.Length), SeekOrigin.Begin);
                    reader = new StreamReader(file, Encoding.UTF8);
                }
                string line;
                while ((line = reader.ReadLine()) != null) SafeConsole.WriteLine(line);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        public void Dispose() { if (reader != null) reader.Dispose(); }
    }

    sealed class ConsoleControl : IDisposable
    {
        delegate bool Handler(uint signal);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool SetConsoleCtrlHandler(Handler handler, [MarshalAs(UnmanagedType.Bool)] bool add);
        readonly Handler handler;
        readonly bool registered;
        public ConsoleControl(Action requestStop, Action waitBriefly)
        {
            handler = signal => {
                if (signal != 0 && signal != 1 && signal != 2 && signal != 5 && signal != 6) return false;
                // Never execute drive operations on Windows' control thread.
                // The pipe also closes automatically if no handler is invoked.
                requestStop();
                if (signal == 2 || signal == 5 || signal == 6) waitBriefly();
                return true;
            };
            registered = SetConsoleCtrlHandler(handler, true);
        }
        public void Dispose()
        {
            if (registered) SetConsoleCtrlHandler(handler, false);
            GC.KeepAlive(handler);
        }
    }

    public sealed class WorkerLifetime : IDisposable
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool PeekNamedPipe(SafePipeHandle handle, IntPtr buffer, uint size, IntPtr read, out uint available, IntPtr remaining);
        readonly AnonymousPipeClientStream pipe;
        readonly Task watcher;
        readonly ManualResetEventSlim stop = new ManualResetEventSlim();
        public WorkerLifetime(string handle, Action parentClosed)
        {
            pipe = new AnonymousPipeClientStream(PipeDirection.In, handle);
            watcher = Task.Factory.StartNew(() => {
                // Never block in ReadFile: normal IPC shutdown must also be
                // able to join this monitor while the console is still open.
                while (!stop.Wait(100))
                {
                    uint available;
                    if (PeekNamedPipe(pipe.SafePipeHandle, IntPtr.Zero, 0, IntPtr.Zero, out available, IntPtr.Zero)) continue;
                    if (!stop.IsSet) parentClosed();
                    return;
                }
            }, TaskCreationOptions.LongRunning);
        }
        public void Dispose()
        {
            stop.Set();
            if (!watcher.Wait(3000)) throw new TimeoutException("Console lifetime monitor did not stop");
            pipe.Dispose(); stop.Dispose();
        }
    }
}
