# Security and safety

## Reporting software vulnerabilities

Please report security issues privately through GitHub's **Report a
vulnerability** feature. Do not include credentials, private network details,
SimHub API tokens, or motion recordings in a public issue.

## Motion-system safety

This project controls high-power motion hardware. Software limits are not a
substitute for mechanical end stops, a hardware emergency stop, drive-level
limits, correctly wired brakes, and a safe operating area.

- Test without a driver in the rig and at the lowest practical speed.
- Verify axis direction and homing one axis at a time.
- Keep clear of the mechanism whenever servo power is available.
- Ensure every StepperOnline A6 750 W servo drive can be disabled independently
  of this software.
- Do not use the software if the Modbus wiring, grounding, braking, or emergency
  stop chain has not been validated by a qualified person.

The maintainers cannot validate a reader's mechanical or electrical design and
accept no responsibility for injury or hardware damage.
