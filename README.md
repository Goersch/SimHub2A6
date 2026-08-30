# SimHub2A6

SimHub2A6 is a Windows bridge between the SimHub Motion Addon and a custom
seven-axis motion rig built with **StepperOnline A6 750 W servo motors and
drives**. SimHub sends calculated actuator positions over UDP; this application
validates them and controls the A6 drives over Modbus RTU/RS-485.

The project includes homing, synchronized position updates, four-corner load
leveling, load calibration, maintenance controls, greasing reminders, live
diagnostics, and CSV motion recording.

> [!WARNING]
> This software moves high-power machinery. Start with the rig unloaded, use
> conservative speed limits, and keep a tested hardware emergency stop within
> reach. Read [SECURITY.md](SECURITY.md) before enabling servo power.

## Hardware layout

The reference installation uses seven StepperOnline A6 750 W servo axes:

| Axis | Function | Default Modbus group |
| ---: | --- | --- |
| 1 | Front traction-loss slide | COM9 |
| 2 | Longitudinal/surge slide | COM9 |
| 3 | Rear traction-loss slide | COM9 |
| 4 | Front-left lift actuator | COM7 |
| 5 | Front-right lift actuator | COM6 |
| 6 | Rear-right lift actuator | COM7 |
| 7 | Rear-left lift actuator | COM6 |

Each A6 drive must have a unique Modbus slave ID matching its axis number. The
default connection is 115200 baud over three RS-485 adapters. Change the COM
ports and axis groups in `INI/SimHub2SimRig.ini` to match your wiring.

The supplied reference geometry is:

- 990 mm between the left and right lift actuators
- 1660 mm between the front and rear lift actuators
- 200 mm actuator stroke
- 900 mm center-of-gravity height
- 10 mm spindle pitch for axes 1–3 and 5 mm for axes 4–7

These values are installation-specific. Measure your rig and update the INI
before moving any axis.

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer with Tkinter
- [SimHub](https://www.simhubdash.com/download-2/) with the Motion Addon enabled
- Seven StepperOnline A6 750 W servo motor/drive sets
- RS-485 adapters and correctly terminated, grounded Modbus wiring
- Mechanical end stops, drive limits, brakes where required, and a hardware
  emergency-stop circuit

SimHub supports generic serial/UDP controllers for custom motion systems. Its
official [Motion Addon getting-started guide](https://manual.simhubdash.com/motion-addon/getting-started)
explains how to enable the addon and create a platform.

## Installation

Open PowerShell in the directory that will contain the repository:

```powershell
git clone https://github.com/Goersch/SimHub2A6.git
cd SimHub2A6
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Edit `INI/SimHub2SimRig.ini` before the first run. At minimum, verify every
`port`, `axes`, geometry, spindle pitch, stroke, zero offset, speed, and
acceleration/deceleration value.

Run the application from the repository's parent directory because it is a
Python package:

```powershell
cd ..
.\SimHub2A6\.venv\Scripts\python.exe -m SimHub2A6.SimHub2SimRig
```

To request a clean shutdown from another terminal:

```powershell
.\SimHub2A6\.venv\Scripts\python.exe -m SimHub2A6.ShutDown
```

## SimHub configuration

### 1. Enable and build the motion platform

1. In SimHub, open **Add / Remove Features** and enable **Motion**.
2. Open **Motion → Configure your platform**.
3. Configure the reference rig as a four-corner 3DOF linear platform plus a
   dedicated surge axis and dual front/rear traction-loss axes.
4. Enter the measured geometry. For the reference rig, use 990 mm width,
   1660 mm length, 200 mm lift stroke, and 900 mm center-of-gravity height.
5. Add a **Generic UDP output** controller with seven axes.

SimHub's official controller documentation notes that generic UDP output is
intended for DIY or unsupported controllers whose protocol is known. Review the
[supported controllers](https://manual.simhubdash.com/motion-addon/supported-controllers)
and [supported motion setups](https://manual.simhubdash.com/motion-addon/supported-motion-setups)
pages before configuring a different geometry.

### 2. Configure Generic UDP output

Use these controller settings:

| Setting | Value |
| --- | --- |
| Target IP address | `127.0.0.1` |
| Target UDP port | `9999` |
| Controlled axes | `7` |
| Axis resolution | `17 bit` (`0`–`131071`) |
| Axis format | Decimal string |
| Startup command | `START` |
| Startup delay | `0 ms` |
| Update command | `POSITIONS <Axis1> <Axis2> <Axis3> <Axis4> <Axis5> <Axis6> <Axis7>` |
| Update command delay | `10 ms` |
| Shutdown command | `END` |
| Shutdown delay | `0 ms` |

The command text is case-insensitive in SimHub2A6, but the SimHub placeholders
such as `<Axis1>` are case-sensitive.

### 3. Assign axes

Assign and test one output at a time:

1. Axis 1 → front traction loss
2. Axis 2 → dedicated surge/longitudinal slide
3. Axis 3 → rear traction loss
4. Axis 4 → front-left lift
5. Axis 5 → front-right lift
6. Axis 6 → rear-right lift
7. Axis 7 → rear-left lift

Reverse an axis in SimHub only when its physical direction is opposite to the
table. Confirm direction at low gain with nobody in the rig.

### 4. Recommended reference-profile values

The included helper checks or updates three values in SimHub's active motion
profile:

- Front/Rear Traction Loss smoothing: `15`
- Generic UDP update delay: `10 ms`
- Four-lift geometry actuator spike filter: `135 mm/s`

Close SimHub first. Run a preview from the repository directory:

```powershell
python tools\update_simhub_motion_config.py
```

If the detected device, profile, game, and changes are correct, open PowerShell
as Administrator and apply them:

```powershell
python tools\update_simhub_motion_config.py --apply
```

The helper creates a timestamped backup next to SimHub's
`MotionPlugin.GeneralSettingsV2.json` and restores it if the write fails.
Profile gains and effects remain rig- and game-specific; begin at low gain and
tune one effect at a time.

## First safe startup

1. Disconnect mechanical loads where possible and clear the motion envelope.
2. Verify the hardware emergency stop disables every drive.
3. Configure each A6 slave ID and the INI COM-port mapping.
4. Start SimHub2A6 while SimHub motion output is disabled.
5. Use **Maintenance** to home and test one axis group at a time.
6. Confirm the displayed actual position and direction.
7. Test SimHub's manual axis assignment at minimum gain.
8. Only after every axis is correct, enable the complete geometry.

If homing, direction, limits, brakes, or communications are uncertain, stop and
remove servo power before troubleshooting.

## Configuration and generated data

- `INI/SimHub2SimRig.ini` contains serial, geometry, motion, leveling, greasing,
  UI, and optional room-light settings.
- `INI/Language.ini` contains every user-visible dialog string.
- `INI/grease_data.json`, `INI/leveling_offsets.json`, and
  `INI/simrig_load_values.json` are generated locally and intentionally ignored
  by Git. Neutral examples are available in `INI/examples/`.
- `LOG/` and `SimHubData/` contain local logs and recordings and are ignored.

The room-light URL in the checked-in INI is only a localhost example. Replace
it with your own endpoint or do not use the light buttons.

## UDP protocol

SimHub2A6 listens on the configured UDP address and accepts semicolon-separated
ASCII commands:

```text
START
POSITIONS 65535 65535 65535 65535 65535 65535 65535
END
```

`POSITIONS` requires exactly seven integers in the configured inclusive range.
`START` begins motion recording, and `END` stops it. `SHUTDOWN` is reserved for
the included shutdown client and returns shutdown status datagrams.

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
ruff check .
pyright
```

GitHub Actions runs the test suite on every push and pull request. Additional
developer information is in [DEVELOPMENT.md](DEVELOPMENT.md).

## License and trademarks

The source code is available under the [MIT License](LICENSE).

SimHub is a product of its respective owner. StepperOnline and A6 are marks of
their respective owner. This independent project is not affiliated with or
endorsed by SimHub or StepperOnline. Obtain the current A6 manual directly from
the manufacturer; the third-party PDF is intentionally not redistributed here.
