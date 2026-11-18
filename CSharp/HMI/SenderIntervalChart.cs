using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using System.Windows.Forms.DataVisualization.Charting;

namespace SimHub2SimRig.HMI
{
    public sealed class SenderIntervalChart : Chart
    {
        readonly double windowSeconds;
        bool simHubSelected = true, simHubConnected;

        public SenderIntervalChart(double windowSeconds, UiContext ui = null)
        {
            var language = ui == null ? LanguageText.LoadDefault() : ui.Language;
            if (windowSeconds <= 0 || double.IsNaN(windowSeconds) || double.IsInfinity(windowSeconds))
                throw new ArgumentOutOfRangeException("windowSeconds");
            this.windowSeconds = windowSeconds;
            Name = "senderIntervalChart";
            Dock = DockStyle.Fill;
            BackColor = Color.White;
            MinimumSize = new Size(200, 120);
            var area = new ChartArea("main");
            area.AxisX.Title = language.Get("Charts", "sender_time_axis");
            area.AxisX.Minimum = -windowSeconds;
            area.AxisX.Maximum = 0;
            area.AxisX.Interval = windowSeconds / 6;
            area.AxisX.IsMarginVisible = false;
            area.AxisX.Enabled = AxisEnabled.True;
            area.AxisX.LabelStyle.Format = language.Get("Formats", "chart_decimal");
            area.AxisY.Title = language.Get("Common", "unit_ms");
            area.AxisY.Minimum = 0;
            area.AxisY.Maximum = 100;
            area.AxisY.Interval = 20;
            area.AxisY.Enabled = AxisEnabled.True;
            area.AxisY.LabelStyle.Format = language.Get("Formats", "chart_integer");
            area.AxisX.MajorGrid.LineColor = area.AxisY.MajorGrid.LineColor = Color.Gainsboro;
            ChartAreas.Add(area);
            Series.Add(new Series("ModBus") { LegendText = language.Get("Panels", "chart_modbus"), ChartType = SeriesChartType.FastLine, Color = Color.SteelBlue });
            Series.Add(new Series("SimHub") { LegendText = language.Get("Panels", "chart_simhub"), ChartType = SeriesChartType.FastLine, Color = Color.DarkOrange, Enabled = false });
        }

        public void SetVisibleSources(bool simHub, bool modBus)
        {
            simHubSelected = simHub;
            Series["SimHub"].Enabled = simHubSelected && simHubConnected;
            Series["ModBus"].Enabled = modBus;
            Invalidate();
        }

        public void SetSimHubConnected(bool connected)
        {
            if (simHubConnected == connected) return;
            simHubConnected = connected;
            Series["SimHub"].Enabled = simHubSelected && simHubConnected;
            Invalidate();
        }

        public void SetSamples(double[] modBusIntervalsMs, double[] simHubIntervalsMs = null)
        {
            SetPoints(Series["ModBus"], modBusIntervalsMs);
            SetPoints(Series["SimHub"], simHubIntervalsMs);
            Invalidate();
        }

        void SetPoints(Series series, double[] intervalsMs)
        {
            // Anchor the newest measurement at zero. Both axis limits and tick
            // spacing stay fixed while the rolling history grows or drains.
            // This avoids the old automatic rescaling on every refresh.
            var points = new List<DataPoint>();
            double x = 0;
            if (intervalsMs != null)
                for (int i = intervalsMs.Length - 1; i >= 0; i--)
                {
                    double interval = intervalsMs[i];
                    if (interval < 0 || double.IsNaN(interval) || double.IsInfinity(interval)) continue;
                    if (x < -windowSeconds) break;
                    points.Add(new DataPoint(x, interval));
                    x -= interval / 1000;
                }
            points.Reverse();
            var target = series.Points;
            target.SuspendUpdates();
            try
            {
                target.Clear();
                foreach (DataPoint point in points) target.Add(point);
            }
            finally { target.ResumeUpdates(); }
        }
    }
}
