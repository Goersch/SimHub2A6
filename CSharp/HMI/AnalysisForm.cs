using System;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;
using System.Windows.Forms.DataVisualization.Charting;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public sealed class AnalysisForm : Form
    {
        readonly UiContext ui;
        readonly ListBox files = new ListBox();
        readonly CheckBox[] axes = new CheckBox[7], signals = new CheckBox[4];
        readonly Chart chart;
        readonly Button play, pause, stop;
        readonly Button[] seek = new Button[4];
        readonly Label status = new Label();
        readonly Color[] colors = { Color.FromArgb(214, 39, 40), Color.FromArgb(255, 127, 14), Color.FromArgb(44, 160, 44), Color.FromArgb(31, 119, 180), Color.FromArgb(148, 103, 189), Color.FromArgb(140, 86, 75), Color.FromArgb(23, 190, 207) };
        Sample[] samples = new Sample[0];
        int generation;
        bool force, closing;
        public AnalysisForm(UiContext ui)
        {
            this.ui = ui; Text = ui.T("Analysis", "title"); Font = new Font("Segoe UI", 9); Size = new Size(1350, 780); MinimumSize = new Size(1000, 600); StartPosition = FormStartPosition.CenterParent;
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 5, Padding = new Padding(16) };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 235)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 65)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 45)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30)); Controls.Add(layout);
            files.Dock = DockStyle.Fill; files.SelectedIndexChanged += async (s, e) => await LoadPreview(); layout.Controls.Add(files, 0, 0); layout.SetRowSpan(files, 5);
            var selectors = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoScroll = true };
            selectors.Controls.Add(UiContext.Button(ui.T("Analysis", "deselect_all_axes"), () => { foreach (var a in axes) a.Checked = false; }));
            selectors.Controls.Add(UiContext.Button(ui.T("Analysis", "select_hub_axes"), () => { for (int i = 0; i < 7; i++) axes[i].Checked = i >= 3; }));
            for (int i = 0; i < 7; i++) { axes[i] = new CheckBox { Text = ui.T("Analysis", "axis_" + (i + 1)), Checked = true, AutoSize = true, ForeColor = colors[i] }; axes[i].CheckedChanged += (s, e) => Draw(); selectors.Controls.Add(axes[i]); }
            layout.Controls.Add(selectors, 1, 0);
            chart = UiContext.Chart(ui.T("Analysis", "time_axis") + " [s]", "mm"); layout.Controls.Add(chart, 1, 1);
            var signalFlow = new FlowLayoutPanel { Dock = DockStyle.Fill };
            string[] keys = { "signal_target", "signal_calculated_actual", "signal_actual", "signal_ground_deviation" };
            for (int i = 0; i < 4; i++) { signals[i] = new CheckBox { Text = ui.T("Analysis", keys[i]), Checked = i == 0, AutoSize = true }; signals[i].CheckedChanged += (s, e) => Draw(); signalFlow.Controls.Add(signals[i]); }
            layout.Controls.Add(signalFlow, 1, 2);
            var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill };
            play = UiContext.Button("▶", async () => {
                if (files.SelectedItem == null) return;
                await ui.Command(this, new Request { Operation = "playback.play", FileName = (string)files.SelectedItem, Axes = Enumerable.Range(1, 7).Where(a => axes[a - 1].Checked).ToArray() });
            });
            pause = UiContext.Button("Ⅱ", async () => await ui.Command(this, new Request { Operation = "playback.pause" }));
            stop = UiContext.Button("■", async () => await ui.Command(this, new Request { Operation = "playback.stop" }));
            buttons.Controls.AddRange(new Control[] { play, pause, stop });
            int[] deltas = { -60, -10, 10, 60 };
            for (int i = 0; i < 4; i++) { int delta = deltas[i]; seek[i] = UiContext.Button((delta > 0 ? "+" : "") + delta + " s", async () => await ui.Command(this, new Request { Operation = "playback.seek", Seconds = delta })); seek[i].MinimumSize = new Size(60, 32); buttons.Controls.Add(seek[i]); }
            layout.Controls.Add(buttons, 1, 3); status.AutoSize = true; layout.Controls.Add(status, 1, 4);
            Shown += async (s, e) => {
                try
                {
                    Reply reply = await ui.Call(new Request { Operation = "files" }); if (IsDisposed) return; files.Items.AddRange(reply.Files);
                    if (files.Items.Count > 0) { int active = ui.Status == null ? -1 : files.Items.IndexOf(ui.Status.PlaybackFile ?? ""); files.SelectedIndex = active < 0 ? 0 : active; }
                }
                catch (Exception failure) { if (!IsDisposed) status.Text = failure.Message; }
            };
            FormClosing += async (s, e) => {
                if (force || e.CloseReason == CloseReason.FormOwnerClosing) return; e.Cancel = true; if (closing) return; closing = true;
                if (await ui.Command(this, new Request { Operation = "analysis.close" })) { force = true; Close(); }
                closing = false;
            };
            if (ui.Status != null) UpdateStatus(ui.Status);
        }
        public void ForceClose() { force = true; Close(); }
        async System.Threading.Tasks.Task LoadPreview()
        {
            if (files.SelectedItem == null) return; int current = ++generation;
            string file = (string)files.SelectedItem; status.Text = ui.T("Analysis", "loading_data");
            try
            {
                Reply reply = await ui.Call(new Request { Operation = "preview", FileName = file });
                if (IsDisposed || current != generation) return; SetPreview(reply.Samples); status.Text = file + "  |  " + samples.Length + " points";
            }
            catch (Exception e) { if (!IsDisposed) status.Text = e.Message; }
        }
        public void SetPreview(Sample[] values) { samples = values ?? new Sample[0]; Draw(); }
        void Draw()
        {
            if (chart == null || signals.Any(s => s == null)) return;
            chart.Series.Clear(); ChartDashStyle[] styles = { ChartDashStyle.Solid, ChartDashStyle.Dash, ChartDashStyle.Dot, ChartDashStyle.DashDot };
            chart.Titles.Clear(); if (samples.Length == 0) chart.Titles.Add(ui.T("Analysis", "no_data"));
            for (int a = 0; a < 7; a++) if (axes[a].Checked)
                for (int signal = 0; signal < 4; signal++) if (signals[signal].Checked && (signal != 3 || a >= 3))
                {
                    var series = new Series(axes[a].Text + " / " + signals[signal].Text) { ChartType = SeriesChartType.FastLine, Color = colors[a], BorderWidth = signal == 2 ? 2 : 1, BorderDashStyle = styles[signal] };
                    foreach (Sample sample in samples) series.Points.AddXY(sample.Seconds, (signal == 0 ? sample.Target : signal == 1 ? sample.Calculated : signal == 2 ? sample.Actual : sample.Ground)[a]);
                    chart.Series.Add(series);
                }
            signals[3].Enabled = axes.Skip(3).Any(a => a.Checked);
        }
        public void UpdateStatus(Status s)
        {
            bool valid = samples.Length > 0 && s.Analysis && !s.Busy;
            play.Enabled = valid && s.Playback != "playing" && s.Mode != "center"; pause.Enabled = s.Playback == "playing"; stop.Enabled = s.Playback != "stopped";
            foreach (Button button in seek) button.Enabled = valid && s.Playback != "playing";
            files.Enabled = s.Playback == "stopped";
            foreach (CheckBox axis in axes) axis.Enabled = s.Playback == "stopped";
            if (!string.IsNullOrEmpty(s.PlaybackFile)) status.Text = s.PlaybackFile + "  |  " + s.Playback + "  |  " + s.PlaybackSeconds.ToString("F2") + " s";
            var area = chart.ChartAreas[0]; area.AxisX.StripLines.Clear();
            if (samples.Length > 0 && s.PlaybackFile == (string)files.SelectedItem) area.AxisX.StripLines.Add(new StripLine { IntervalOffset = s.PlaybackSeconds, StripWidth = .015, BackColor = Color.Red });
        }
    }
}
