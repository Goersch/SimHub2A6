using System;
using System.Drawing;
using System.Globalization;
using System.Windows.Forms;

namespace SimHub2SimRig.HMI
{
    public sealed class LogTable : DataGridView
    {
        readonly bool compact;
        int messageWidth;

        public LogTable(bool compact)
        {
            this.compact = compact;
            Dock = DockStyle.Fill; ReadOnly = true;
            AllowUserToAddRows = AllowUserToDeleteRows = AllowUserToResizeRows = false;
            RowHeadersVisible = false; BorderStyle = BorderStyle.None;
            BackgroundColor = Color.White; GridColor = Color.Gainsboro;
            SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            ClipboardCopyMode = DataGridViewClipboardCopyMode.EnableAlwaysIncludeHeaderText;
            ScrollBars = compact ? ScrollBars.None : ScrollBars.Both;
            EnableHeadersVisualStyles = false;
            ColumnHeadersDefaultCellStyle.BackColor = Color.WhiteSmoke;
            ColumnHeadersDefaultCellStyle.ForeColor = Color.FromArgb(45, 45, 45);
            DefaultCellStyle.WrapMode = DataGridViewTriState.False;
            DefaultCellStyle.Padding = new Padding(8, 0, 8, 0);
            ColumnHeadersDefaultCellStyle.Padding = new Padding(8, 0, 8, 0);
            Columns.Add("date", "Datum"); Columns.Add("time", "Uhrzeit");
            Columns.Add("process", "PROZESS"); Columns.Add("message", "Message");
            Columns[0].Width = 105; Columns[1].Width = 115; Columns[2].Width = 95;
            Columns[0].Frozen = Columns[1].Frozen = Columns[2].Frozen = true;
            foreach (DataGridViewColumn column in Columns) column.SortMode = DataGridViewColumnSortMode.NotSortable;
            Columns[3].MinimumWidth = 160;
            if (compact) Columns[3].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
            Font = new Font("Segoe UI", 9);
            SetRowSizes(); SizeChanged += (s, e) => FitMessageColumn();
        }

        protected override void OnFontChanged(EventArgs e)
        {
            base.OnFontChanged(e); SetRowSizes();
        }
        void SetRowSizes()
        {
            RowTemplate.Height = Math.Max(18, Font.Height + 3);
            ColumnHeadersHeight = Math.Max(20, Font.Height + 5);
        }
        void FitMessageColumn()
        {
            if (!compact && Columns.Count == 4)
                Columns[3].Width = Math.Min(65536, Math.Max(160, Math.Max(messageWidth, ClientSize.Width - Columns[0].Width - Columns[1].Width - Columns[2].Width - SystemInformation.VerticalScrollBarWidth)));
        }
        public void AddMessage(string output)
        {
            string[] original = output.Replace("\r", "").Split('\n');
            string text = original[0], process = "", date = "", time = "";
            if (text.StartsWith("[", StringComparison.Ordinal))
            {
                int end = text.IndexOf(']');
                if (end > 0) { process = text.Substring(1, end - 1); text = text.Substring(end + 1).TrimStart(); }
            }
            DateTime stamp; int length = 0;
            if (text.Length >= 23 && DateTime.TryParseExact(text.Substring(0, 23), "yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture, DateTimeStyles.None, out stamp)) length = 23;
            else if (text.Length >= 19 && DateTime.TryParseExact(text.Substring(0, 19), "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.None, out stamp)) length = 19;
            else stamp = DateTime.MinValue;
            if (length != 0) { date = stamp.ToString("dd.MM.yyyy"); time = stamp.ToString("HH:mm:ss.fff"); text = text.Substring(length).TrimStart(); }
            for (int i = 0; i < (compact ? 1 : original.Length); i++)
            {
                string message = i == 0 ? text : original[i];
                int row = Rows.Add(date, time, process, message);
                Rows[row].Tag = compact ? output : original[i];
                if (!compact) messageWidth = Math.Max(messageWidth, TextRenderer.MeasureText(message, Font).Width + 24);
            }
            FitMessageColumn();
        }
        public bool AtEnd
        {
            get { return Rows.Count == 0 || (Rows[Rows.Count - 1].Displayed && GetRowDisplayRectangle(Rows.Count - 1, false).Bottom <= ClientSize.Height); }
        }
        public void ScrollToEnd()
        {
            if (Rows.Count > 0) FirstDisplayedScrollingRowIndex = Math.Max(0, Rows.Count - Math.Max(1, DisplayedRowCount(false)));
        }
    }
}
