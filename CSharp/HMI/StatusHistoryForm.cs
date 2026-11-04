using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace SimHub2SimRig.HMI
{
    public sealed class StatusHistoryForm : Form
    {
        readonly LogTable messages = new LogTable(false);

        public StatusHistoryForm(IEnumerable<string> history)
        {
            Text = "Meldungen seit HMI-Start";
            Size = new Size(1100, 500); MinimumSize = new Size(650, 300);
            StartPosition = FormStartPosition.CenterParent; ShowInTaskbar = false;
            Padding = new Padding(8);
            messages.Name = "fullStatusHistory"; messages.Dock = DockStyle.Fill;
            Controls.Add(messages);
            messages.SuspendLayout();
            try { foreach (string message in history) AddMessage(message); }
            finally { messages.ResumeLayout(); }
            Shown += (s, e) => ScrollToEnd();
        }

        public void AddMessage(string message)
        {
            // Follow live output only when the user is already at the end.
            // Scrolling back to read older messages keeps that position.
            bool follow = messages.AtEnd;
            messages.AddMessage(message);
            if (follow) ScrollToEnd();
        }

        void ScrollToEnd()
        {
            messages.ScrollToEnd();
        }
    }
}
