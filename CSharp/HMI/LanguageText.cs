using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Runtime.CompilerServices;
using System.Windows.Forms;
using System.Drawing;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.HMI
{
    public static class LanguageText
    {
        sealed class MessageRule
        {
            public Regex Pattern;
            public string Template;
            public string[] Groups;
        }
        static readonly ConditionalWeakTable<Ini, MessageRule[]> rules = new ConditionalWeakTable<Ini, MessageRule[]>();
        public static Ini LoadDefault()
        {
            return new Ini(Path.Combine(Configuration.FindRoot(new string[0], "Language.ini"), "INI", "Language.ini"));
        }

        public static string Format(Ini language, string section, string key, params object[] values)
        {
            var replacements = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < values.Length; i += 2) replacements.Add((string)values[i], values[i + 1]);
            return FormatTemplate(language.Get(section, key), replacements);
        }

        static string FormatTemplate(string template, IDictionary<string, object> values)
        {
            return Regex.Replace(template, @"\{(?<name>\w+)(?::(?<format>[^}]+))?\}", match => {
                object value;
                if (!values.TryGetValue(match.Groups["name"].Value, out value)) throw new FormatException(match.Value);
                string format = match.Groups["format"].Value;
                if (Regex.IsMatch(format, @"^\.\d+f$")) format = "F" + format.Substring(1, format.Length - 2);
                else if (Regex.IsMatch(format, @"^0\d+d$")) format = "D" + int.Parse(format.Substring(1, format.Length - 2), CultureInfo.InvariantCulture);
                var formatted = value as IFormattable;
                return formatted == null ? Convert.ToString(value, CultureInfo.CurrentCulture) : formatted.ToString(format.Length == 0 ? null : format, CultureInfo.CurrentCulture);
            });
        }

        public static string TranslateMessage(Ini language, string message)
        {
            return TranslateMessage(language, message, 0);
        }

        static string TranslateMessage(Ini language, string message, int depth)
        {
            if (string.IsNullOrEmpty(message) || depth >= 8 || !language.Has("MessagePatterns")) return message;
            foreach (MessageRule rule in rules.GetValue(language, ini => ini.Keys("MessagePatterns").Select(key => {
                var pattern = new Regex(ini.Get("MessagePatterns", key), RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));
                return new MessageRule { Pattern = pattern, Template = ini.Get("MessageTexts", key), Groups = pattern.GetGroupNames().Where(g => g != "0").ToArray() };
            }).ToArray()))
            {
                Match match = rule.Pattern.Match(message);
                if (!match.Success) continue;
                var values = new Dictionary<string, object>();
                foreach (string group in rule.Groups)
                    values[group] = new[] { "error", "message", "action", "stage", "result" }.Contains(group)
                        ? TranslateMessage(language, match.Groups[group].Value, depth + 1) : match.Groups[group].Value;
                return FormatTemplate(rule.Template, values);
            }
            return message; // External device/OS details and data remain inspectable.
        }

        public static DialogResult ShowMessage(UiContext ui, IWin32Window owner, string text, string title, MessageBoxButtons buttons, MessageBoxIcon icon)
        {
            return ShowMessage(ui.Language, owner, text, title, buttons, icon);
        }

        public static DialogResult ShowMessage(Ini language, IWin32Window owner, string text, string title, MessageBoxButtons buttons, MessageBoxIcon icon)
        {
            using (var dialog = new Form { Text = title, Font = SystemFonts.MessageBoxFont, StartPosition = FormStartPosition.CenterParent, FormBorderStyle = FormBorderStyle.FixedDialog, MinimizeBox = false, MaximizeBox = false, ShowInTaskbar = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Padding = new Padding(16), MinimumSize = new Size(340, 0) })
            using (var iconImage = (icon == MessageBoxIcon.Error ? SystemIcons.Error : icon == MessageBoxIcon.Warning ? SystemIcons.Warning : SystemIcons.Information).ToBitmap())
            {
                var layout = new TableLayoutPanel { AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, ColumnCount = 2, RowCount = 2, Dock = DockStyle.Fill };
                layout.Controls.Add(new PictureBox { Image = iconImage, Size = new Size(40, 40), Margin = new Padding(0, 0, 14, 10) }, 0, 0);
                layout.Controls.Add(new Label { Text = text, AutoSize = true, MaximumSize = new Size(720, 0), Margin = new Padding(0, 0, 0, 16) }, 1, 0);
                var actions = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill };
                if (buttons == MessageBoxButtons.OKCancel)
                {
                    var cancel = new Button { Text = language.Get("Common", "button_cancel"), DialogResult = DialogResult.Cancel, AutoSize = true, MinimumSize = new Size(90, 28) };
                    actions.Controls.Add(cancel); dialog.CancelButton = cancel;
                }
                var ok = new Button { Text = language.Get("Common", "button_ok"), DialogResult = DialogResult.OK, AutoSize = true, MinimumSize = new Size(90, 28) };
                actions.Controls.Add(ok); dialog.AcceptButton = ok;
                if (dialog.CancelButton == null) dialog.CancelButton = ok;
                layout.Controls.Add(actions, 0, 1); layout.SetColumnSpan(actions, 2); dialog.Controls.Add(layout);
                return dialog.ShowDialog(owner);
            }
        }
    }
}
