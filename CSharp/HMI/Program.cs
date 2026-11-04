using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Windows.Forms.DataVisualization.Charting;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public sealed class UiContext
    {
        public readonly Configuration Config;
        public readonly Ini Language;
        public readonly ComClient Client;
        readonly string hmiSession = Guid.NewGuid().ToString("N");
        public Status Status;
        public UiContext(string root, string pipeName = null) { Config = new Configuration(root); Language = new Ini(Config.PathFor("INI/Language.ini")); Client = new ComClient(pipeName); }
        public string T(string section, string key) { return Language.Get(section, key); }
        public async Task<Reply> Call(Request request)
        {
            request.HmiSession = hmiSession;
            Reply reply = await Task.Run(() => Client.Send(request));
            if (!reply.Ok) throw new InvalidOperationException(reply.Error);
            return reply;
        }
        public async Task<bool> Command(Control owner, Request request)
        {
            try { await Call(request); return true; }
            catch (Exception e) { if (!owner.IsDisposed) MessageBox.Show(owner, e.Message, "SimHub2SimRigCom", MessageBoxButtons.OK, MessageBoxIcon.Error); return false; }
        }
        public static Button Button(string text, Action action)
        {
            var button = new Button { Text = text, AutoSize = true, MinimumSize = new Size(100, 32), Margin = new Padding(4), FlatStyle = FlatStyle.System };
            button.Click += (s, e) => action(); return button;
        }
        public static Chart Chart(string x, string y)
        {
            var chart = new Chart { Dock = DockStyle.Fill, BackColor = Color.White, MinimumSize = new Size(200, 120) };
            var area = new ChartArea("main"); area.AxisX.Title = x; area.AxisY.Title = y;
            area.AxisX.LabelStyle.Format = "0.0"; area.AxisY.LabelStyle.Format = "0.##";
            area.AxisX.MajorGrid.LineColor = area.AxisY.MajorGrid.LineColor = Color.Gainsboro;
            area.CursorX.IsUserEnabled = true; area.CursorX.IsUserSelectionEnabled = true; area.AxisX.ScaleView.Zoomable = true;
            area.CursorY.IsUserEnabled = true; area.CursorY.IsUserSelectionEnabled = true; area.AxisY.ScaleView.Zoomable = true;
            chart.ChartAreas.Add(area); chart.Legends.Add(new Legend { Docking = Docking.Bottom });
            chart.DoubleClick += (s, e) => { area.AxisX.ScaleView.ZoomReset(0); area.AxisY.ScaleView.ZoomReset(0); };
            return chart;
        }
    }
    public sealed class MainForm : Form
    {
        readonly UiContext ui;
        readonly LogTable connection = new LogTable(true);
        readonly List<string> statusMessages = new List<string>();
        StatusHistoryForm statusHistoryForm;
        string lastConnection, lastActivity, lastComMessage;
        readonly DataGridView motors = new DataGridView();
        readonly SenderIntervalChart timings;
        readonly RigView rig;
        readonly Timer refresh = new Timer();
        readonly Button initialize, startCom, leveling, levelingStop, loads, maintenance, analysis, lightOn, lightOff;
        readonly RadioButton[] modes = new RadioButton[3];
        MaintenanceForm maintenanceForm;
        AnalysisForm analysisForm;
        bool polling, updating, closing, captureRequested, comUnavailable, startingCom;
        int chartTick;
        public MainForm(UiContext context)
        {
            ui = context; Text = "SimHub2SimRigHMI"; Font = new Font("Segoe UI", 9); Size = new Size(1320, 820); MinimumSize = new Size(1000, 650);
            StartPosition = FormStartPosition.Manual; var screen = Screen.AllScreens.Skip(1).FirstOrDefault() ?? Screen.PrimaryScreen; Location = new Point(screen.WorkingArea.Left + 25, screen.WorkingArea.Top + 25);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 4, Padding = new Padding(16) };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 30)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 70));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 0)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 132)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 72));
            Controls.Add(layout);
            connection.Name = "statusHistory"; connection.Dock = DockStyle.Fill; connection.Font = Font;
            var statusPanel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(6), BackColor = Color.WhiteSmoke };
            statusPanel.Controls.Add(connection); layout.Controls.Add(statusPanel, 0, 3); layout.SetColumnSpan(statusPanel, 2);
            layout.RowStyles[3].Height = connection.ColumnHeadersHeight + 3 * connection.RowTemplate.Height + statusPanel.Padding.Vertical + statusPanel.Margin.Vertical;
            connection.DoubleClick += (s, e) => ShowStatusHistory();
            statusPanel.DoubleClick += (s, e) => ShowStatusHistory();
            AddStatus("HMI gestartet - warte auf Com", true);
            rig = new RigView { Dock = DockStyle.Fill, RawMin = ui.Config.Ini.Int("simhub", "position_min"), RawMax = ui.Config.Ini.Int("simhub", "position_max") }; layout.Controls.Add(rig, 0, 0); layout.SetRowSpan(rig, 3);
            motors.Dock = DockStyle.Fill; motors.ReadOnly = true; motors.AllowUserToAddRows = motors.AllowUserToDeleteRows = false; motors.RowHeadersVisible = false; motors.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill; motors.BackgroundColor = Color.White; motors.BorderStyle = BorderStyle.None; motors.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            motors.DefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleCenter;
            motors.ColumnHeadersDefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleCenter;
            motors.Font = new Font(Font.FontFamily, Font.Size * 1.2f, Font.Style);
            motors.ColumnHeadersDefaultCellStyle.Font = motors.Font;
            motors.DefaultCellStyle.WrapMode = DataGridViewTriState.False;
            motors.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
            motors.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.AllCells;
            foreach (string key in new[] { "table_axis", "table_leveling_offset", "table_load_rate", "table_state", "table_grease" }) motors.Columns.Add(key, ui.T("MainDialog", key));
            for (int a = 1; a <= 7; a++) motors.Rows.Add(ui.T("Analysis", "axis_" + a), "—", "—", "—", "—");
            var motorPanel = new Panel { Dock = DockStyle.Fill };
            motorPanel.Controls.Add(motors);
            initialize = UiContext.Button("INIT", async () => {
                initialize.Enabled = false;
                await Send("initialize"); await Poll();
            });
            initialize.AutoSize = false; initialize.Dock = DockStyle.Fill;
            initialize.Font = new Font(Font.FontFamily, 32, FontStyle.Bold);
            initialize.MinimumSize = new Size(200, 96);
            motorPanel.Controls.Add(initialize); initialize.BringToFront();
            startCom = UiContext.Button("START", async () => await StartComAndInitialize());
            startCom.Visible = false; startCom.Enabled = false;
            startCom.AutoSize = false; startCom.Dock = DockStyle.Fill;
            startCom.Font = new Font(Font.FontFamily, 32, FontStyle.Bold);
            startCom.MinimumSize = initialize.MinimumSize;
            motorPanel.Controls.Add(startCom);
            layout.Controls.Add(motorPanel, 1, 0);
            Action fitMotorTable = () => {
                int height = Math.Max(initialize.MinimumSize.Height, motors.ColumnHeadersHeight + motors.Rows.GetRowsHeight(DataGridViewElementStates.Visible)) + motorPanel.Margin.Vertical;
                if (layout.RowStyles[0].Height != height) layout.RowStyles[0].Height = height;
            };
            motors.RowHeightChanged += (s, e) => fitMotorTable();
            motors.ColumnHeadersHeightChanged += (s, e) => fitMotorTable();
            motors.FontChanged += (s, e) => fitMotorTable();
            fitMotorTable();
            var actions = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.WhiteSmoke, ColumnCount = 4, RowCount = 3, Padding = new Padding(2) };
            for (int i = 0; i < 4; i++) actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
            for (int i = 0; i < 3; i++) actions.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
            leveling = UiContext.Button(ui.T("Panels", "leveling_start"), async () => await Send("leveling.start"));
            levelingStop = UiContext.Button(ui.T("Panels", "leveling_stop"), async () => await Send("leveling.stop"));
            loads = UiContext.Button(ui.T("Panels", "save_load_values"), async () => {
                if (MessageBox.Show(this, ui.T("MainDialog", "save_load_confirmation"), ui.T("Panels", "save_load_values"), MessageBoxButtons.OKCancel, MessageBoxIcon.Information) == DialogResult.OK) captureRequested = await Send("loads.save");
            });
            analysis = UiContext.Button(ui.T("Panels", "analysis"), async () => {
                if (analysisForm != null && !analysisForm.IsDisposed) { analysisForm.Activate(); return; }
                if (await Send("analysis.open")) { analysisForm = new AnalysisForm(ui); analysisForm.Show(this); }
            });
            maintenance = UiContext.Button(ui.T("Panels", "maintenance"), async () => {
                if (maintenanceForm != null && !maintenanceForm.IsDisposed) { maintenanceForm.Activate(); return; }
                if (await Send("maintenance.open")) { maintenanceForm = new MaintenanceForm(ui); maintenanceForm.Show(this); }
            });
            lightOn = UiContext.Button(ui.T("Panels", "light_on"), async () => await Light(true));
            lightOff = UiContext.Button(ui.T("Panels", "light_off"), async () => await Light(false));
            foreach (Button button in new[] { leveling, levelingStop, loads, maintenance, analysis, lightOn, lightOff }) button.Dock = DockStyle.Fill;
            actions.Controls.Add(leveling, 0, 0);
            actions.Controls.Add(levelingStop, 0, 1);
            actions.Controls.Add(loads, 0, 2);
            actions.Controls.Add(lightOn, 1, 0);
            actions.Controls.Add(lightOff, 1, 1);
            actions.Controls.Add(maintenance, 2, 0);
            actions.Controls.Add(analysis, 2, 1);
            var modePanel = new GroupBox { Text = ui.T("Panels", "mode"), Dock = DockStyle.Fill };
            var modeFlow = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false };
            string[] keys = { "mode_active", "mode_actual_positions", "mode_inactive" }, values = { "simhub", "actual_positions", "center" };
            for (int i = 0; i < 3; i++)
            {
                string value = values[i]; var radio = modes[i] = new RadioButton { Text = ui.T("Panels", keys[i]), AutoSize = true, Tag = value };
                radio.CheckedChanged += async (s, e) => { if (!updating && radio.Checked) await ui.Command(this, new Request { Operation = "mode", Mode = value }); };
                modeFlow.Controls.Add(radio);
            }
            modePanel.Controls.Add(modeFlow); actions.Controls.Add(modePanel, 3, 0); actions.SetRowSpan(modePanel, 3);
            layout.Controls.Add(actions, 1, 1);
            timings = new SenderIntervalChart(ui.Config.Ini.Number("ui", "trigger_chart_window_s"));
            layout.Controls.Add(timings, 1, 2);
            UpdateInitializationControls(false, false);
            refresh.Interval = Math.Max(50, ui.Config.Ini.Int("ui", "refresh_ms")); refresh.Tick += async (s, e) => await Poll(); refresh.Start();
            Shown += async (s, e) => await Poll();
            FormClosing += OnClosing;
        }
        Task<bool> Send(string command) { return ui.Command(this, new Request { Operation = command }); }
        async Task StartComAndInitialize()
        {
            if (startingCom || closing) return;
            startingCom = true; UpdateInitializationControls(false, false);
            AddStatus("Com-Verbindung wird hergestellt …", true);
            try
            {
                await ComStartup.Run(async () => (await ui.Call(new Request { Operation = "status" })).Status,
                    () => {
                        string executable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "SimHub2SimRigCom.exe");
                        if (!File.Exists(executable)) throw new FileNotFoundException("Com-Anwendung wurde nicht gefunden.", executable);
                        // Windows directory names cannot contain quotes. Double
                        // trailing backslashes so they cannot escape the quote.
                        string root = ui.Config.Root;
                        int trailing = root.Length - root.TrimEnd('\\').Length;
                        string quotedRoot = "\"" + root + new string('\\', trailing) + "\"";
                        var process = Process.Start(new ProcessStartInfo(executable, "--root " + quotedRoot) { WorkingDirectory = root, UseShellExecute = true, WindowStyle = ProcessWindowStyle.Normal });
                        if (process == null) throw new InvalidOperationException("Com konnte nicht gestartet werden.");
                        process.Dispose(); AddStatus("Com gestartet – warte auf Verbindung.", true);
                    }, async () => {
                        AddStatus("Verbindung zu Com steht – INIT wird angefordert.", true);
                        await ui.Call(new Request { Operation = "initialize" });
                    }, () => closing || IsDisposed);
            }
            catch (OperationCanceledException) { }
            catch (Exception e) { if (!IsDisposed && !closing) AddStatus("Com-Start/INIT fehlgeschlagen: " + e.Message, true); }
            finally { startingCom = false; }
            if (!IsDisposed && !closing) await Poll();
        }
        void ShowStatusHistory()
        {
            if (statusHistoryForm == null || statusHistoryForm.IsDisposed)
            {
                statusHistoryForm = new StatusHistoryForm(statusMessages);
                statusHistoryForm.Show(this);
            }
            else
            {
                if (statusHistoryForm.WindowState == FormWindowState.Minimized) statusHistoryForm.WindowState = FormWindowState.Normal;
                statusHistoryForm.Activate();
            }
        }
        void AddStatus(string message, bool timestamp)
        {
            if (string.IsNullOrWhiteSpace(message)) return;
            string output = timestamp ? "[HMI] " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + message : "[COM] " + message;
            statusMessages.Add(output); connection.AddMessage(output);
            while (connection.Rows.Count > 3) connection.Rows.RemoveAt(0);
            if (statusHistoryForm != null && !statusHistoryForm.IsDisposed) statusHistoryForm.AddMessage(output);
        }
        void UpdateInitializationControls(bool initialized, bool canInitialize)
        {
            bool showStart = comUnavailable && !startingCom;
            startCom.Visible = showStart; startCom.Enabled = showStart;
            if (showStart) startCom.BringToFront();
            initialize.Visible = !initialized && !showStart; initialize.Enabled = canInitialize && !startingCom;
            motors.Visible = initialized; motors.Enabled = rig.Enabled = timings.Enabled = initialized;
            foreach (Form dialog in OwnedForms) if (dialog != statusHistoryForm) dialog.Enabled = initialized;
            if (!initialized)
            {
                foreach (Button button in new[] { leveling, levelingStop, loads, maintenance, analysis, lightOn, lightOff }) button.Enabled = false;
                foreach (RadioButton radio in modes) radio.Enabled = false;
            }
        }
        async Task Light(bool enabled)
        {
            try
            {
                await Task.Run(() => {
                    string local = ui.Config.PathFor("INI/room_light.ini"); Ini source = File.Exists(local) ? new Ini(local) : ui.Config.Ini;
                    string url = source.Get("room_light", "uri").Replace("{status}", enabled ? "1" : "0");
                    var request = WebRequest.Create(url); request.Timeout = (int)(source.Number("room_light", "request_timeout_s") * 1000);
                    using (var response = request.GetResponse()) { }
                });
            }
            catch (Exception e) { if (!IsDisposed) MessageBox.Show(this, e.Message, ui.T("Panels", enabled ? "light_on" : "light_off")); }
        }
        async Task Poll()
        {
            if (polling || closing) return; polling = true;
            try
            {
                Reply reply = await ui.Call(new Request { Operation = "status" }); if (IsDisposed || closing) return;
                UpdateStatus(reply.Status);
            }
            catch (Exception e)
            {
                if (IsDisposed) return;
                string message = "SimHub2SimRigCom ist nicht erreichbar. Com starten. " + e.Message;
                if (message != lastConnection) { AddStatus(message, true); lastConnection = message; }
                connection.ForeColor = Color.Firebrick;
                comUnavailable = true;
                UpdateInitializationControls(false, false);
            }
            finally { polling = false; }
        }
        public void UpdateStatus(Status status)
        {
                comUnavailable = false;
                ui.Status = status;
                string summary = "SimHub2SimRigCom  |  " + (status.Simulated ? "SIMULATION  |  " : "") + "SimHub: " + (status.Connected ? "Connected" : "Disconnected") + (status.GameRunning ? "  /  Recording" : "");
                if (summary != lastConnection) { AddStatus(summary, true); lastConnection = summary; }
                connection.ForeColor = status.Simulated ? Color.DarkOrange : status.Connected ? Color.ForestGreen : Color.SteelBlue;
                BackColor = ColorTranslator.FromHtml(ui.Config.Ini.Get("ui", status.Connected ? "connected_background" : "disconnected_background"));
                string activity = status.Activity + (string.IsNullOrEmpty(status.LastError) ? "" : "  |  " + status.LastError);
                if (activity != lastActivity) { AddStatus(activity, true); lastActivity = activity; }
                if (!string.IsNullOrEmpty(status.LastError)) connection.ForeColor = Color.Firebrick;
                if (status.Messages != null && status.Messages.Length > 0)
                {
                    int start = Array.IndexOf(status.Messages, lastComMessage) + 1;
                    for (int i = start; i < status.Messages.Length; i++) AddStatus(status.Messages[i], false);
                    lastComMessage = status.Messages[status.Messages.Length - 1];
                }
                bool ready = status.Initialized && !status.Busy && status.Playback == "stopped";
                lightOn.Enabled = lightOff.Enabled = status.Initialized;
                leveling.Enabled = ready && !status.Connected && !status.Maintenance && !status.Analysis && status.Mode != "center";
                levelingStop.Enabled = status.Leveling || status.Activity == "Leveling";
                loads.Enabled = ready && !status.GameRunning && !status.Maintenance && !status.Analysis;
                maintenance.Enabled = status.Maintenance || (ready && !status.Connected && !status.Analysis);
                analysis.Enabled = status.Analysis || (ready && !status.Connected && !status.Maintenance);
                updating = true;
                foreach (RadioButton radio in modes) { radio.Checked = (string)radio.Tag == status.Mode; radio.Enabled = ready && !status.Maintenance && !status.Analysis; }
                updating = false;
                foreach (AxisStatus axis in status.Axes)
                {
                    var row = motors.Rows[axis.Axis - 1];
                    row.Cells[1].Value = axis.Offset.ToString("F2") + " mm"; row.Cells[2].Value = status.GameRunning ? "" : axis.Load.ToString("F1") + " %";
                    row.Cells[3].Value = DriveStateView.Text(ui, axis, status);
                    string key = axis.Axis <= 3 ? axis.Axis.ToString() : "4-7";
                    double hours = status.Grease[key].playtimeMinutes / 60;
                    row.Cells[4].Value = hours >= ui.Config.Ini.Number("grease", "alarm_after_operating_hours") ? ui.T("MainDialog", "grease_required") : hours >= ui.Config.Ini.Number("grease", "warning_after_operating_hours") ? ui.T("MainDialog", "grease_soon_required") : ui.T("Common", "status_ok");
                    row.DefaultCellStyle.BackColor = DriveStateView.IsFault(axis) ? Color.MistyRose : axis.Axis == status.FixedAxis || DriveStateView.IsInhibited(axis) ? Color.LightGoldenrodYellow : Color.White;
                }
                rig.Status = status; rig.Invalidate();
                if (++chartTick * refresh.Interval >= ui.Config.Ini.Int("ui", "trigger_chart_refresh_ms"))
                {
                    chartTick = 0; timings.SetSamples(status.TriggerIntervals);
                }
                if (maintenanceForm != null && !maintenanceForm.IsDisposed) maintenanceForm.UpdateStatus(status);
                if (analysisForm != null && !analysisForm.IsDisposed) analysisForm.UpdateStatus(status);
                UpdateInitializationControls(status.Initialized, !status.Initialized && !status.Busy);
                if (captureRequested && !status.Busy)
                {
                    captureRequested = false;
                    if (string.IsNullOrEmpty(status.LastError) && status.CalibrationLoads != null)
                    {
                        string loadText = string.Join(", ", status.CalibrationLoads.Select((v, i) => (i + 4) + ": " + (v / 10).ToString("F1") + "%"));
                        string message = ui.T("MainDialog", "save_load_success").Replace("{load_text}", loadText).Replace("{front_rear:.1f}", status.LoadCenter[0].ToString("F1")).Replace("{left_right:.1f}", status.LoadCenter[1].ToString("F1"));
                        MessageBox.Show(this, message, ui.T("MainDialog", "save_load_success_title"));
                    }
                }
        }
        protected override void Dispose(bool disposing) { if (disposing) refresh.Dispose(); base.Dispose(disposing); }
        void OnClosing(object sender, FormClosingEventArgs e)
        {
            closing = true; refresh.Stop();
            // Close local windows without issuing maintenance/analysis stop
            // commands. Com owns ongoing operations independently of this UI.
            if (maintenanceForm != null) maintenanceForm.ForceClose();
            if (analysisForm != null) analysisForm.ForceClose();
        }
    }
    public sealed class RigView : Control
    {
        public Status Status;
        public int RawMin, RawMax = 131071;
        public RigView() { DoubleBuffered = true; BackColor = Color.White; }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); if (Status == null) return;
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (var title = new Font("Segoe UI", 11, FontStyle.Bold)) e.Graphics.DrawString("SimRig", title, Brushes.SteelBlue, 16, 12);
            int y = 52;
            foreach (AxisStatus axis in Status.Axes)
            {
                int width = Math.Max(40, Width - 48);
                e.Graphics.DrawString(axis.Axis + "     " + axis.Target.ToString("F1") + " mm", Font, Brushes.DimGray, 20, y);
                e.Graphics.FillRectangle(Brushes.AliceBlue, 20, y + 23, width, 12);
                e.Graphics.DrawLine(Pens.SlateGray, 20 + width / 2, y + 20, 20 + width / 2, y + 38);
                double fraction = Status.LatestPositions == null ? (axis.Target + 100) / 200 : (Status.LatestPositions[axis.Axis - 1] - RawMin) / (double)(RawMax - RawMin);
                float x = 20 + (float)Math.Max(0, Math.Min(1, fraction)) * width;
                e.Graphics.FillRectangle(Brushes.SteelBlue, x - 2, y + 21, 4, 16);
                y += Math.Max(53, (Height - 70) / 7);
            }
        }
    }
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                bool created;
                using (var single = new System.Threading.Mutex(true, "Local\\" + Protocol.PipeName + ".HMI", out created))
                {
                    if (!created) throw new InvalidOperationException("SimHub2SimRigHMI läuft bereits.");
                    try { Application.Run(new MainForm(new UiContext(Configuration.FindRoot(args)))); }
                    finally { single.ReleaseMutex(); }
                }
                return 0;
            }
            catch (Exception e) { MessageBox.Show(e.ToString(), "SimHub2SimRigHMI", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        }
    }
}
