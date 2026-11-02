using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public sealed class MaintenanceForm : Form
    {
        readonly UiContext ui;
        readonly List<Tuple<Button, int, int, bool>> actions = new List<Tuple<Button, int, int, bool>>();
        readonly Button[] start = new Button[4], stop = new Button[4], reset = new Button[4];
        readonly Label[] last = new Label[4], runtime = new Label[4], homed = new Label[4];
        readonly Label status = new Label();
        bool force, closing;
        public MaintenanceForm(UiContext ui)
        {
            this.ui = ui; Text = ui.T("Maintenance", "title"); Font = new Font("Segoe UI", 9); Size = new Size(1040, 600); MinimumSize = new Size(800, 560); StartPosition = FormStartPosition.CenterParent;
            var table = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 11, Padding = new Padding(18) };
            Controls.Add(table); for (int i = 0; i < 4; i++) table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
            string[] groupKeys = { "axis_front", "axis_middle", "axis_rear", "axis_hub" };
            string[][] keys = {
                new[] { "action_right", "action_center", "action_left", "action_home" },
                new[] { "action_front_position", "action_center", "action_rear_position", "action_home" },
                new[] { "action_right", "action_center", "action_left", "action_home" },
                new[] { "action_top_position", "action_center", "action_bottom_position", "action_home" }
            };
            for (int group = 0; group < 4; group++)
            {
                int first = group + 1, end = group == 3 ? 7 : first, g = group;
                table.Controls.Add(new Label { Text = ui.T("Common", groupKeys[group]), AutoSize = true, Font = new Font(Font, FontStyle.Bold) }, group, 0);
                for (int row = 0; row < 4; row++)
                {
                    bool home = row == 3; int position = row == 0 ? ui.Config.RawMax : row == 1 ? ui.Config.RawMax / 2 : 0;
                    var button = UiContext.Button(ui.T("Maintenance", keys[group][row]), async () => await ui.Command(this, new Request { Operation = home ? "home" : "move", FirstAxis = first, LastAxis = end, Position = position }));
                    button.Dock = DockStyle.Fill; table.Controls.Add(button, group, row + 1); actions.Add(Tuple.Create(button, first, end, home));
                }
                homed[group] = new Label { AutoSize = true, Margin = new Padding(4, 10, 4, 10) }; table.Controls.Add(homed[group], group, 5);
                start[group] = UiContext.Button(ui.T("Panels", "grease_start"), async () => await ui.Command(this, new Request { Operation = "grease.start", FirstAxis = first, LastAxis = end }));
                stop[group] = UiContext.Button(ui.T("Panels", "grease_stop"), async () => await ui.Command(this, new Request { Operation = "grease.stop" }));
                reset[group] = UiContext.Button(ui.T("Panels", "grease_reset"), async () => await ui.Command(this, new Request { Operation = "grease.reset", FirstAxis = first, LastAxis = end }));
                var buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, FlowDirection = FlowDirection.TopDown }; buttons.Controls.AddRange(new Control[] { start[g], stop[g], reset[g] });
                var greaseBox = new GroupBox { Text = ui.T("Panels", "grease_title"), MinimumSize = new Size(0, 168), Dock = DockStyle.Fill, Padding = new Padding(4, 18, 4, 4) }; greaseBox.Controls.Add(buttons); table.Controls.Add(greaseBox, group, 6);
                last[group] = new Label { AutoSize = true }; runtime[group] = new Label { AutoSize = true }; table.Controls.Add(last[group], group, 7); table.Controls.Add(runtime[group], group, 8);
            }
            status.AutoSize = true; status.MaximumSize = new Size(950, 0); status.Margin = new Padding(4, 16, 4, 4); table.Controls.Add(status, 0, 9); table.SetColumnSpan(status, 4);
            FormClosing += async (s, e) => {
                if (force) return; e.Cancel = true; if (closing) return; closing = true;
                if (await ui.Command(this, new Request { Operation = "maintenance.close" })) { force = true; Close(); }
                closing = false;
            };
            if (ui.Status != null) UpdateStatus(ui.Status);
        }
        public void ForceClose() { force = true; Close(); }
        public void UpdateStatus(Status s)
        {
            bool ready = s.Initialized && s.Maintenance && !s.Busy && !s.Connected;
            foreach (var item in actions) item.Item1.Enabled = ready && (item.Item4 || s.Axes.Where(a => a.Axis >= item.Item2 && a.Axis <= item.Item3).All(a => a.Enabled && a.Homed));
            for (int g = 0; g < 4; g++)
            {
                int first = g + 1, end = g == 3 ? 7 : first;
                bool referenced = s.Axes.Where(a => a.Axis >= first && a.Axis <= end).All(a => a.Enabled && a.Homed);
                homed[g].Text = ui.T("Maintenance", referenced ? "homing_done" : "not_homed"); homed[g].ForeColor = referenced ? Color.ForestGreen : Color.Firebrick;
                start[g].Enabled = ready && referenced; stop[g].Enabled = (s.Greasing || s.Activity == "Greasing") && first == s.GreaseFirst; reset[g].Enabled = ready;
                GreaseEntry entry = s.Grease[g == 3 ? "4-7" : first.ToString()]; DateTime date;
                last[g].Text = ui.T("Panels", "grease_last") + ": " + (DateTime.TryParse(entry.lastGreaseAt, out date) ? date.ToString("dd.MM.yyyy HH:mm") : "—");
                var elapsed = TimeSpan.FromMinutes(Math.Max(0, entry.playtimeMinutes)); runtime[g].Text = ui.T("Panels", "grease_playtime") + ": " + ((int)elapsed.TotalHours).ToString("00") + ":" + elapsed.Minutes.ToString("00");
                runtime[g].BackColor = elapsed.TotalHours >= ui.Config.Ini.Number("grease", "alarm_after_operating_hours") ? Color.LightCoral : elapsed.TotalHours >= ui.Config.Ini.Number("grease", "warning_after_operating_hours") ? Color.Khaki : Color.Transparent;
            }
            status.Text = s.Connected ? ui.T("Maintenance", "simhub_locked") : s.Activity + "  " + s.LastError;
        }
    }
}
