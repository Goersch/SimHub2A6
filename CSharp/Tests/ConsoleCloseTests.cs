using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct StartupInfo
        {
            public int Size;
            public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public short ShowWindow, Reserved2;
            public IntPtr Reserved2Pointer, StdInput, StdOutput, StdError;
        }
        [StructLayout(LayoutKind.Sequential)]
        struct ProcessInfo { public IntPtr Process, Thread; public uint ProcessId, ThreadId; }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool CreateProcess(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo information);
        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool CloseHandle(IntPtr handle);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr FindWindow(string className, string title);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string className, string title);
        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

        static string ReadWorkerLog(string isolated)
        {
            string path = Path.Combine(isolated, "LOG", "simhub2simrig-com.log");
            if (!File.Exists(path)) return "";
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var reader = new StreamReader(stream)) return reader.ReadToEnd();
        }
        static int WorkerPid(string isolated)
        {
            var match = System.Text.RegularExpressions.Regex.Match(ReadWorkerLog(isolated), @"Communication worker PID: (\d+)");
            return match.Success ? int.Parse(match.Groups[1].Value) : 0;
        }
        static Process WaitForOwnedWorker(Process launcher, string isolated)
        {
            var client = new ComClient(); int pid = 0;
            Until(() => {
                if (launcher.HasExited) throw new Exception("Com launcher exited: " + ReadWorkerLog(isolated));
                pid = WorkerPid(isolated); if (pid == 0) return false;
                try
                {
                    Status status = client.Send(new Request { Operation = "status" }).Status;
                    if (status.ProcessId != pid || !status.Simulated) throw new Exception("Tests refuse to control an unrelated or non-simulated worker");
                    return status.Initialized && !status.Busy;
                }
                catch (TimeoutException) { return false; }
            }, "Owned simulated worker did not initialize", 40);
            var process = Process.GetProcessById(pid);
            Check(process.Handle != IntPtr.Zero, "Retain worker handle for its final exit code");
            Check(process.StartTime >= launcher.StartTime, "Worker was created by this test launcher");
            Check(process.Id != launcher.Id, "Drive owner runs independently of the console");
            return process;
        }
        static void AssertLoweredAndReleased(Process worker, string isolated)
        {
            Check(worker.WaitForExit(25000), "Background worker completes shutdown after console loss");
            Equal(0, worker.ExitCode, "Background worker exited cleanly");
            string log = ReadWorkerLog(isolated);
            int requested = log.IndexOf("Console closed; requesting orderly shutdown", StringComparison.Ordinal),
                lowered = log.IndexOf("Shutdown: hub axes lowered", StringComparison.Ordinal),
                released = log.IndexOf("Shutdown: all Modbus ports closed", StringComparison.Ordinal),
                complete = log.IndexOf("SHUTDOWN_COMPLETE", StringComparison.Ordinal);
            Check(requested >= 0, "Lost console requests shutdown");
            Check(lowered > requested, "Lowering completes after the request");
            Check(released > lowered, "Modbus ports close after lowering");
            Check(complete > released, "Completion is logged after port release");
            int port = new Configuration(isolated).Ini.Int("simhub", "udp_port");
            using (var probe = new UdpClient())
            { probe.Client.ExclusiveAddressUse = true; probe.Client.Bind(new IPEndPoint(IPAddress.Loopback, port)); checks++; }
        }
        static void CleanupSimulatedProcesses(Process launcher, Process worker)
        {
            if (!launcher.HasExited) { launcher.Kill(); launcher.WaitForExit(5000); }
            if (worker != null && !worker.HasExited)
            {
                if (!worker.WaitForExit(25000)) { worker.Kill(); worker.WaitForExit(5000); }
            }
        }
        static void ConsoleProcessExit()
        {
            string isolated = Prepare(root), exe = Path.Combine(binaryDirectory, "SimHub2SimRigCom.exe");
            using (var launcher = Process.Start(new ProcessStartInfo(exe, "--simulate --root " + ConsoleLauncher.Quote(isolated)) { UseShellExecute = false, CreateNoWindow = true }))
            {
                Process worker = null;
                try
                {
                    worker = WaitForOwnedWorker(launcher, isolated);
                    launcher.Kill(); Check(launcher.WaitForExit(5000), "Console process is gone");
                    AssertLoweredAndReleased(worker, isolated);
                }
                finally { CleanupSimulatedProcesses(launcher, worker); if (worker != null) worker.Dispose(); }
            }
        }
        static void ConsoleWindowClose()
        {
            string isolated = Prepare(root), exe = Path.Combine(binaryDirectory, "SimHub2SimRigCom.exe");
            var startup = new StartupInfo { Size = Marshal.SizeOf(typeof(StartupInfo)), Flags = 1, ShowWindow = 0 };
            ProcessInfo information;
            if (!CreateProcess(exe, new StringBuilder(ConsoleLauncher.Quote(exe) + " --simulate --root " + ConsoleLauncher.Quote(isolated)), IntPtr.Zero, IntPtr.Zero, false, 0x10, IntPtr.Zero, root, ref startup, out information))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            CloseHandle(information.Thread); CloseHandle(information.Process);
            using (var launcher = Process.GetProcessById((int)information.ProcessId))
            {
                Process worker = null;
                try
                {
                    worker = WaitForOwnedWorker(launcher, isolated);
                    string title = ConsoleLauncher.WindowTitle(launcher.Id); IntPtr window = IntPtr.Zero;
                    Until(() => {
                        window = FindWindow("ConsoleWindowClass", title);
                        if (window == IntPtr.Zero) window = FindWindowEx(new IntPtr(-3), IntPtr.Zero, "ConsoleWindowClass", title);
                        return window != IntPtr.Zero;
                    }, "Hidden test console window not found", 5);
                    var elapsed = Stopwatch.StartNew();
                    Check(PostMessage(window, 0x10, IntPtr.Zero, IntPtr.Zero), "Send a real WM_CLOSE to the console window");
                    Check(launcher.WaitForExit(10000), "Windows closes the console launcher");
                    AssertLoweredAndReleased(worker, isolated);
                    Check(elapsed.Elapsed.TotalSeconds > 5, "Worker cleanup survives beyond the console close time budget");
                }
                finally { CleanupSimulatedProcesses(launcher, worker); if (worker != null) worker.Dispose(); }
            }
        }
    }
}
