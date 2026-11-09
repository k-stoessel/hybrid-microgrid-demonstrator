"""
Serial acquisition on a background thread.

The port is not read from the GUI thread, because a blocking read would
stall the Qt event loop and the touchscreen would stop responding. The reader
runs in a QThread and hands data to the interface with Qt signals.
"""

from __future__ import annotations

import math
import random
import time

from qtcompat import QThread, Signal

import channels as chmap
from protocol import (
    FrameParser, LinkStats, TYPE_AC, TYPE_STATUS,
    parse_ac, parse_status,
)


class SerialReader(QThread):
    """Reads framed data from a serial port and emits decoded blocks."""

    ac_block = Signal(object)     # (fs, [[ch0], [ch1], ...])
    status = Signal(object)       # dict
    stats = Signal(object)        # LinkStats
    failed = Signal(str)

    def __init__(self, port: str, baud: int, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self._running = False

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    def run(self) -> None:
        try:
            import serial
        except ImportError:
            self.failed.emit("pyserial not installed. Run: sudo apt install python3-serial")
            return

        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.05)
        except Exception as exc:
            self.failed.emit(f"Cannot open {self.port}: {exc}")
            return

        parser = FrameParser()
        stats = LinkStats(connected=True)
        self._running = True

        t0 = time.monotonic()
        last_emit = 0.0
        last_frame = 0.0

        while self._running:
            try:
                n = ser.in_waiting
                chunk = ser.read(n) if n else ser.read(1)
            except Exception as exc:
                self.failed.emit(f"Read error: {exc}")
                break

            if chunk:
                stats.bytes_total += len(chunk)
                for f in parser.feed(chunk):
                    if f.type == TYPE_AC:
                        # After a gap in the link the MCU may have restarted
                        # its sequence counter, so start counting afresh.
                        gap = time.monotonic() - last_frame
                        if last_frame and gap > 1.0:
                            stats.last_seq = None
                        last_frame = time.monotonic()
                        stats.note_seq(f.seq)
                        self.ac_block.emit(parse_ac(f.payload))
                    elif f.type == TYPE_STATUS:
                        self.status.emit(parse_status(f.payload))

            now = time.monotonic()
            if now - last_emit >= 0.25:
                last_emit = now
                el = max(now - t0, 1e-6)
                stats.crc_errors = parser.crc_errors
                stats.resyncs = parser.resyncs
                stats.blocks_per_s = stats.blocks / el
                stats.kb_per_s = stats.bytes_total / el / 1024.0
                self.stats.emit(stats)

        try:
            ser.close()
        except Exception:
            pass


class DemoReader(QThread):
    """Synthesises the same stream in software (run main.py with --demo).

    Lets the interface run with no MCU attached. It fills all eight AC
    channels according to the channel map, so every readout works as it does
    on hardware. The values are simulated, not measured.
    """

    ac_block = Signal(object)
    status = Signal(object)
    stats = Signal(object)
    failed = Signal(str)

    FS = 4000
    SPC = 80

    # Per-point synthetic amplitudes: (v_peak, i_peak, phase lag degrees)
    SHAPE = {
        "Probe":        (7.0, 0.30, 0.0),     # resistive, incandescent bulbs
        "Solar":        (18.0, 0.80, None),   # None -> DC
        "Wind":         (9.0, 0.45, 15.0),    # lightly inductive
        "Inverter out": (7.0, 0.35, 25.0),
    }

    def __init__(self, phase_deg: float | None = None, parent=None):
        """phase_deg, if given, overrides every AC point's phase lag.

        0 gives purely resistive loads. None uses each point's value from
        SHAPE above.
        """
        super().__init__(parent)
        self.phase_override = phase_deg
        self._running = False

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    def run(self) -> None:
        self._running = True
        stats = LinkStats(connected=True)
        n = 0
        slow = 0.0
        t0 = time.monotonic()
        last_emit = 0.0
        frame_len = 10 + 6 + self.SPC * 8 * 2 + 2

        while self._running:
            chans = [[0.0] * self.SPC for _ in range(8)]

            for point in chmap.POINTS:
                vpk, ipk, lag = self.SHAPE.get(point.name, (5.0, 0.2, 0.0))
                if self.phase_override is not None and lag is not None:
                    lag = self.phase_override

                for k in range(self.SPC):
                    ph = 2 * math.pi * (n * self.SPC + k) / self.SPC
                    if lag is None:
                        # DC source with a little ripple and noise
                        v = vpk + 0.15 * math.sin(ph)
                        i = ipk + 0.01 * math.sin(ph)
                    else:
                        v = vpk * (math.sin(ph) + 0.03 * math.sin(3 * ph))
                        i = ipk * math.sin(ph - math.radians(lag))
                    chans[point.v_ch][k] = v + random.uniform(-0.01, 0.01)
                    chans[point.i_ch][k] = i + random.uniform(-0.002, 0.002)

            self.ac_block.emit((self.FS, chans))
            stats.note_seq(n)
            stats.bytes_total += frame_len
            n += 1

            if n % 25 == 0:
                slow += 2 * math.pi * 0.1 * 0.5
                w = math.sin(slow)
                dc = {}
                for idx, ch in chmap.DC_CHANNELS.items():
                    base = 12.4 if "atter" in ch.name else 8.2
                    dc[idx] = base + 0.4 * w + random.uniform(-0.005, 0.005)
                self.status.emit({
                    "uptime_s": time.monotonic() - t0,
                    "seq_ac": n,
                    "mcu_dropped": 0,
                    "sample_errors": 0,
                    "adc_status": 0x0500,
                    "dc": dc,
                })

            now = time.monotonic()
            if now - last_emit >= 0.25:
                last_emit = now
                el = max(now - t0, 1e-6)
                stats.blocks_per_s = stats.blocks / el
                stats.kb_per_s = stats.bytes_total / el / 1024.0
                self.stats.emit(stats)

            self.msleep(20)