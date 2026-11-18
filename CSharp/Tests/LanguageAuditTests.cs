using System;
using System.Diagnostics;
using System.IO;

namespace SimHub2SimRig.Tests
{
    public static partial class TestProgram
    {
        static void HmiLanguageAudit()
        {
            string fixture = Path.Combine(Prepare(root), "language-audit");
            Directory.CreateDirectory(Path.Combine(fixture, "INI"));
            Directory.CreateDirectory(Path.Combine(fixture, "CSharp", "HMI"));
            string language = Path.Combine(fixture, "INI", "Language.ini");
            string source = Path.Combine(fixture, "CSharp", "HMI", "Example.cs");
            File.WriteAllText(language, "[Common]\nlabel = Translated label\n");
            Func<bool> audit = () => {
                using (var process = Process.Start(new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(root, "CSharp", "Tests", "check_hmi_language.ps1") + "\" -RepositoryRoot \"" + fixture + "\"")
                { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }))
                {
                    var output = process.StandardOutput.ReadToEndAsync();
                    var error = process.StandardError.ReadToEndAsync();
                    if (!process.WaitForExit(10000)) { process.Kill(); throw new TimeoutException("Language audit did not finish"); }
                    System.Threading.Tasks.Task.WaitAll(output, error);
                    return process.ExitCode == 0;
                }
            };
            File.WriteAllText(source, "var label = new Label { Text = ui.T(\"Common\", \"label\") }; // Text = \"Comment only\"\n/* MenuAction(\"Comment only\") */\nvar raw = new Request { Operation = \"status\" };\nui.T(\"Analysis\", \"axis_\" + axis);\n");
            Check(audit(), "Localized controls, comments, protocol values and dynamic keys pass the build guard");
            File.AppendAllText(source, "var menu = new ToolStripMenuItem(\"Hard-coded caption\");\n");
            Check(!audit(), "The build guard rejects a new untranslated menu caption");
            File.WriteAllText(source, "label.Text = ui.T(\"Common\", \"missing\");\n");
            Check(!audit(), "The build guard rejects missing language keys");
            File.WriteAllText(source, "label.Text = ui.T(\"Common\", \"label\");\n");
            File.AppendAllText(language, "label = Duplicate entry\n");
            Check(!audit(), "The build guard rejects duplicate language entries");
            File.WriteAllText(language, "[Common]\nlabel = Translated label\n[MessagePatterns]\nmessage = ^Error (?<detail>.*)$\n[MessageTexts]\nmessage = Fehler {missing}\n");
            Check(!audit(), "The build guard rejects diagnostic placeholders without matching captures");
        }
    }
}
