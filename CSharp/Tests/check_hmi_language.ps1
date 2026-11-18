param([string]$RepositoryRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$ErrorActionPreference = 'Stop'

# No Python/NuGet dependency: this also runs in the Framework-only release build.
# This is a regression guard for common text sinks, complemented by the
# hmi-language agent's review of dynamic text and backend presentation.
$catalogPath = Join-Path $RepositoryRoot 'INI/Language.ini'
$catalog = @{}
$section = ''
$problems = New-Object 'System.Collections.Generic.List[string]'
foreach ($line in [System.IO.File]::ReadAllLines($catalogPath)) {
    $value = $line.Trim()
    if (-not $value -or $value.StartsWith(';') -or $value.StartsWith('#')) { continue }
    if ($value -match '^\[(.+)\]$') {
        $section = $Matches[1]
        if ($catalog.ContainsKey($section)) { $problems.Add("Duplicate language section: $section") }
        else { $catalog[$section] = @{} }
    } else {
        $separator = $value.IndexOf('=')
        if ($separator -lt 0 -or -not $catalog.ContainsKey($section)) { $problems.Add("Invalid language entry: $value"); continue }
        $key = $value.Substring(0, $separator).Trim()
        if ($catalog[$section].ContainsKey($key)) { $problems.Add("Duplicate language key: $section.$key") }
        else { $catalog[$section][$key] = $value.Substring($separator + 1).Trim() }
    }
}

if ($catalog.ContainsKey('MessagePatterns') -or $catalog.ContainsKey('MessageTexts')) {
    foreach ($ruleSection in @('MessagePatterns', 'MessageTexts')) {
        if (-not $catalog.ContainsKey($ruleSection)) { $problems.Add("Missing language section: $ruleSection") }
    }
    if ($catalog.ContainsKey('MessagePatterns') -and $catalog.ContainsKey('MessageTexts')) {
        foreach ($key in $catalog['MessagePatterns'].Keys) {
            if (-not $catalog['MessageTexts'].ContainsKey($key)) { $problems.Add("Missing message template: $key"); continue }
            try {
                $pattern = New-Object System.Text.RegularExpressions.Regex -ArgumentList @($catalog['MessagePatterns'][$key], [System.Text.RegularExpressions.RegexOptions]::CultureInvariant, [TimeSpan]::FromMilliseconds(100))
                foreach ($placeholder in [regex]::Matches($catalog['MessageTexts'][$key], '\{(?<name>\w+)(?::[^}]+)?\}')) {
                    if ($pattern.GetGroupNames() -notcontains $placeholder.Groups['name'].Value) { $problems.Add("Unknown message placeholder in ${key}: $($placeholder.Value)") }
                }
            } catch { $problems.Add("Invalid message pattern ${key}: $($_.Exception.Message)") }
        }
        foreach ($key in $catalog['MessageTexts'].Keys) {
            if (-not $catalog['MessagePatterns'].ContainsKey($key)) { $problems.Add("Missing message pattern: $key") }
        }
    }
}

# Keep strings and positions intact while masking comments and character
# literals, so commented-out code cannot satisfy or fail a language check.
$tokens = [regex]'(?s)//[^\r\n]*|/\*.*?\*/|@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|''(?:\\.|[^''\\])'''
$quoted = '"((?:\\.|[^"\\])*)"'
$references = [regex]('(?<![\w])(?:T|F|[lL]anguage\.Get)\s*\(\s*' + $quoted + '\s*,\s*' + $quoted + '\s*(?=[,)])')
$formatReferences = [regex]('LanguageText\.Format\s*\(\s*\w+\s*,\s*' + $quoted + '\s*,\s*' + $quoted + '\s*(?=[,)])')
$sinks = @(
    # Literal assignment to a visible property (empty text is intentional).
    ('\b(?:Text|HeaderText|LegendText|Title|ToolTipText)\s*=\s*' + $quoted),
    # Visible first arguments.
    ('\b(?:Button|MenuAction|DrawString|AddStatus|Titles\.Add)\s*\(\s*' + $quoted),
    ('new\s+ToolStripMenuItem\s*\(\s*' + $quoted),
    # Visible messages, table headers and tooltips following a control/id.
    ('MessageBox\.Show\s*\(\s*(?:this\s*,\s*)?' + $quoted),
    ('\b(?:Columns\.Add|SetToolTip)\s*\(\s*' + $quoted + '\s*,\s*' + $quoted),
    ('\bItems\.Add\s*\(\s*' + $quoted),
    ('UiContext\.Chart\s*\(\s*' + $quoted),
    ('UiContext\.Chart\s*\(\s*' + $quoted + '\s*,\s*' + $quoted),
    ('throw\s+new\s+(?:InvalidOperationException|TimeoutException|FileNotFoundException|IOException|FormatException)\s*\(\s*' + $quoted)
)
$checkedReferences = 0
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'CSharp/HMI') -Filter '*.cs' -Recurse) {
    $source = [System.IO.File]::ReadAllText($file.FullName)
    $code = $tokens.Replace($source, [System.Text.RegularExpressions.MatchEvaluator]{ param($match)
        if ($match.Value.StartsWith('//') -or $match.Value.StartsWith('/*') -or $match.Value.StartsWith("'")) {
            return [regex]::Replace($match.Value, '[^\r\n]', ' ')
        }
        return $match.Value
    })
    foreach ($referencePattern in @($references, $formatReferences)) {
        foreach ($reference in $referencePattern.Matches($code)) {
            $checkedReferences++
            $s = $reference.Groups[1].Value; $k = $reference.Groups[2].Value
            $lineNumber = 1 + ([regex]::Matches($code.Substring(0, $reference.Index), '\n')).Count
            if (-not $catalog.ContainsKey($s) -or -not $catalog[$s].ContainsKey($k)) {
                $problems.Add("$($file.Name):${lineNumber}: Missing Language.ini key [$s] $k")
            }
        }
    }
    foreach ($sinkPattern in $sinks) {
        foreach ($sink in [regex]::Matches($code, $sinkPattern)) {
            $literal = $sink.Groups[$sink.Groups.Count - 1].Value
            if ([string]::IsNullOrWhiteSpace($literal)) { continue }
            $lineNumber = 1 + ([regex]::Matches($code.Substring(0, $sink.Index), '\n')).Count
            $originalLine = [regex]::Split($source, '\r?\n')[$lineNumber - 1]
            if ($originalLine -match '//\s*language-audit:\s*\S.+') { continue }
            $problems.Add("$($file.Name):${lineNumber}: Visible literal '$literal' must use Language.ini")
        }
    }
}
if ($problems.Count) {
    $problems | Sort-Object -Unique | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    throw "HMI language check failed. Run the hmi-language agent and repair the reported text/keys."
}
Write-Host "HMI language check passed ($checkedReferences static language references)."
