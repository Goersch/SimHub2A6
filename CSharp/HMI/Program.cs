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
        public readonly HmiConfiguration Config;
        public readonly Ini Language;
        public readonly ComClient Client;
        readonly string hmiSession = Guid.NewGuid().ToString("N");
        public Status Status;
        public UiContext(string root, string pipeName = null) { Config = new HmiConfiguration(root); Language = new Ini(Config.PathFor("INI/Language.ini")); Client = new ComClient(pipeName); }
        public string T(string section, string key) { return Language.Get(section, key); }
        public string F(string section, string key, params object[] values) { return LanguageText.Format(Language, section, key, values); }
        public string Message(string value) { return LanguageText.TranslateMessage(Language, value); }
        public DialogResult ShowMessage(IWin32Window owner, string text, string title, MessageBoxButtons buttons = MessageBoxButtons.OK, MessageBoxIcon icon = MessageBoxIcon.Error)
        { return LanguageText.ShowMessage(this, owner, text, title, buttons, icon); }
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
            catch (Exception e) { if (!owner.IsDisposed) ShowMessage(owner, F("Errors", "command_failed", "error", Message(e.Message)), T("Errors", "command_title")); return false; }
        }
        public static Button Button(string text, Action action)
        {
            var button = new Button { Text = text, AutoSize = true, MinimumSize = new Size(100, 32), Margin = new Padding(4), FlatStyle = FlatStyle.System };
            button.Click += (s, e) => action(); return button;
        }
        public static Chart Chart(string x, string y, UiContext ui = null)
        {
            var language = ui == null ? LanguageText.LoadDefault() : ui.Language;
            var chart = new Chart { Dock = DockStyle.Fill, BackColor = Color.White, MinimumSize = new Size(200, 120) };
            var area = new ChartArea("main"); area.AxisX.Title = x; area.AxisY.Title = y;
            area.AxisX.LabelStyle.Format = language.Get("Formats", "chart_x_number"); area.AxisY.LabelStyle.Format = language.Get("Formats", "chart_y_number");
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
        readonly LogTable connection;
        readonly List<string> statusMessages = new List<string>();
        StatusHistoryForm statusHistoryForm;
        string lastConnection, lastActivity, lastComMessage;
        readonly DataGridView motors = new DataGridView();
        readonly Action fitMotorTable;
        readonly SenderIntervalChart timings;
        readonly RigView rig;
        readonly Timer refresh = new Timer();
        readonly Timer clockRefresh = new Timer();
        readonly Label clock = new Label();
        readonly Button initialize, startCom, lightOn, lightOff;
        readonly ToolStripMenuItem leveling, levelingStop, loads, maintenance, analysis;
        readonly ToolStripMenuItem[] modes = new ToolStripMenuItem[3];
        readonly ToolStripMenuItem[] senderModes = new ToolStripMenuItem[2];
        MaintenanceForm maintenanceForm;
        AnalysisForm analysisForm;
        bool polling, closing, captureRequested, comUnavailable, startingCom;
        int chartTick;
        public MainForm(UiContext context)
        {
            ui = context; Text = ui.T("MainDialog", "hmi_title"); Font = new Font("Segoe UI", 9); Size = new Size(1320, 820); MinimumSize = new Size(1000, 650);
            connection = new LogTable(true, ui);
            StartPosition = FormStartPosition.Manual;
            string monitorName = @"\\.\DISPLAY" + ui.Config.Monitor.ToString(System.Globalization.CultureInfo.InvariantCulture);
            var screen = Screen.AllScreens.FirstOrDefault(s => string.Equals(s.DeviceName, monitorName, StringComparison.OrdinalIgnoreCase)) ?? Screen.PrimaryScreen;
            Location = new Point(screen.WorkingArea.Left + 25, screen.WorkingArea.Top + 25);
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 4, Padding = new Padding(16) };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 30)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 70));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 48)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 0)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 72));
            Controls.Add(layout);
            connection.Name = "statusHistory"; connection.Dock = DockStyle.Fill; connection.Font = Font;
            var statusPanel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(6), BackColor = Color.WhiteSmoke };
            statusPanel.Controls.Add(connection); layout.Controls.Add(statusPanel, 0, 3); layout.SetColumnSpan(statusPanel, 2);
            layout.RowStyles[3].Height = connection.ColumnHeadersHeight + 3 * connection.RowTemplate.Height + statusPanel.Padding.Vertical + statusPanel.Margin.Vertical;
            connection.DoubleClick += (s, e) => ShowStatusHistory();
            statusPanel.DoubleClick += (s, e) => ShowStatusHistory();
            AddStatus(ui.T("Status", "hmi_started"), true);
            var leftPanel = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, Margin = Padding.Empty };
            leftPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            leftPanel.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            leftPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            clock.Name = "clock"; clock.Dock = DockStyle.Fill; clock.TextAlign = ContentAlignment.MiddleCenter;
            clock.Font = new Font(Font.FontFamily, 14, FontStyle.Bold); clock.ForeColor = Color.Gray;
            clock.Text = DateTime.Now.ToString(ui.T("Formats", "clock"), System.Globalization.CultureInfo.CurrentCulture);
            leftPanel.Controls.Add(clock, 0, 0);
            rig = new RigView(ui) { Dock = DockStyle.Fill };
            leftPanel.Controls.Add(rig, 0, 1); layout.Controls.Add(leftPanel, 0, 0); layout.SetRowSpan(leftPanel, 3);
            clockRefresh.Interval = 1000;
            clockRefresh.Tick += (s, e) => clock.Text = DateTime.Now.ToString(ui.T("Formats", "clock"), System.Globalization.CultureInfo.CurrentCulture);
            clockRefresh.Start();
            motors.Dock = DockStyle.Fill; motors.ReadOnly = true; motors.AllowUserToAddRows = motors.AllowUserToDeleteRows = false; motors.RowHeadersVisible = false; motors.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill; motors.BackgroundColor = Color.White; motors.BorderStyle = BorderStyle.None; motors.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            motors.DefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleCenter;
            motors.ColumnHeadersDefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleCenter;
            motors.Font = new Font(Font.FontFamily, Font.Size * 1.2f, Font.Style);
            motors.ColumnHeadersDefaultCellStyle.Font = motors.Font;
            motors.DefaultCellStyle.WrapMode = DataGridViewTriState.False;
            motors.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
            motors.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.AllCells;
            foreach (string key in new[] { "table_axis", "table_leveling_offset", "table_load_rate", "table_state", "table_grease" }) motors.Columns.Add(key, ui.T("MainDialog", key));
            for (int a = 1; a <= 7; a++) motors.Rows.Add(ui.T("Analysis", "axis_" + a), ui.T("Common", "placeholder"), ui.T("Common", "placeholder"), ui.T("Common", "placeholder"), ui.T("Common", "placeholder"));
            var motorPanel = new Panel { Dock = DockStyle.Fill };
            motorPanel.Controls.Add(motors);
            initialize = UiContext.Button(ui.T("MainDialog", "initialize"), async () => {
                initialize.Enabled = false;
                await Send("initialize"); await Poll();
            });
            initialize.Name = "initialize"; initialize.AutoSize = false; initialize.Dock = DockStyle.Fill;
            initialize.Font = new Font(Font.FontFamily, 32, FontStyle.Bold);
            initialize.MinimumSize = new Size(200, 96);
            motorPanel.Controls.Add(initialize); initialize.BringToFront();
            startCom = UiContext.Button(ui.T("MainDialog", "start_com"), async () => await StartComAndInitialize());
            startCom.Name = "startCom"; startCom.Visible = false; startCom.Enabled = false;
            startCom.AutoSize = false; startCom.Dock = DockStyle.Fill;
            startCom.Font = new Font(Font.FontFamily, 32, FontStyle.Bold);
            startCom.MinimumSize = initialize.MinimumSize;
            motorPanel.Controls.Add(startCom);
            layout.Controls.Add(motorPanel, 1, 1);
            bool fittingMotorTable = false;
            fitMotorTable = () => {
                if (fittingMotorTable) return;
                fittingMotorTable = true;
                try
                {
                    // RowHeightChanged can run before the grid invalidates its
                    // cached total height. Measure the rows directly, including
                    // a little space below the final row for startup/DPI rounding.
                    int rowsHeight = motors.Rows.Cast<DataGridViewRow>().Where(row => row.Visible).Sum(row => row.Height);
                    int height = Math.Max(initialize.MinimumSize.Height, motors.ColumnHeadersHeight + rowsHeight + Math.Max(4, motors.Font.Height / 3)) + motorPanel.Margin.Vertical;
                    if (layout.RowStyles[1].Height != height) layout.RowStyles[1].Height = height;
                }
                finally { fittingMotorTable = false; }
            };
            motors.RowHeightChanged += (s, e) => fitMotorTable();
            motors.ColumnHeadersHeightChanged += (s, e) => fitMotorTable();
            motors.FontChanged += (s, e) => fitMotorTable();
            motors.Layout += (s, e) => fitMotorTable();
            motors.VisibleChanged += (s, e) => fitMotorTable();
            Shown += (s, e) => { motors.AutoResizeRows(DataGridViewAutoSizeRowsMode.AllCells); fitMotorTable(); };
            fitMotorTable();
            var actions = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.WhiteSmoke, ColumnCount = 2, RowCount = 1, Padding = new Padding(2) };
            actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            actions.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            leveling = MenuAction(ui.T("Panels", "grease_start"), async () => await Send("leveling.start"));
            levelingStop = MenuAction(ui.T("Panels", "grease_stop"), async () => await Send("leveling.stop"));
            loads = MenuAction(ui.T("Panels", "save"), async () => {
                if (ui.ShowMessage(this, ui.T("MainDialog", "save_load_confirmation"), ui.T("Panels", "save_load_values"), MessageBoxButtons.OKCancel, MessageBoxIcon.Information) == DialogResult.OK) captureRequested = await Send("loads.save");
            });
            analysis = MenuAction(ui.T("Panels", "analysis"), async () => {
                if (analysisForm != null && !analysisForm.IsDisposed) { analysisForm.Activate(); return; }
                if (await Send("analysis.open")) { analysisForm = new AnalysisForm(ui); analysisForm.Show(this); }
            });
            maintenance = MenuAction(ui.T("Panels", "maintenance"), async () => {
                if (maintenanceForm != null && !maintenanceForm.IsDisposed) { maintenanceForm.Activate(); return; }
                if (await Send("maintenance.open")) { maintenanceForm = new MaintenanceForm(ui); maintenanceForm.Show(this); }
            });
            lightOn = UiContext.Button(ui.T("Panels", "light_on"), async () => await Light(true));
            lightOff = UiContext.Button(ui.T("Panels", "light_off"), async () => await Light(false));
            lightOn.Name = "lightOn"; lightOff.Name = "lightOff";
            foreach (Button button in new[] { lightOn, lightOff }) button.Dock = DockStyle.Fill;
            actions.Controls.Add(lightOn, 0, 0);
            actions.Controls.Add(lightOff, 1, 0);
            var modeMenu = new ToolStripMenuItem(ui.T("Panels", "mode")) { Name = "modeMenu" };
            string[] keys = { "mode_active", "mode_actual_positions", "mode_inactive" }, values = { "simhub", "actual_positions", "center" };
            for (int i = 0; i < 3; i++)
            {
                string value = values[i];
                var item = modes[i] = new ToolStripMenuItem(ui.T("Panels", keys[i]).Replace("&", "&&")) { Tag = value };
                item.Click += async (s, e) => {
                    if (!item.Checked && await ui.Command(this, new Request { Operation = "mode", Mode = value })) await Poll();
                };
                modeMenu.DropDownItems.Add(item);
            }
            modeMenu.DropDownItems.Add(new ToolStripSeparator());
            string[] senderKeys = { "sender_oversampling", "sender_simhub_cycle" }, senderValues = { "oversampling", "simhub_cycle" };
            for (int i = 0; i < senderModes.Length; i++)
            {
                string value = senderValues[i];
                var item = senderModes[i] = new ToolStripMenuItem(ui.T("Panels", senderKeys[i]).Replace("&", "&&")) { Name = "senderMode_" + value, Tag = value };
                item.Click += async (s, e) => {
                    if (!item.Checked && await ui.Command(this, new Request { Operation = "sender.mode", SenderMode = value })) await Poll();
                };
                modeMenu.DropDownItems.Add(item);
            }
            layout.Controls.Add(actions, 1, 0);
            timings = new SenderIntervalChart(ui.Config.Ini.Number("ui", "trigger_chart_window_s"), ui);
            var timingPanel = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, Margin = Padding.Empty };
            timingPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            timingPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            timingPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            var timingSources = new FlowLayoutPanel { Name = "timingSources", Dock = DockStyle.Fill, AutoSize = true, WrapContents = false, BackColor = Color.White };
            timingSources.Controls.Add(new Label { Text = ui.T("Panels", "chart_data"), AutoSize = true, Margin = new Padding(3, 5, 10, 0) });
            foreach (string source in new[] { "SimHub", "ModBus", "Both" })
            {
                string selected = source;
                var radio = new RadioButton { Name = "chart" + source, Tag = source, Text = ui.T("Panels", "chart_" + source.ToLowerInvariant()), AutoSize = true, Checked = source == "Both", ForeColor = source == "SimHub" ? Color.DarkOrange : source == "ModBus" ? Color.SteelBlue : SystemColors.ControlText };
                radio.CheckedChanged += (s, e) => { if (radio.Checked) timings.SetVisibleSources(selected != "ModBus", selected != "SimHub"); };
                timingSources.Controls.Add(radio);
            }
            timingPanel.Controls.Add(timingSources, 0, 0);
            timingPanel.Controls.Add(timings, 0, 1);
            layout.Controls.Add(timingPanel, 1, 2);
            var menu = new MenuStrip { Name = "mainMenu", Font = Font };
            var fileMenu = new ToolStripMenuItem(ui.T("Menus", "file")) { Name = "fileMenu" };
            fileMenu.DropDownItems.Add(new ToolStripMenuItem(ui.T("Menus", "exit"), null, (s, e) => Close()));
            var levelingMenu = new ToolStripMenuItem(ui.T("Menus", "leveling")) { Name = "levelingMenu" };
            levelingMenu.DropDownItems.AddRange(new ToolStripItem[] { leveling, levelingStop, loads });
            var toolsMenu = new ToolStripMenuItem(ui.T("Menus", "tools")) { Name = "toolsMenu" };
            toolsMenu.DropDownItems.AddRange(new ToolStripItem[] { maintenance, analysis });
            menu.Items.AddRange(new ToolStripItem[] { fileMenu, levelingMenu, modeMenu, toolsMenu });
            MainMenuStrip = menu;
            Controls.Add(menu);
            UpdateInitializationControls(false, false);
            refresh.Interval = Math.Max(50, ui.Config.Ini.Int("ui", "refresh_ms")); refresh.Tick += async (s, e) => await Poll(); refresh.Start();
            Shown += async (s, e) => await Poll();
            FormClosing += OnClosing;
        }
        static ToolStripMenuItem MenuAction(string text, Action action)
        {
            return new ToolStripMenuItem(text, null, (s, e) => action());
        }
        Task<bool> Send(string command) { return ui.Command(this, new Request { Operation = command }); }
        async Task StartComAndInitialize()
        {
            if (startingCom || closing) return;
            startingCom = true; UpdateInitializationControls(false, false);
            AddStatus(ui.T("Status", "com_connecting"), true);
            try
            {
                await ComStartup.Run(async () => (await ui.Call(new Request { Operation = "status" })).Status,
                    () => {
                        string executable = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "SimHub2SimRigCom.exe");
                        if (!File.Exists(executable)) throw new FileNotFoundException(ui.T("Errors", "com_missing"), executable);
                        // Windows directory names cannot contain quotes. Double
                        // trailing backslashes so they cannot escape the quote.
                        string root = ui.Config.Root;
                        int trailing = root.Length - root.TrimEnd('\\').Length;
                        string quotedRoot = "\"" + root + new string('\\', trailing) + "\"";
                        using (var process = Process.Start(new ProcessStartInfo(executable, "--root " + quotedRoot) { WorkingDirectory = root, UseShellExecute = true, WindowStyle = ProcessWindowStyle.Normal }))
                        {
                            if (process == null) throw new InvalidOperationException(ui.T("Errors", "com_launch_failed"));
                            process.PriorityClass = ProcessPriorityClass.High;
                        }
                        AddStatus(ui.T("Status", "com_launched"), true);
                    }, async () => {
                        AddStatus(ui.T("Status", "com_initializing"), true);
                        await ui.Call(new Request { Operation = "initialize" });
                    }, () => closing || IsDisposed, ui.T("Errors", "com_start_timeout"));
            }
            catch (OperationCanceledException) { }
            catch (Exception e) { if (!IsDisposed && !closing) AddStatus(ui.F("Errors", "com_start_failed", "error", ui.Message(e.Message)), true); }
            finally { startingCom = false; }
            if (!IsDisposed && !closing) await Poll();
        }
        void ShowStatusHistory()
        {
            if (statusHistoryForm == null || statusHistoryForm.IsDisposed)
            {
                statusHistoryForm = new StatusHistoryForm(statusMessages, ui);
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
            bool showingMotors = initialized && !motors.Visible;
            bool showStart = comUnavailable && !startingCom;
            startCom.Visible = showStart; startCom.Enabled = showStart;
            if (showStart) startCom.BringToFront();
            initialize.Visible = !initialized && !showStart; initialize.Enabled = canInitialize && !startingCom;
            motors.Visible = initialized; motors.Enabled = rig.Enabled = timings.Enabled = initialized;
            if (showingMotors)
            {
                motors.AutoResizeRows(DataGridViewAutoSizeRowsMode.AllCells);
                fitMotorTable();
                motors.Parent.Parent.PerformLayout();
            }
            foreach (Form dialog in OwnedForms) if (dialog != statusHistoryForm) dialog.Enabled = initialized;
            if (!initialized)
            {
                foreach (ToolStripMenuItem item in new[] { leveling, levelingStop, loads, maintenance, analysis }) item.Enabled = false;
                lightOn.Enabled = lightOff.Enabled = false;
                foreach (ToolStripMenuItem item in modes) item.Enabled = false;
                foreach (ToolStripMenuItem item in senderModes) item.Enabled = false;
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
            catch (Exception e) { if (!IsDisposed) ui.ShowMessage(this, ui.F("Errors", "light_failed", "error", ui.Message(e.Message)), ui.T("Panels", enabled ? "light_on" : "light_off")); }
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
                string message = ui.F("Errors", "com_unavailable", "error", ui.Message(e.Message));
                if (message != lastConnection) { AddStatus(message, true); lastConnection = message; }
                connection.ForeColor = Color.Firebrick;
                comUnavailable = true;
                timings.SetSimHubConnected(false);
                UpdateInitializationControls(false, false);
            }
            finally { polling = false; }
        }
        public void UpdateStatus(Status status)
        {
                comUnavailable = false;
                ui.Status = status;
                timings.SetSimHubConnected(status.Connected);
                rig.RawMin = status.SimHubPositionMin; rig.RawMax = status.SimHubPositionMax;
                string summary = ui.F("Status", "connection_summary", "simulation", status.Simulated ? ui.T("Status", "simulation") : "", "connection", ui.T("Status", status.Connected ? "connected" : "disconnected"), "recording", status.Recording ? ui.T("Status", "recording") : status.GameRunning ? ui.T("Status", "recording_paused") : "");
                if (summary != lastConnection) { AddStatus(summary, true); lastConnection = summary; }
                connection.ForeColor = status.Simulated ? Color.DarkOrange : status.Connected ? Color.ForestGreen : Color.SteelBlue;
                BackColor = ColorTranslator.FromHtml(ui.Config.Ini.Get("ui", status.Connected ? "connected_background" : "disconnected_background"));
                string activity = ui.F("Status", "activity", "activity", ui.Message(status.Activity), "error", string.IsNullOrEmpty(status.LastError) ? "" : ui.F("Status", "error_suffix", "error", ui.Message(status.LastError)));
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
                foreach (ToolStripMenuItem item in modes) { item.Checked = (string)item.Tag == status.Mode; item.Enabled = ready && !status.Maintenance && !status.Analysis; }
                foreach (ToolStripMenuItem item in senderModes) { item.Checked = (string)item.Tag == (status.SenderMode ?? "oversampling"); item.Enabled = ready && !status.Maintenance && !status.Analysis; }
                foreach (AxisStatus axis in status.Axes)
                {
                    var row = motors.Rows[axis.Axis - 1];
                    row.Cells[1].Value = ui.F("Formats", "leveling_offset", "value", axis.Offset); row.Cells[2].Value = status.GameRunning ? "" : ui.F("Formats", "load_rate", "value", axis.Load);
                    row.Cells[3].Value = DriveStateView.Text(ui, axis, status);
                    string key = axis.Axis <= 3 ? axis.Axis.ToString() : "4-7";
                    double hours = status.Grease[key].playtimeMinutes / 60;
                    row.Cells[4].Value = hours >= status.GreaseAlarmHours ? ui.T("MainDialog", "grease_required") : hours >= status.GreaseWarningHours ? ui.T("MainDialog", "grease_soon_required") : ui.T("Common", "status_ok");
                    row.DefaultCellStyle.BackColor = DriveStateView.IsFault(axis) ? Color.MistyRose : axis.Axis == status.FixedAxis || DriveStateView.IsInhibited(axis) ? Color.LightGoldenrodYellow : Color.White;
                }
                rig.Status = status; rig.Invalidate();
                if (++chartTick * refresh.Interval >= ui.Config.Ini.Int("ui", "trigger_chart_refresh_ms"))
                {
                    chartTick = 0; timings.SetSamples(status.TriggerIntervals, status.SimHubIntervals);
                }
                if (maintenanceForm != null && !maintenanceForm.IsDisposed) maintenanceForm.UpdateStatus(status);
                if (analysisForm != null && !analysisForm.IsDisposed) analysisForm.UpdateStatus(status);
                UpdateInitializationControls(status.Initialized, !status.Initialized && !status.Busy);
                if (captureRequested && !status.Busy)
                {
                    captureRequested = false;
                    if (string.IsNullOrEmpty(status.LastError) && status.CalibrationLoads != null)
                    {
                        string loadText = string.Join(ui.T("Formats", "list_separator"), status.CalibrationLoads.Select((v, i) => ui.F("Formats", "load_item", "axis", i + 4, "value", v / 10)));
                        string message = ui.F("MainDialog", "save_load_success", "load_text", loadText, "front_rear", status.LoadCenter[0], "left_right", status.LoadCenter[1]);
                        ui.ShowMessage(this, message, ui.T("MainDialog", "save_load_success_title"), MessageBoxButtons.OK, MessageBoxIcon.Information);
                    }
                }
        }
        protected override void Dispose(bool disposing) { if (disposing) { refresh.Dispose(); clockRefresh.Dispose(); } base.Dispose(disposing); }
        void OnClosing(object sender, FormClosingEventArgs e)
        {
            closing = true; refresh.Stop(); clockRefresh.Stop();
            // Close local windows without issuing maintenance/analysis stop
            // commands. Com owns ongoing operations independently of this UI.
            if (maintenanceForm != null) maintenanceForm.ForceClose();
            if (analysisForm != null) analysisForm.ForceClose();
        }
    }
    public sealed class RigView : Control
    {
        readonly string[] axisLabels;
        readonly UiContext ui;
        public Status Status;
        public int RawMin, RawMax = 131071;
        public RigView(UiContext ui) { this.ui = ui; axisLabels = Enumerable.Range(1, 7).Select(a => ui.T("Analysis", "axis_" + a)).ToArray(); DoubleBuffered = true; BackColor = Color.White; }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); if (Status == null) return;
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (var title = new Font("Segoe UI", 11, FontStyle.Bold)) e.Graphics.DrawString(ui.T("MainDialog", "rig_title"), title, Brushes.SteelBlue, 16, 12);
            int y = 52;
            foreach (AxisStatus axis in Status.Axes)
            {
                int width = Math.Max(40, Width - 48);
                e.Graphics.DrawString(ui.F("Formats", "rig_axis", "axis", axisLabels[axis.Axis - 1], "value", axis.Target), Font, Brushes.DimGray, 20, y);
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
            Ini language = null;
            try
            {
                string root = Configuration.FindRoot(args, "Language.ini");
                language = new Ini(Path.Combine(root, "INI", "Language.ini"));
                var ui = new UiContext(root);
                bool created;
                using (var single = new System.Threading.Mutex(true, "Local\\" + Protocol.PipeName + ".HMI", out created))
                {
                    if (!created) throw new InvalidOperationException(ui.T("Errors", "already_running"));
                    try { Application.Run(new MainForm(ui)); }
                    finally { single.ReleaseMutex(); }
                }
                return 0;
            }
            catch (Exception e)
            {
                if (language != null) LanguageText.ShowMessage(language, null, LanguageText.Format(language, "Errors", "startup_failed", "error", LanguageText.TranslateMessage(language, e.Message)), language.Get("MainDialog", "hmi_title"), MessageBoxButtons.OK, MessageBoxIcon.Error);
                else MessageBox.Show(e.ToString(), Application.ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error); // Language.ini could not be loaded; retain OS bootstrap diagnostics.
                return 1;
            }
        }
    }
}
