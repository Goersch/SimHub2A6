using System;
using System.IO;
using System.Linq;
using SimHub2SimRig.Com;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static void RecordingInactivity()
        {
            var c = new Configuration(Prepare(root));
            double now = 100;
            int[] values = Enumerable.Repeat(65535, 7).ToArray();
            using (var recorder = new Recorder(c, () => now))
            {
                recorder.SetRunning(true); recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                string first = recorder.CurrentFile;
                now += 10; recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                Check(recorder.IsRecording, "Exactly ten unchanged seconds still records");
                now += .001;
                recorder.ReadActual(true); recorder.SetActual(1, 12);
                recorder.Stage(1, 25, 400, 100, 400, 80000); recorder.Trigger();
                recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                Check(!recorder.IsRecording, "Repeated input, feedback and calculated movement do not extend recording");
                string firstPath = c.PathFor("SimHubData/" + first);
                Equal(3, File.ReadAllLines(firstPath).Length, "Inactivity closes and flushes the CSV before writing another row");
                now += 20; recorder.SetRunning(true); recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                Check(!recorder.IsRecording, "Repeated START and unchanged packets do not resume an idle recording");
                Equal(1, Directory.GetFiles(c.PathFor("SimHubData"), "*.csv").Length, "No extra idle files");

                string previous = first;
                for (int axis = 0; axis < 7; axis++)
                {
                    values[axis]++; recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                    Check(recorder.IsRecording, "One raw unit on axis " + (axis + 1) + " resumes recording");
                    Check(recorder.CurrentFile != previous, "Every resume creates a separate file");
                    previous = recorder.CurrentFile;
                    now += 9.9; recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                    Check(recorder.IsRecording, "Changed data resets the inactivity deadline");
                    now += .101; recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                    Check(!recorder.IsRecording, "Every recording pauses again after ten unchanged seconds");
                    Equal(3, File.ReadAllLines(c.PathFor("SimHubData/" + previous)).Length, "Resumed recording contains its own header and samples");
                }
                Equal(3, File.ReadAllLines(firstPath).Length, "Resuming never appends to the old recording");
                recorder.SetRunning(false); values[0]++; recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                Check(!recorder.IsRecording, "Data does not resume recording after END");
                recorder.SetRunning(true); recorder.ObserveSimHubPositions(values); recorder.Tick(.025);
                Check(recorder.IsRecording && recorder.CurrentFile != previous, "A new session starts even with the previous session's values");
                recorder.SetRunning(false);
                Check(!recorder.IsRecording, "END immediately stops an active recording");
                recorder.SetRunning(true); now += 10.001; recorder.Tick(.025);
                Check(!recorder.IsRecording, "START without position data also times out");
            }
        }
    }
}
