using System;
using System.Collections.Generic;
using System.Linq;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Com
{
    public sealed class MotionController
    {
        public readonly Modbus Bus;
        readonly Configuration config;
        readonly Action<double> sleep;
        readonly Dictionary<int, int[]> parameters = new Dictionary<int, int[]>();
        readonly Dictionary<int, int> positions = new Dictionary<int, int>();
        readonly object parameterGate = new object(), positionGate = new object();
        public MotionController(Configuration configuration, Modbus bus, Action<double> delay = null)
        { config = configuration; Bus = bus; sleep = delay ?? Timing.Sleep; }
        public void Reset() { lock (parameterGate) parameters.Clear(); lock (positionGate) positions.Clear(); }
        public void Initialize(int axis)
        {
            Bus.Write(axis, 0x0411, 0);
            bool save = Bus.Read(axis, 0x0401)[0] != 1;
            sleep(.2);
            Bus.Write(axis, 0x0022, 1);
            Bus.U32(axis, 0x0302, 131072); Bus.U32(axis, 0x0304, 131072);
            Bus.Write(axis, 0x0401, 1); Bus.Write(axis, 0x0405, 1); Bus.Write(axis, 0x0408, 0);
            Bus.Write(axis, 0x0412, 1); Bus.Write(axis, 0x0414, 19); Bus.Write(axis, 0x0418, 25); Bus.Write(axis, 0x041c, 0);
            Bus.U32(axis, 0x050a, 10); Bus.Write(axis, 0x050c, 10);
            Bus.U32(axis, 0x0e00, config.Units(axis, config.Ini.Number("motion_limits", "maximum_mm")));
            Bus.U32(axis, 0x0e03, config.Units(axis, config.Ini.Number("motion_limits", "minimum_mm")));
            Bus.Write(axis, 0x1000, 2); Bus.Write(axis, 0x1001, 17);
            Bus.U32(axis, 0x1004, 100); Bus.U32(axis, 0x1006, 100);
            Bus.U32(axis, 0x100b, config.Units(axis, config.Ini.Number("motion_limits", "homing_mm")));
            if (save) { sleep(.2); Bus.Write(axis, 0x0a05, 1); sleep(2); Log.Write("Axis " + axis + ": restart required; check ALF1.0 and press SET"); }
            else Bus.Write(axis, 0x0a05, 0);
        }
        public bool Homed(int axis)
        {
            int function = Bus.Read(axis, 0x0438)[0]; int logic = Bus.Read(axis, 0x0439)[0];
            if (function != 9) Log.Write("Axis " + axis + ": DO5 function != 9");
            return ((Bus.Read(axis, 0x4005)[0] >> 4) & 1) == (logic & 1);
        }
        public void StartHoming(int axis, bool ignore = false, bool initialize = true, bool trigger = true)
        {
            if (!ignore && Homed(axis)) return;
            if (initialize)
            {
                Bus.Write(axis, 0x1000, 2);
                Bus.Write(axis, 0x1002, config.PitchRpm(axis, config.Ini.Int("a6", "homing_initial_rpm")));
                Bus.Write(axis, 0x1003, config.PitchRpm(axis, config.Ini.Int("a6", "homing_end_rpm")));
                Bus.U32(axis, 0x1004, config.Ini.Int("a6", "homing_accel_ms")); Bus.U32(axis, 0x1006, config.Ini.Int("a6", "homing_decel_ms"));
                Bus.U32(axis, 0x100a, 0); Bus.U32(axis, 0x100b, config.Units(axis, config.Ini.Number("motion_limits", "homing_mm")));
            }
            Servo(axis, true); if (trigger) TriggerHoming(axis);
        }
        public void TriggerHoming(int axis) { sleep(.25); Bus.Write(axis, 0x0419, 1, axis != 0); sleep(.25); Bus.Write(axis, 0x0419, 0, axis != 0); }
        public bool HomingComplete(int axis)
        {
            int speed = Bus.Read(axis, 0x4001)[0], status = ServoStatus(axis);
            bool done = Homed(axis) && speed == 0 && (status == 1 || status == 2);
            if (done) Servo(axis, false); return done;
        }
        public bool PositionReached(int axis, bool ignore = false)
        {
            int status = ServoStatus(axis), speed = Bus.Read(axis, 0x4001)[0];
            return (status != 2 || ignore) && (speed >= 65534 || speed <= 1);
        }
        public int ServoStatus(int axis) { return Bus.Read(axis, 0x410a)[0]; }
        public int Error(int axis) { return Bus.Read(axis, 0x4043)[0]; }
        public int Load(int axis) { return Bus.ReadI16(axis, 0x4007); }
        public int Current(int axis) { return Bus.ReadI16(axis, 0x4002); }
        public int MotorCurrent(int axis) { return Bus.ReadI16(axis, 0x4003); }
        public double PositionMm(int axis) { return Bus.ReadU32(axis, 0x4016, true) / config.UnitsPerMm(axis); }
        public void Servo(int axis, bool enabled) { Bus.Write(axis, 0x0411, enabled ? 1 : 0); }
        public bool DigitalInput(int axis, int levels, int number)
        {
            if (number < 1 || number > 8) throw new ArgumentOutOfRangeException("number");
            int level = (levels >> (number - 1)) & 1;
            if (config.Ini.Bool("a6", "invert_digital_inputs")) level ^= 1;
            int logic = Bus.Read(axis, 0x0401 + (number - 1) * 4)[0] & 1;
            return level == (logic == 0 ? 1 : 0);
        }
        void Configure(int axis, int mode, int position, int rpm, int accel, int decel)
        {
            int selected;
            try { selected = Bus.Read(axis, 0x0300)[0]; } catch { selected = -1; }
            if (selected != 1) Bus.Write(axis, 0x0300, 1);
            lock (parameterGate) parameters.Remove(axis);
            Bus.Write(axis, 0x1100, mode); Bus.Write(axis, 0x1101, 0); Bus.Write(axis, 0x1102, 1); Bus.Write(axis, 0x1103, 1); Bus.Write(axis, 0x1104, 1);
            lock (positionGate) positions.Remove(axis);
            Bus.U32(axis, 0x1106, position);
            lock (positionGate) positions.Remove(axis);
            Bus.Write(axis, 0x1108, config.PitchRpm(axis, rpm));
            Bus.U32(axis, 0x110a, accel); Bus.U32(axis, 0x110c, decel);
            lock (parameterGate) parameters.Remove(axis);
            Bus.U32(axis, 0x110e, 0);
        }
        public void PlannerStart(int axis, double initialMm = 0)
        {
            Servo(axis, false); sleep(.05);
            Configure(axis, 3, config.Units(axis, initialMm), config.Ini.Int("a6", "planner_start_rpm"), config.Ini.Int("a6", "planner_start_accel_ms"), config.Ini.Int("a6", "planner_start_decel_ms"));
            Servo(axis, true); Bus.Write(axis, 0x0415, 1); sleep(.01); Bus.Write(axis, 0x0415, 0); sleep(.05);
        }
        int[] ParameterValues(int axis, int rpm, int accel, int decel) { return new[] { config.PitchRpm(axis, Math.Max(0, rpm)), Math.Max(100, accel), Math.Max(100, decel) }; }
        public bool ParametersMatch(int axis, int rpm, int accel, int decel)
        { lock (parameterGate) { int[] old; return parameters.TryGetValue(axis, out old) && old.SequenceEqual(ParameterValues(axis, rpm, accel, decel)); } }
        public void Parameters(int axis, int rpm, int accel = 300, int decel = 300, bool queued = false)
        {
            lock (parameterGate)
            {
                int[] p = ParameterValues(axis, rpm, accel, decel), old;
                if (!parameters.TryGetValue(axis, out old)) old = new[] { -1, -1, -1 };
                if (old[0] != p[0]) { Bus.Write(axis, 0x1108, p[0], true, queued); old[0] = p[0]; }
                bool a = old[1] != p[1], d = old[2] != p[2];
                if (a && d) { Bus.TwoU32(axis, 0x110a, p[1], p[2], true, queued); old[1] = p[1]; old[2] = p[2]; }
                else if (a) { Bus.U32(axis, 0x110a, p[1], true, queued); old[1] = p[1]; }
                else if (d) { Bus.U32(axis, 0x110c, p[2], true, queued); old[2] = p[2]; }
                parameters[axis] = old;
            }
        }
        public void Position(int axis, double mm, bool trigger = true, bool queued = false, bool check = true)
        {
            int position = config.Units(axis, mm);
            lock (positionGate)
            {
                int old;
                if (!positions.TryGetValue(axis, out old) || old != position)
                { Bus.U32(axis, 0x1106, position, check, queued); positions[axis] = position; }
            }
            if (trigger) Trigger(axis, check);
        }
        public void Trigger(int axis = 0, bool check = true) { Bus.Write(axis, 0x0415, 1, check && axis != 0); Bus.Write(axis, 0x0415, 0, check && axis != 0); }
        public void PlannerStop(int axis)
        {
            lock (positionGate) positions.Remove(axis);
            Bus.U32(axis, 0x1106, 0);
            lock (positionGate) positions.Remove(axis);
            Trigger(axis); Servo(axis, false); Bus.Write(axis, 0x0415, 1); sleep(.01); Bus.Write(axis, 0x0415, 0);
        }
        public bool MoveToMm(int axis, double mm)
        {
            double until = Timing.Now + 5;
            while (Timing.Now < until && !PositionReached(axis)) sleep(.1);
            Bus.HighFirst(axis); int target = config.Units(axis, mm); Bus.ReadU32(axis, 0x4016, true);
            Configure(axis, 0, target, config.Ini.Int("a6", "move_rpm"), config.Ini.Int("a6", "move_accel_ms"), config.Ini.Int("a6", "move_decel_ms"));
            sleep(.5); Servo(axis, true); sleep(.3); Bus.Write(axis, 0x0415, 1); sleep(.1);
            int tolerance = Bus.Read(axis, 0x0312)[0]; if (tolerance == 0) tolerance = 10;
            until = Timing.Now + config.Ini.Number("a6", "operation_timeout_s"); bool reached = false;
            try
            {
                while (Timing.Now < until)
                {
                    long pos = Bus.ReadU32(axis, 0x4016, true); int speed = Bus.Read(axis, 0x4000)[0];
                    if (Math.Abs(pos - target) <= tolerance && Math.Abs(speed) < 10) { reached = true; break; }
                    sleep(config.Ini.Number("a6", "poll_interval_s"));
                }
            }
            finally { Bus.Write(axis, 0x0415, 0); Servo(axis, false); }
            return reached;
        }
        public void Disconnect()
        {
            Exception error = null;
            try
            {
                if (Bus.Connected)
                {
                    foreach (int axis in config.Enabled()) try { Servo(axis, false); } catch (Exception e) { error = error ?? e; }
                    sleep(.5);
                }
            }
            finally { try { Bus.Dispose(); } catch (Exception e) { error = error ?? e; } Reset(); }
            if (error != null) throw error;
        }
    }
}
