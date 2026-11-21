using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Windows.Forms.DataVisualization.Charting;
using SimHub2SimRig.Com;
using SimHub2SimRig.HMI;
using SimHub2SimRig.Shared;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static void HmiLanguagePresentation()
        {
            string isolated = Prepare(root), path = Path.Combine(isolated, "INI", "Language.ini");
            string languageText = File.ReadAllText(path);
            var original = new Ini(path);
            Action<string, string, string> change = (section, key, value) => {
                string pattern = @"(?ms)(^\[" + Regex.Escape(section) + @"\]\r?\n)(.*?)(?=^\[|\z)";
                languageText = Regex.Replace(languageText, pattern, match => match.Groups[1].Value + Regex.Replace(match.Groups[2].Value, @"(?m)^" + Regex.Escape(key) + @"\s*=.*$", key + " = " + value));
            };
            change("MainDialog", "hmi_title", "Parametrierte Hauptanzeige");
            change("MainDialog", "initialize", "Initialisierung anfordern");
            change("MainDialog", "start_com", "Kommunikation starten");
            change("MainDialog", "drive_fault", "Antriebsfehler aus Sprachdatei");
            change("Panels", "mode", "Betriebsarten aus Sprachdatei");
            change("Panels", "sender_simhub_cycle", "Senden bei jedem neuen SimHub-Datensatz");
            change("Panels", "chart_both", "Beide Messreihen aus Sprachdatei");
            change("Common", "unit_ms", "Millisekunden");
            change("Common", "button_ok", "Bestaetigen");
            change("Common", "button_cancel", "Abbrechen");
            change("History", "title", "Parametrierte Meldungen");
            change("History", "date", "Datum aus Sprachdatei");
            change("Formats", "log_date", "yyyy'/'MM'/'dd");
            change("Formats", "log_time", "HH-mm-ss");
            change("Formats", "leveling_offset", "Offset {value:.1f} Millimeter");
            change("Formats", "playtime", "{hours:02d} Stunden {minutes:02d} Minuten");
            change("Analysis", "playback_playing", "Wiedergabe laeuft");
            change("Analysis", "playback_status", "{state}: {file} bei {seconds:.1f} Sekunden");
            for (int axis = 1; axis <= 7; axis++) change("Analysis", "axis_" + axis, "Identische Achsenbezeichnung");
            foreach (string key in new[] { "signal_target", "signal_calculated_actual", "signal_actual", "signal_ground_deviation" }) change("Analysis", key, "Identische Signalbezeichnung");
            string initializationKey = original.Keys("MessageTexts").Single(key => original.Get("MessagePatterns", key) == "^Initialization$");
            change("MessageTexts", initializationKey, "Initialisierung aus Sprachdatei");
            string crcKey = original.Keys("MessageTexts").Single(key => original.Get("MessageTexts", key) == "Modbus CRC error");
            change("MessageTexts", crcKey, "CRC-Pruefung fehlgeschlagen");
            change("MessageTexts", "telemetry", "Achse {axis}: {error}");
            change("MessageTexts", "invalid_a6_init", "Ungueltiger Treiberparameter aus Sprachdatei: {setting}");
            change("MessageTexts", "nonnegative_value", "Wartezeit muss mindestens null sein: {setting}");
            File.WriteAllText(path, languageText);
            var ui = new UiContext(isolated, "SimHub2SimRig.LanguageTest." + Guid.NewGuid().ToString("N"));
            string screenshots = Path.Combine(root, "CSharp", "Tests", "screenshots");
            Directory.CreateDirectory(screenshots);
            Check(ui.Language.Keys("MessagePatterns").OrderBy(k => k).SequenceEqual(ui.Language.Keys("MessageTexts").OrderBy(k => k)), "Every diagnostic pattern has exactly one configurable template");
            foreach (string key in ui.Language.Keys("MessagePatterns"))
            {
                var regex = new Regex(ui.Language.Get("MessagePatterns", key));
                string[] groups = regex.GetGroupNames();
                foreach (Match placeholder in Regex.Matches(ui.Language.Get("MessageTexts", key), @"\{(\w+)(?::[^}]+)?\}")) Check(groups.Contains(placeholder.Groups[1].Value), "Diagnostic template binds a captured field: " + key);
            }
            Equal("Offset " + 1.25.ToString("F1") + " Millimeter", ui.F("Formats", "leveling_offset", "value", 1.25), "Python-style precision is configurable");
            Equal("02 Stunden 07 Minuten", ui.F("Formats", "playtime", "hours", 2, "minutes", 7), "Named integer widths are configurable");
            Equal("Initialisierung aus Sprachdatei", ui.Message("Initialization"), "Activity values use configurable presentation");
            Equal("Achse 4: Initialisierung aus Sprachdatei", ui.Message("Telemetry axis 4: Initialization"), "Nested diagnostic contexts are translated");
            Equal("COM port Initialization opened successfully", ui.Message("COM port Initialization opened successfully"), "Captured port data is never translated");
            Equal("System.InvalidOperationException: Initialisierung aus Sprachdatei", ui.Message("System.InvalidOperationException: Initialization"), "CLR exception prefixes retain the localized error");
            Equal("SimHub2SimRig.Com.CrcException: CRC-Pruefung fehlgeschlagen", ui.Message("SimHub2SimRig.Com.CrcException: Modbus CRC error"), "Application exception prefixes stay raw while the message is translated");
            Equal("External USB detail 42", ui.Message("External USB detail 42"), "Unknown external diagnostics remain available");
            Equal("Ungueltiger Treiberparameter aus Sprachdatei: a6_init.c03_02", ui.Message("Invalid A6 initialization parameter: a6_init.c03_02"), "INIT validation is configurable while the setting identifier stays intact");
            Equal("Wartezeit muss mindestens null sein: a6_init.save_delay_s", ui.Message("Must be nonnegative: a6_init.save_delay_s"), "INIT wait validation comes from Language.ini");
            Equal("System.IO.InvalidDataException: Ungueltiger Treiberparameter aus Sprachdatei: a6_init.c04_01", ui.Message("System.IO.InvalidDataException: Invalid A6 initialization parameter: a6_init.c04_01"), "Nested INIT validation retains the technical exception type");
            Status status;
            using (var engine = new Engine(new Configuration(isolated), true)) status = engine.Snapshot();
            status.Initialized = true; status.Playback = "stopped"; status.Mode = "simhub";
            using (var main = new MainForm(ui))
            {
                main.UpdateStatus(status);
                Equal("Parametrierte Hauptanzeige", main.Text, "Window title comes from Language.ini");
                Equal("Initialisierung anfordern", Descendants(main).OfType<Button>().Single(b => b.Name == "initialize").Text, "INIT label is configurable");
                Equal("Kommunikation starten", Descendants(main).OfType<Button>().Single(b => b.Name == "startCom").Text, "START label is configurable");
                var mode = main.MainMenuStrip.Items.OfType<ToolStripMenuItem>().Single(m => m.Name == "modeMenu");
                Equal("Betriebsarten aus Sprachdatei", mode.Text, "Menu labels use stable technical names");
                var senderItems = mode.DropDownItems.OfType<ToolStripMenuItem>().Where(item => item.Name.StartsWith("senderMode_")).ToArray();
                Equal("Senden bei jedem neuen SimHub-Datensatz", senderItems.Single(item => (string)item.Tag == "simhub_cycle").Text, "Sender mode menu text is configurable");
                Check(!mode.DropDownItems.OfType<ToolStripControlHost>().Any(), "Sender selection uses the same menu-item style as the motion modes");
                Equal(2, mode.DropDownItems.OfType<ToolStripMenuItem>().Count(item => item.Checked), "One motion mode and one independent sender mode are checked");
                var both = Descendants(main).OfType<RadioButton>().Single(r => (string)r.Tag == "Both");
                Check(both.Checked, "Both charts remain selected with a translated caption");
                Equal("Beide Messreihen aus Sprachdatei", both.Text, "Chart selection uses Language.ini");
                main.StartPosition = FormStartPosition.Manual; main.Location = new Point(-30000, -30000); main.ShowInTaskbar = false; main.Opacity = 0; main.Show();
                mode.DropDown.Opacity = 0; mode.ShowDropDown();
                foreach (ToolStripMenuItem item in senderItems)
                {
                    Check(mode.DropDown.ClientRectangle.Contains(item.Bounds), "Translated sender menu item stays inside the popup");
                    Check(item.Width >= TextRenderer.MeasureText(item.Text, item.Font).Width, "Translated sender caption has enough width");
                }
                using (var bitmap = new Bitmap(mode.DropDown.Width, mode.DropDown.Height))
                {
                    mode.DropDown.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                    bitmap.Save(Path.Combine(screenshots, "language-mode.png"));
                }
                mode.HideDropDown();
            }
            using (var table = new LogTable(false, ui))
            {
                table.AddMessage("[COM] 2026-11-18 12:34:56.789 Initialization");
                Equal("Datum aus Sprachdatei", table.Columns[0].HeaderText, "History headers are configurable");
                Equal("2026/11/18", Convert.ToString(table.Rows[0].Cells[0].Value), "History date format is configurable");
                Equal("12-34-56", Convert.ToString(table.Rows[0].Cells[1].Value), "History time format is configurable");
                Equal("Initialisierung aus Sprachdatei", Convert.ToString(table.Rows[0].Cells[3].Value), "History translates presentation only");
                Equal("[COM] 2026-11-18 12:34:56.789 Initialization", (string)table.Rows[0].Tag, "Raw log entry stays intact");
                const string initializationError = "[COM] 2026-11-18 12:34:57.123 Invalid A6 initialization parameter: a6_init.c03_02";
                table.AddMessage(initializationError);
                Equal("Ungueltiger Treiberparameter aus Sprachdatei: a6_init.c03_02", Convert.ToString(table.Rows[1].Cells[3].Value), "Dynamic INIT diagnostics use the configured language template");
                Equal(initializationError, (string)table.Rows[1].Tag, "INIT diagnostic localization preserves the original log record");
            }
            using (var chart = new SenderIntervalChart(60, ui)) Equal("Millisekunden", chart.ChartAreas[0].AxisY.Title, "Chart units are configurable");
            using (var history = new StatusHistoryForm(new string[0], ui)) Equal("Parametrierte Meldungen", history.Text, "History title is configurable");
            using (var analysis = new AnalysisForm(ui))
            {
                foreach (CheckBox signal in Descendants(analysis).OfType<CheckBox>()) signal.Checked = true;
                analysis.SetPreview(new[] { new Sample { Target = new double[7], Calculated = new double[7], Actual = new double[7], Ground = new double[7] } });
                Equal(25, Descendants(analysis).OfType<Chart>().Single().Series.Count, "Identical translated legend captions do not collide with series IDs");
                status.Analysis = true; status.Playback = "playing"; status.PlaybackFile = "Initialization.csv"; status.PlaybackSeconds = 1.25;
                analysis.UpdateStatus(status);
                Check(Descendants(analysis).OfType<Label>().Any(label => label.Text == "Wiedergabe laeuft: Initialization.csv bei " + 1.25.ToString("F1") + " Sekunden"), "Dynamic playback state and time are configurable while file data stays intact");
            }
            using (var timer = new Timer { Interval = 20 })
            {
                Exception dialogFailure = null;
                timer.Tick += (s, e) => {
                    var dialog = Application.OpenForms.Cast<Form>().SingleOrDefault(f => f.Text == "Language dialog test");
                    if (dialog == null) return;
                    timer.Stop();
                    try
                    {
                        var buttons = Descendants(dialog).OfType<Button>().ToArray();
                        Check(buttons.Any(b => b.Text == "Bestaetigen") && buttons.Any(b => b.Text == "Abbrechen"), "Standard dialog button captions come from Language.ini");
                        using (var bitmap = new Bitmap(dialog.Width, dialog.Height))
                        {
                            dialog.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                            bitmap.Save(Path.Combine(screenshots, "language-dialog.png"));
                        }
                        buttons.Single(b => b.DialogResult == DialogResult.Cancel).PerformClick();
                    }
                    catch (Exception failure) { dialogFailure = failure; dialog.DialogResult = DialogResult.Cancel; dialog.Close(); }
                };
                timer.Start();
                Equal(DialogResult.Cancel, ui.ShowMessage(null, "Language dialog body", "Language dialog test", MessageBoxButtons.OKCancel, MessageBoxIcon.Information), "Localized dialog preserves its result");
                if (dialogFailure != null) throw dialogFailure;
            }
        }
    }
}
