# Repository instructions

## Before every commit

- Fetch the current motion configuration from the local SimHub installation and
  copy it into the repository's `INI/` directory before creating any commit.
- The current source is
  `C:\Program Files (x86)\SimHub\PluginsData\Common\MotionPlugin.GeneralSettingsV2.json`.
  Store the snapshot as `INI/MotionPlugin.GeneralSettingsV2.json`, preserving its
  original filename and contents even though it is JSON rather than an INI file.
- Refresh this snapshot from the live source for every commit; do not reuse an
  existing repository copy. Include it in the commit whenever its contents change.
- If the source is unavailable or cannot be copied, resolve that before committing.
  Do not modify the live SimHub configuration as part of this step.
