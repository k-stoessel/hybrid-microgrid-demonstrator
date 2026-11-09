"""
Channel map and calibration.

The MCU sends the voltage at each ADC pin and applies no scaling, so every
channel assignment and calibration factor lives here. Changing either is a
file edit and a restart of the app, with no change to the firmware.

Wire units are 100 uV at the ADC input: a raw count of 6780 is 0.678 V at
the pin. Each channel's `scale` turns that into the real quantity:

    voltage channel   scale = (R1 + R2) / R2          divider ratio, 10.53
    current channel   scale = divider ratio / R_shunt  10.53 / 0.5 = 21.06

A scale of 1.0 reports the voltage at the ADC pin.
"""

from __future__ import annotations

from dataclasses import dataclass

# One wire count = 100 uV at the ADC input. Must match to_code() in
# src/telemetry.c.
LSB_VOLTS = 100e-6

# Nominal line frequency, used to size the scope window and the RMS window.
NOMINAL_HZ = 50.0

VOLTAGE = "voltage"
CURRENT = "current"


def divider(r1: float, r2: float) -> float:
    """Ratio of a divider with r1 on top and r2 to ground."""
    return (r1 + r2) / r2


@dataclass(frozen=True)
class Channel:
    name: str
    kind: str          # VOLTAGE or CURRENT
    scale: float       # multiplies volts-at-the-pin to give the real quantity
    unit: str


@dataclass(frozen=True)
class Point:
    """A place in the circuit measured by a voltage/current channel pair."""
    name: str
    v_ch: int
    i_ch: int
    ac: bool           # True -> report rms, False -> report mean


# ADC board 0: streamed as waveforms. Even channels are current, odd are
# voltage.
AC_CHANNELS: dict[int, Channel] = {
    0: Channel("Probe current",        CURRENT, 21.06, "A"),
    1: Channel("Probe voltage",        VOLTAGE, 10.53, "V"),
    2: Channel("Solar current",        CURRENT, 21.06, "A"),
    3: Channel("Solar voltage",        VOLTAGE, 10.53, "V"),
    4: Channel("Wind current",         CURRENT, 21.06, "A"),
    5: Channel("Wind voltage",         VOLTAGE, 10.53, "V"),
    6: Channel("Inverter current",     CURRENT, 21.06, "A"),
    7: Channel("Inverter voltage",     VOLTAGE, 10.53, "V"),
}

POINTS: list[Point] = [
    Point("Probe",        v_ch=1, i_ch=0, ac=True),
    Point("Solar",        v_ch=3, i_ch=2, ac=False),
    Point("Wind",         v_ch=5, i_ch=4, ac=True),
    Point("Inverter out", v_ch=7, i_ch=6, ac=True),
]

# Which point the scope draws. The probe leads can be moved around the rig,
# so the scope follows the probe.
SCOPE_POINT = 0

# Shown as a power figure with a trend behind it.
POWER_POINT = 3

# Shown as plain voltage/current pairs in the sidebar, in this order.
SUMMARY_POINTS = [1, 2]

# ADC board 1: averaged on the MCU and sent in the status frame twice a
# second. Indices are positions in the status frame's dc_uv array (board 1's
# channels in order). Unlisted channels are ignored.
DC_CHANNELS: dict[int, Channel] = {
    0: Channel("Battery",   VOLTAGE, 10.53, "V"),
    1: Channel("Capacitor", VOLTAGE, 10.53, "V"),
}

# Which DC index feeds which sidebar tile. Set either to None to drop that
# tile from the display.
BATTERY_DC = 0
CAPACITOR_DC = 1

# The capacitor is shown as a charge bar: 0% at CAP_VMIN, 100% at CAP_VMAX.
CAP_VMIN = 4.5
CAP_VMAX = 10.0


def ac_channel(idx: int) -> Channel:
    return AC_CHANNELS.get(idx, Channel(f"ch{idx}", VOLTAGE, 1.0, ""))


def dc_channel(idx: int) -> Channel | None:
    return DC_CHANNELS.get(idx)
