# Repository instructions

## HMI language agent

- After changing `CSharp/HMI/`, or changing other code that produces HMI text,
  run the `hmi-language` sub-agent before completing the task. This is an
  explicit instruction to delegate this check. The agent must inspect the final
  changes, fix localization gaps, and verify them against `INI/Language.ini`.
- Repeat the agent check if further HMI changes are made after its review.
  Coordinate file ownership so agents do not edit the same files concurrently.
- The project custom agent is `.codex/agents/hmi-language.toml`; its maintained
  instructions are in `CSharp/Agents/HmiLanguage.md`. If the available spawn
  tool cannot select a custom agent type, spawn a sub-agent with those
  instructions explicitly included in its task.
- All application-owned visible HMI text must come from `INI/Language.ini`.
  Preserve protocol values, identifiers, file names and external diagnostic
  details. Use stable control names/tags in code and tests, never translated
  text, for behavior or control lookup.
- Run `powershell -NoProfile -ExecutionPolicy Bypass -File
  CSharp/Tests/check_hmi_language.ps1` and the relevant localization/UI tests.
  `CSharp/build.ps1` also runs the static language check on every build.

## Before every commit

- Fetch the current motion configuration from the local SimHub installation and
  copy it into the repository's `INI/` directory before creating any commit.
- The current source is
  `C:\Program Files (x86)\SimHub\PluginsData\Common\MotionPlugin.GeneralSettingsV2.json`.
  Store the snapshot as `INI/MotionPlugin.GeneralSettingsV2.json`, preserving its
  original filename and all non-secret contents even though it is JSON rather
  than an INI file. Before staging, replace every `ApiToken` value with an empty
  string. Never commit API tokens from the live installation.
- Refresh this snapshot from the live source for every commit; do not reuse an
  existing repository copy. Include it in the commit whenever its contents change.
- If the source is unavailable or cannot be copied, resolve that before committing.
  Do not modify the live SimHub configuration as part of this step.
