#!/usr/bin/env python3

"""Register map and command registers of the A6 servo drive."""

# C00: Basic settings
C00_02 = 0x0002  # Pulses per revolution (U32)
C00_22 = 0x0022  # Firmware/display unit setting (U16)

# C03: Electronic gear and position settings
C03_00 = 0x0300  # Position reference: 0=pulse, 1=multi-position
C03_02 = 0x0302  # Electronic gear numerator (U32)
C03_04 = 0x0304  # Electronic gear denominator (U32)
C03_06 = 0x0306
C03_12 = 0x0312  # Position-reached threshold (U16)

# C04: Digital input/output configuration
C04_01 = 0x0401
C04_05 = 0x0405
C04_08 = 0x0408
C04_09 = 0x0409
C04_0D = 0x040D
C04_11 = 0x0411
C04_12 = 0x0412
C04_14 = 0x0414
C04_15 = 0x0415
C04_18 = 0x0418
C04_19 = 0x0419
C04_1C = 0x041C
C04_1D = 0x041D
C04_38 = 0x0438  # DO5 function
C04_39 = 0x0439  # DO5 logic

# Command registers
S_ON = 0x0411
POS_TRIG = 0x0415
HOM_TRIG = 0x0419

# C05: Position movement
C05_0A = 0x050A
C05_0C = 0x050C

# C0A: Communication and parameter storage
C0A_05 = 0x0A05
C0A_06 = 0x0A06  # 32-bit word order: 0=low-first, 1=high-first

# C0E: Position limits
C0E_00 = 0x0E00
C0E_03 = 0x0E03

# C10: Homing
C10_00 = 0x1000
C10_01 = 0x1001
C10_02 = 0x1002
C10_03 = 0x1003
C10_04 = 0x1004
C10_06 = 0x1006
C10_08 = 0x1008
C10_0A = 0x100A
C10_0B = 0x100B

# C11: Multi-position group 1
C11_00 = 0x1100
C11_01 = 0x1101
C11_02 = 0x1102
C11_03 = 0x1103
C11_04 = 0x1104
C11_06 = 0x1106
C11_08 = 0x1108
C11_0A = 0x110A
C11_0C = 0x110C
C11_0E = 0x110E

# U40/U41: Runtime status
U40_00 = 0x4000
U40_01 = 0x4001
U40_02 = 0x4002
U40_03 = 0x4003
U40_04 = 0x4004
U40_05 = 0x4005
U40_07 = 0x4007
U40_16 = 0x4016
U40_43 = 0x4043
U41_0A = 0x410A
U41_0B = 0x410B
