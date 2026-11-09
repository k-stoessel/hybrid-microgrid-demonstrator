"""
Frame parsing and signal analysis for the telemetry stream from the
STM32F446RE. Free of Qt and pyserial imports.

Wire format (little-endian), must match src/telemetry.c on the MCU:

    offset  size  field
    0       2     sync = 0x5AA5
    2       1     type (1 = AC block, 2 = status)
    3       1     version = 2
    4       2     payload_len
    6       4     seq
    10      N     payload
    10+N    2     CRC-16/CCITT-FALSE over bytes [0 .. 10+N-1]

AC payload: n_samples, sample_rate_hz, n_channels, reserved, then
n_samples * n_channels int16 in units of 100 uV, sample-major.

Status payload: uptime_ms, seq_ac, dropped_frames, sample_errors,
adc_status, n_dc, reserved, then n_dc int32 of mean microvolts.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

import channels as chmap

SYNC = b"\xA5\x5A"
HDR = struct.Struct("<HBBHI")
HDR_LEN = HDR.size
MAX_PAYLOAD = 8192

TYPE_AC = 1
TYPE_STATUS = 2

AC_HDR = struct.Struct("<HHBB")
ST_HDR = struct.Struct("<IIIIHBB")


def crc16_ccitt_false(data: bytes) -> int:
    """Poly 0x1021, init 0xFFFF, no reflection, no final XOR.

    Matches crc16_ccitt_false() in the firmware, which uses a nibble table
    for speed but produces identical results. Check value for b"123456789"
    is 0x29B1.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class Frame:
    type: int
    version: int
    seq: int
    payload: bytes


class FrameParser:
    """Byte stream -> frames, resynchronising on the sync word after errors.

    Feed it whatever the serial port gives you; it buffers partial frames.
    """

    def __init__(self) -> None:
        self.buf = bytearray()
        self.crc_errors = 0
        self.resyncs = 0

    def feed(self, chunk: bytes):
        self.buf.extend(chunk)
        while True:
            frame = self._try_one()
            if frame is None:
                return
            yield frame

    def _try_one(self):
        idx = self.buf.find(SYNC)
        if idx < 0:
            # Keep one byte back in case the sync word straddles a chunk edge
            del self.buf[: max(0, len(self.buf) - 1)]
            return None
        if idx > 0:
            del self.buf[:idx]
            self.resyncs += 1

        if len(self.buf) < HDR_LEN:
            return None

        _sync, ftype, ver, plen, seq = HDR.unpack_from(self.buf, 0)
        if plen > MAX_PAYLOAD:
            del self.buf[:2]
            self.resyncs += 1
            return None

        total = HDR_LEN + plen + 2
        if len(self.buf) < total:
            return None

        body = bytes(self.buf[: HDR_LEN + plen])
        got = struct.unpack_from("<H", self.buf, HDR_LEN + plen)[0]

        if got != crc16_ccitt_false(body):
            self.crc_errors += 1
            del self.buf[:2]
            return None

        del self.buf[:total]
        return Frame(ftype, ver, seq, body[HDR_LEN:])


def parse_ac(payload: bytes):
    """-> (sample_rate_hz, [[ch0 samples], [ch1 samples], ...])

    Values come back in real units: wire counts are multiplied by 100 uV to
    get volts at the ADC pin, then by that channel's scale from channels.py.
    With every scale at 1.0 you get volts at the pin, which is what you want
    before calibration.
    """
    n, fs, n_ch, _res = AC_HDR.unpack_from(payload, 0)
    raw = struct.unpack_from("<%dh" % (n * n_ch), payload, AC_HDR.size)

    out = []
    for ch in range(n_ch):
        k = chmap.LSB_VOLTS * chmap.ac_channel(ch).scale
        out.append([raw[s * n_ch + ch] * k for s in range(n)])

    return fs, out


def parse_status(payload: bytes) -> dict:
    """-> dict of link counters plus {dc index: scaled value}."""
    uptime, seq_ac, dropped, errors, adc_st, n_dc, _res = \
        ST_HDR.unpack_from(payload, 0)

    dc_uv = struct.unpack_from("<%di" % n_dc, payload, ST_HDR.size) if n_dc else ()

    dc = {}
    for idx, uv in enumerate(dc_uv):
        ch = chmap.dc_channel(idx)
        if ch is not None:
            dc[idx] = uv * 1e-6 * ch.scale

    return {
        "uptime_s": uptime / 1000.0,
        "seq_ac": seq_ac,
        "mcu_dropped": dropped,
        "sample_errors": errors,
        "adc_status": adc_st,
        "dc": dc,
    }


def _crossings(buf, level: float, hyst: float):
    """Rising crossings of `level`, sub-sample interpolated.

    Hysteresis: the signal must first fall below (level - hyst) before another
    crossing is accepted. Without it, noise near the crossing produces a burst
    of false triggers and the display jitters.
    """
    out = []
    armed = False
    for i in range(1, len(buf)):
        if buf[i] < level - hyst:
            armed = True
        elif armed and buf[i] >= level > buf[i - 1]:
            span = buf[i] - buf[i - 1]
            frac = (level - buf[i - 1]) / span if span else 0.0
            out.append(i - 1 + frac)
            armed = False
    return out


def find_trigger(buf, need: int, hyst: float, level: float = 0.0):
    """Index of the latest rising crossing leaving `need` samples after it.

    This is what a scope's trigger does: align every sweep to the same point on
    the waveform so the trace stands still instead of crawling sideways.
    """
    limit = len(buf) - need
    if limit <= 1:
        return None
    last = None
    armed = False
    for i in range(1, limit):
        if buf[i] < level - hyst:
            armed = True
        elif armed and buf[i] >= level > buf[i - 1]:
            last = i
            armed = False
    return last


def rms(xs) -> float:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0


def mean(xs) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@dataclass
class Metrics:
    vrms: float = 0.0
    irms: float = 0.0
    vmean: float = 0.0             # for DC points
    imean: float = 0.0
    real_power: float = 0.0        # W, mean of v*i -- correct for any waveform
    apparent_power: float = 0.0    # VA
    power_factor: float = 0.0
    freq: float = 0.0              # Hz, from zero crossings

    @property
    def reactive_power(self) -> float:
        s2 = self.apparent_power ** 2 - self.real_power ** 2
        return math.sqrt(s2) if s2 > 0 else 0.0


class Analyzer:
    """Rolling per-channel sample history plus trigger and power metrics.

    History is kept for every channel, not just the displayed pair, so
    switching measurement point is instant and the sidebar can show figures
    for points that are not on screen.

    Metrics are computed over a whole number of cycles; a partial cycle biases
    RMS and makes the readout wobble.
    """

    def __init__(self, n_channels: int = 8, cycles_shown: int = 2,
                 history_cycles: int = 6):
        self.cycles_shown = cycles_shown
        self.history_cycles = history_cycles
        self.fs = 4000
        self.samples_per_cycle = 80
        self.n_channels = n_channels
        self.ch: list[list[float]] = [[] for _ in range(n_channels)]

    def _resize(self, fs: int, n_channels: int):
        if n_channels != self.n_channels:
            self.n_channels = n_channels
            self.ch = [[] for _ in range(n_channels)]
        if fs != self.fs:
            self.fs = fs
        self.samples_per_cycle = max(8, int(round(fs / chmap.NOMINAL_HZ)))

    @property
    def window(self) -> int:
        return self.samples_per_cycle * self.cycles_shown

    def clear(self) -> None:
        for buf in self.ch:
            buf.clear()

    def add_block(self, fs: int, chans) -> None:
        self._resize(fs, len(chans))
        cap = self.samples_per_cycle * self.history_cycles
        for i, block in enumerate(chans):
            buf = self.ch[i]
            buf.extend(block)
            if len(buf) > cap:
                del buf[: len(buf) - cap]

    def ready(self) -> bool:
        return (bool(self.ch)
                and len(self.ch[0]) >= self.window + self.samples_per_cycle)

    def triggered(self, v_ch: int, i_ch: int):
        """-> (v_slice, i_slice) aligned to a rising zero crossing, or None."""
        need = self.window
        if not self.ready():
            return None

        v = self.ch[v_ch]
        i = self.ch[i_ch]

        peak = max((abs(x) for x in v), default=0.0) or 1.0
        idx = find_trigger(v, need, hyst=0.05 * peak)
        if idx is None:
            idx = len(v) - need      # free-run if no crossing found (DC point)
        return v[idx:idx + need], i[idx:idx + need]

    def metrics(self, v_ch: int, i_ch: int) -> Metrics:
        """RMS, mean and power over a whole number of cycles.

        The window runs between the first and last zero crossing found in the
        history, which is exactly a whole number of cycles *at whatever
        frequency the signal actually is*. That matters for the wind turbine:
        an alternator's output frequency follows rotor speed, so a window
        sized from a nominal 50 Hz would leave a partial cycle in the sum and
        the reading would drift with wind speed rather than with voltage.

        Falls back to a nominal-frequency window when no crossings are found,
        which is the DC case.
        """
        if not self.ch or not self.ch[v_ch]:
            return Metrics()

        whole = self.ch[v_ch]
        total = len(whole)
        if total < self.samples_per_cycle:
            return Metrics()

        peak = max((abs(x) for x in whole), default=0.0) or 1.0
        xs = _crossings(whole, 0.0, 0.05 * peak)

        freq = 0.0
        if len(xs) >= 2:
            period = (xs[-1] - xs[0]) / (len(xs) - 1)
            if period > 0:
                freq = self.fs / period
            lo, hi = int(round(xs[0])), int(round(xs[-1]))
        else:
            n = self.samples_per_cycle * max(1, total // self.samples_per_cycle)
            lo, hi = total - min(n, total), total

        if hi - lo < 2:
            lo, hi = 0, total

        v = whole[lo:hi]
        i = self.ch[i_ch][lo:hi]

        vr = rms(v)
        ir = rms(i)
        p = sum(a * b for a, b in zip(v, i)) / len(v)
        s = vr * ir
        pf = (p / s) if s > 1e-9 else 0.0

        return Metrics(vr, ir, mean(v), mean(i), p, s, pf, freq)


@dataclass
class LinkStats:
    blocks: int = 0
    lost: int = 0
    crc_errors: int = 0
    resyncs: int = 0
    bytes_total: int = 0
    blocks_per_s: float = 0.0
    kb_per_s: float = 0.0
    connected: bool = False
    last_seq: int | None = field(default=None, repr=False)

    def note_seq(self, seq: int) -> None:
        if self.last_seq is not None and seq != self.last_seq + 1:
            self.lost += (seq - self.last_seq - 1) & 0xFFFFFFFF
        self.last_seq = seq
        self.blocks += 1