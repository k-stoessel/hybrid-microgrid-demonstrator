"""
Single-window interface for the 1920 x 720 touchscreen.

The waveform is a pyqtgraph PlotWidget placed in the same layout as the
other widgets, so the whole app is one window. The scope pane on the left
shows the probe point; the sidebar on the right shows the fixed measurement
points as numbers.
"""

from __future__ import annotations

import math
import time
from collections import deque

import pyqtgraph as pg
from qtcompat import (
    ALIGN_BOTTOM, SIZE_EXPANDING, QFrame, QGridLayout, QHBoxLayout,
    QKeySequence, QLabel, QPushButton, QShortcut, QTimer, QVBoxLayout,
    QWidget, QtCore, QtGui, Qt, enum_of,
)

import channels as chmap
import theme
from protocol import Analyzer

pg.setConfigOptions(antialias=True, background=theme.BG, foreground=theme.MUTED)


class MiniReadout(QWidget):
    """Compact label/value pair for secondary measurements."""

    def __init__(self, label: str, unit: str = "", decimals: int = 2, parent=None):
        super().__init__(parent)
        self.setObjectName("Mini")
        self.decimals = decimals
        self.unit = unit
        self._value = QLabel("--")
        self._value.setObjectName("MiniValue")
        self._name = QLabel(label)
        self._name.setObjectName("MiniLabel")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self._name)
        box.addWidget(self._value)

    def set_label(self, text: str) -> None:
        self._name.setText(text)

    def set_unit(self, text: str) -> None:
        self.unit = text

    def set_value(self, v: float | None) -> None:
        if v is None:
            self._value.setText("--")
        else:
            self._value.setText(f"{v:.{self.decimals}f}{self.unit}")


class ValueTile(QFrame):
    """A titled tile holding one large numeric readout."""

    def __init__(self, label: str, unit: str, decimals: int = 2, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.decimals = decimals

        name = QLabel(label)
        name.setObjectName("TileLabel")
        self._value = QLabel("--")
        self._value.setObjectName("TileValue")
        unit_lbl = QLabel(unit)
        unit_lbl.setObjectName("TileUnit")

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._value)
        row.addWidget(unit_lbl, alignment=ALIGN_BOTTOM)
        row.addStretch(1)

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 8, 14, 10)
        box.setSpacing(2)
        box.addWidget(name)
        box.addLayout(row)

    def set_value(self, v: float | None) -> None:
        self._value.setText("--" if v is None else f"{v:.{self.decimals}f}")


class PairTile(QFrame):
    """A titled tile holding two readouts side by side."""

    def __init__(self, title: str, left: str, right: str,
                 left_unit: str = "", right_unit: str = "",
                 decimals: int = 3, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.title = QLabel(title)
        self.left = MiniReadout(left, left_unit, decimals)
        self.right = MiniReadout(right, right_unit, decimals)

        grid = QGridLayout(self)
        grid.setContentsMargins(14, 8, 14, 10)
        grid.setSpacing(4)
        grid.addWidget(self.title, 0, 0, 1, 2)
        grid.addWidget(self.left, 1, 0)
        grid.addWidget(self.right, 1, 1)

    def set_values(self, a: float | None, b: float | None) -> None:
        self.left.set_value(a)
        self.right.set_value(b)


class ChargeBar(QWidget):
    """Segmented battery-style charge indicator.

    Maps a voltage onto a 0-100% fill between vmin and vmax, which is easier
    to read at a glance than a bare voltage.
    """

    SEGMENTS = 10

    def __init__(self, vmin: float, vmax: float, parent=None):
        super().__init__(parent)
        self.vmin = vmin
        self.vmax = vmax
        self._frac = None
        self.setMinimumHeight(32)

    def set_voltage(self, v: float | None) -> None:
        if v is None:
            self._frac = None
        else:
            span = self.vmax - self.vmin
            f = (v - self.vmin) / span if span else 0.0
            self._frac = max(0.0, min(1.0, f))
        self.update()

    @property
    def percent(self) -> float | None:
        return None if self._frac is None else self._frac * 100.0

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(enum_of(QtGui.QPainter, "RenderHint", "Antialiasing"))

        no_pen = enum_of(Qt, "PenStyle", "NoPen")
        no_brush = enum_of(Qt, "BrushStyle", "NoBrush")

        w, h = self.width(), self.height()
        nub_w = 5.0
        body = QtCore.QRectF(1.0, 1.0, w - nub_w - 4.0, h - 2.0)

        pen = QtGui.QPen(QtGui.QColor(theme.PANEL_EDGE))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(no_brush)
        p.drawRoundedRect(body, 4, 4)

        # The terminal nub, so it reads as a battery at a glance
        p.setPen(no_pen)
        p.setBrush(QtGui.QBrush(QtGui.QColor(theme.PANEL_EDGE)))
        nub_h = h * 0.42
        p.drawRoundedRect(
            QtCore.QRectF(body.right() + 2.0, (h - nub_h) / 2.0, nub_w, nub_h),
            2, 2)

        if self._frac is not None:
            inner = body.adjusted(4.0, 4.0, -4.0, -4.0)
            gap = 3.0
            seg_w = (inner.width() - gap * (self.SEGMENTS - 1)) / self.SEGMENTS

            # Lit segments take the colour of the state, not just the level
            if self._frac > 0.5:
                lit_colour = theme.GOOD
            elif self._frac > 0.2:
                lit_colour = theme.VOLT
            else:
                lit_colour = theme.ALERT

            lit = int(round(self._frac * self.SEGMENTS))
            for k in range(self.SEGMENTS):
                x = inner.left() + k * (seg_w + gap)
                colour = lit_colour if k < lit else theme.GRID
                p.setBrush(QtGui.QBrush(QtGui.QColor(colour)))
                p.drawRoundedRect(
                    QtCore.QRectF(x, inner.top(), seg_w, inner.height()), 2, 2)

        p.end()


class ChargeTile(QFrame):
    """A charge bar with its percentage and the raw voltage beside it."""

    def __init__(self, title: str, vmin: float, vmax: float,
                 unit: str = "V", parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")

        name = QLabel(title)
        name.setObjectName("TileLabel")

        self.bar = ChargeBar(vmin, vmax)
        self._pct = QLabel("--")
        self._pct.setObjectName("TileValue")
        self._volts = QLabel("--")
        self._volts.setObjectName("MiniValue")
        self.unit = unit

        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(self._pct)
        right.addWidget(self._volts)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.bar, stretch=1)
        row.addLayout(right)

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 8, 14, 10)
        box.setSpacing(6)
        box.addWidget(name)
        box.addLayout(row)

    def set_value(self, v: float | None) -> None:
        self.bar.set_voltage(v)
        pct = self.bar.percent
        self._pct.setText("--" if pct is None else f"{pct:.0f}%")
        self._volts.setText("--" if v is None else f"{v:.2f} {self.unit}")


class TrendTile(QFrame):
    """A big number with its recent history plotted underneath."""

    SPAN_S = 120          # two minutes of history
    STEP_S = 1.0          # one point per second

    def __init__(self, title: str, unit: str, decimals: int = 2, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.decimals = decimals
        self._pts: deque[float] = deque(maxlen=int(self.SPAN_S / self.STEP_S))
        self._last_add = 0.0

        name = QLabel(title)
        name.setObjectName("TileLabel")
        self._value = QLabel("--")
        self._value.setObjectName("TileValue")
        unit_lbl = QLabel(unit)
        unit_lbl.setObjectName("TileUnit")

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._value)
        row.addWidget(unit_lbl, alignment=ALIGN_BOTTOM)
        row.addStretch(1)

        self.plot = pg.PlotWidget()
        self.plot.setFixedHeight(62)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.showGrid(x=False, y=True, alpha=0.15)
        self.plot.getAxis("bottom").setStyle(showValues=False)
        self.plot.getAxis("left").setStyle(showValues=False)
        for ax in ("left", "bottom"):
            self.plot.getAxis(ax).setPen(theme.GRID)
        self.plot.setLabel("bottom", f"last {self.SPAN_S // 60} min",
                           color=theme.MUTED)
        # Fixed X range so the trace grows in from the left as history
        # accumulates, rather than three early points being stretched across
        # the whole width and looking like a filled block.
        self.plot.setXRange(0, int(self.SPAN_S / self.STEP_S), padding=0)
        self.curve = self.plot.plot(
            pen=pg.mkPen(theme.GOOD, width=2),
            fillLevel=0.0, brush=pg.mkBrush(111, 207, 127, 40))

        box = QVBoxLayout(self)
        box.setContentsMargins(14, 8, 14, 8)
        box.setSpacing(4)
        box.addWidget(name)
        box.addLayout(row)
        box.addWidget(self.plot)

    def set_value(self, v: float | None) -> None:
        self._value.setText("--" if v is None else f"{v:.{self.decimals}f}")
        if v is None:
            return
        now = time.monotonic()
        if now - self._last_add >= self.STEP_S:
            self._last_add = now
            self._pts.append(v)
            ys = list(self._pts)
            self.curve.setData(list(range(len(ys))), ys)
            lo, hi = min(ys), max(ys)
            pad = max((hi - lo) * 0.15, abs(hi) * 0.05, 1e-6)
            self.plot.setYRange(min(lo - pad, 0.0), hi + pad)

    def clear(self) -> None:
        self._pts.clear()
        self.curve.setData([], [])
        self._value.setText("--")


class _TwoToneAxis(pg.AxisItem):
    """A left axis that can carry two differently coloured scales.

    In split mode the left axis shows volts over the upper half of the plot
    and amps over the lower. Each tick label is coloured by where it lands:
    above the midline it is a voltage, below it a current.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.two_tone = False

    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        if not self.two_tone:
            super().drawPicture(p, axisSpec, tickSpecs, textSpecs)
            return

        p.setRenderHint(p.RenderHint.Antialiasing, False)
        p.setRenderHint(p.RenderHint.TextAntialiasing, True)

        pen, p1, p2 = axisSpec
        p.setPen(pen)
        p.drawLine(p1, p2)
        for pen, p1, p2 in tickSpecs:
            p.setPen(pen)
            p.drawLine(p1, p2)

        p.setRenderHint(p.RenderHint.Antialiasing, True)
        mid = self.boundingRect().center().y()
        for rect, flags, text in textSpecs:
            p.setPen(pg.mkPen(theme.VOLT if rect.center().y() < mid
                              else theme.CURR))
            p.drawText(rect, int(flags), text)


class ScopePane(pg.PlotWidget):
    """Triggered two-channel waveform display with independent Y axes.

    Voltage reads against the left axis in volts, current against the right
    in amps. Each axis scales to its own trace, so a small current is still
    visible beside a large voltage, and each axis is coloured to match its
    trace.
    """

    # Split-mode view ranges, as multiples of each trace's half-height.
    # Shifting the axis ranges, rather than the data, keeps the numbers real.
    SPLIT_V = (-3.0, 1.0)      # voltage zero three quarters up
    SPLIT_I = (-1.0, 3.0)      # current zero one quarter up

    # Width the left axis is pinned to in split mode, wide enough for a
    # band title and its numbers side by side without crowding.
    SPLIT_AXIS_W = 64

    def __init__(self, parent=None):
        self._left_axis = _TwoToneAxis(orientation="left")
        super().__init__(parent, axisItems={"left": self._left_axis})
        self.p1 = self.plotItem

        self.showGrid(x=True, y=True, alpha=0.18)
        self.setMenuEnabled(False)
        self.hideButtons()
        self.setMouseEnabled(x=False, y=False)

        # A second ViewBox sharing the scene drives the right-hand axis. It
        # follows p1 in X and is positioned to match it on every resize.
        self.p2 = pg.ViewBox()
        self.p2.setMouseEnabled(x=False, y=False)
        self.p1.showAxis("right")
        self.p1.scene().addItem(self.p2)
        self.p1.getAxis("right").linkToView(self.p2)
        self.p2.setXLink(self.p1)

        for name, colour in (("left", theme.VOLT), ("right", theme.CURR),
                             ("bottom", theme.TEXT)):
            ax = self.p1.getAxis(name)
            ax.setPen(theme.GRID)
            ax.setTextPen(colour)
            # Stop pyqtgraph rescaling small values and adding a "(x0.001)"
            # multiplier to the axis label.
            ax.enableAutoSIPrefix(False)

        self.setLabel("left", "voltage (V)", color=theme.VOLT)
        self.setLabel("right", "current (A)", color=theme.CURR)
        self.setLabel("bottom", "time (ms)", color=theme.TEXT)

        self.curve_v = pg.PlotDataItem(pen=pg.mkPen(theme.VOLT, width=2))
        self.p1.addItem(self.curve_v)
        self.curve_i = pg.PlotDataItem(pen=pg.mkPen(theme.CURR, width=2))
        self.p2.addItem(self.curve_i)

        # Tapping a legend entry hides or shows that trace.
        legend = self.p1.addLegend(offset=(-52, 10), labelTextColor=theme.TEXT)
        legend.addItem(self.curve_v, "voltage")
        legend.addItem(self.curve_i, "current")

        # One zero line per trace, shown only in split mode.
        self.split = False
        dash = (pg.QtCore.Qt.PenStyle.DashLine
                if hasattr(pg.QtCore.Qt, "PenStyle") else pg.QtCore.Qt.DashLine)
        self._zero_v = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen(theme.VOLT, width=1, style=dash))
        self._zero_i = pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen(theme.CURR, width=1, style=dash))
        self._zero_v.setVisible(False)
        self._zero_i.setVisible(False)
        self.p1.addItem(self._zero_v)
        self.p2.addItem(self._zero_i)

        # Both peak labels live in p1; the current label's height is mapped
        # across from amps with _i_to_p1().
        self._peak_v = pg.TextItem("", color=theme.VOLT, anchor=(0, 0.5))
        self._peak_i = pg.TextItem("", color=theme.CURR, anchor=(0, 0.5))
        self.p1.addItem(self._peak_v)
        self.p1.addItem(self._peak_i)

        # In split mode both scales share the left axis, which has only one
        # title, so each band gets its own title drawn beside it. angle=90
        # matches the direction of pyqtgraph's own left-axis title.
        self._band_lab_v = pg.TextItem("voltage (V)", color=theme.VOLT,
                                       anchor=(0.5, 0.5), angle=90)
        self._band_lab_i = pg.TextItem("current (A)", color=theme.CURR,
                                       anchor=(0.5, 0.5), angle=90)
        for it in (self._band_lab_v, self._band_lab_i):
            self.p1.scene().addItem(it)
            it.setVisible(False)

        self.p1.vb.sigResized.connect(self._sync_views)
        self._sync_views()

    def _sync_views(self) -> None:
        """Keep the second ViewBox sitting exactly over the first."""
        self.p2.setGeometry(self.p1.vb.sceneBoundingRect())
        self.p2.linkedViewChanged(self.p1.vb, self.p2.XAxis)
        self._place_band_labels()

    def _place_band_labels(self) -> None:
        """Park each split-mode band title level with its own band."""
        if not (self._band_lab_v.isVisible() or self._band_lab_i.isVisible()):
            return
        r = self.p1.vb.sceneBoundingRect()
        ar = self.p1.getAxis("left").sceneBoundingRect()
        x = ar.left() + 10
        # Voltage zero sits three quarters up, current zero one quarter up.
        self._band_lab_v.setPos(x, r.top() + 0.25 * r.height())
        self._band_lab_i.setPos(x, r.top() + 0.75 * r.height())

    def set_split(self, on: bool) -> None:
        """Separate the traces vertically.

        Done by shifting each axis's range rather than offsetting the data,
        so both keep their real units -- voltage takes the top half of the
        plot, current the bottom, and the numbers on each axis stay true.
        """
        self.split = on
        self._zero_v.setVisible(on)
        self._zero_i.setVisible(on)

    def _i_to_p1(self, amps: float, vspan: float, ispan: float) -> float:
        """Where a current reading lands in the voltage ViewBox's numbers.

        The two ViewBoxes sit on top of each other, so a height in one maps
        to the same height in the other. Everything that has to be placed in
        p1 but measured in amps -- the split-mode ticks, the current peak
        label -- goes through here.
        """
        lo_i, hi_i = ispan * self.SPLIT_I[0], ispan * self.SPLIT_I[1]
        lo_v, hi_v = vspan * self.SPLIT_V[0], vspan * self.SPLIT_V[1]
        return lo_v + (amps - lo_i) / (hi_i - lo_i) * (hi_v - lo_v)

    @staticmethod
    def _step_125(x: float) -> float:
        """Smallest 1, 2 or 5 times a power of ten that reaches x."""
        if x <= 0:
            return 1.0
        mag = 10.0 ** math.floor(math.log10(x))
        for mult in (1.0, 2.0, 5.0):
            if x <= mult * mag:
                return mult * mag
        return 10.0 * mag

    @classmethod
    def _nice_span(cls, peak: float) -> float:
        """Half-height of the plot for a trace of this peak, rounded up to a
        round number.

        Snapping to a round step stops the axis range flickering as the peak
        varies slightly from block to block.
        """
        if peak <= 0:
            return 1.0
        want = peak * 1.12
        q = cls._step_125(want / 6.0)
        return q * math.ceil(want / q)

    def _y_per_px(self) -> float:
        """Height of one pixel in p1's units, for offsets that must not
        scale with the signal."""
        try:
            return abs(self.p1.vb.viewPixelSize()[1]) or 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _band_ticks(peak: float):
        """Tick marks covering only +/-peak, in a 1-2-5 sequence.

        In split mode each axis covers far more than its trace, so only the
        band the trace occupies is labelled.
        """
        if peak <= 0:
            return [[(0.0, "0")], []]
        step = ScopePane._step_125(peak / 2.0)
        vals, k = [], 0
        while k * step <= peak * 1.001:
            vals.append(k * step)
            if k:
                vals.append(-k * step)
            k += 1
        # pyqtgraph wants [major, minor], each a list of (value, label)
        return [[(v, f"{v:g}") for v in sorted(vals)], []]

    def draw(self, volts, amps, fs: int, v_unit: str, i_unit: str) -> None:
        vpk = max((abs(x) for x in volts), default=0.0) or 1e-9
        ipk = max((abs(x) for x in amps), default=0.0) or 1e-9

        xs = [k * 1000.0 / fs for k in range(len(volts))]
        self.curve_v.setData(xs, list(volts))
        self.curve_i.setData(xs, list(amps))

        # The time axis is set from the block rather than auto-ranged, so
        # hiding a trace cannot change it. It ends one sample interval after
        # the last sample so the final tick (e.g. 40 ms) is drawn, with a
        # little padding so that label is not clipped.
        if xs:
            self.p1.vb.setXRange(0.0, len(volts) * 1000.0 / fs, padding=0.015)

        v_on, i_on = self.curve_v.isVisible(), self.curve_i.isVisible()

        vspan, ispan = self._nice_span(vpk), self._nice_span(ipk)

        if self.split:
            self.p1.vb.setYRange(vspan * self.SPLIT_V[0],
                                 vspan * self.SPLIT_V[1], padding=0)
            self.p2.setYRange(ispan * self.SPLIT_I[0],
                              ispan * self.SPLIT_I[1], padding=0)
        else:
            self.p1.vb.setYRange(-vspan, vspan, padding=0)
            self.p2.setYRange(-ispan, ispan, padding=0)

        self._apply_axes(vpk, ipk, vspan, ispan, v_on, i_on)

        # Peak labels sit at the left, clear of the legend. In split mode
        # each label sits above its own band; overlaid, they stack in the top
        # corner. Each hangs from the top of the space above its trace, so
        # the waveform never draws over it.
        x0 = xs[0] + 0.012 * (xs[-1] - xs[0]) if len(xs) > 1 else 0.0
        ypx = self._y_per_px()
        v_top = vspan                            # top of the voltage band
        self._peak_v.setPos(x0, v_top - 14 * ypx)
        if self.split:
            # The same headroom above the current band as the voltage label
            # has above its own, so the pair sit at matching heights.
            i_top = self._i_to_p1(ispan, vspan, ispan)
            self._peak_i.setPos(x0, i_top - 14 * ypx)
        else:
            # One line below the voltage label, in pixels so the gap stays
            # put whatever the signal is doing -- or on the top line itself
            # when the voltage trace is hidden and that line is free.
            self._peak_i.setPos(x0, v_top - (33 if v_on else 14) * ypx)

        self._peak_v.setText(f"peak {vpk:.3g} {v_unit}")
        self._peak_i.setText(f"peak {ipk:.3g} {i_unit}")

        # A hidden trace shouldn't leave its peak label behind
        self._peak_v.setVisible(v_on)
        self._peak_i.setVisible(i_on)
        self._zero_v.setVisible(self.split and v_on)
        self._zero_i.setVisible(self.split and i_on)

    def _apply_axes(self, vpk: float, ipk: float, vspan: float, ispan: float,
                    v_on: bool, i_on: bool) -> None:
        """Decide what the two Y axes show.

        Overlaid, each trace gets its own axis and pyqtgraph picks the ticks.
        Split, both scales go on the left axis, volts against the upper band
        and amps against the lower, and the right axis is hidden.
        """
        left, right = self.p1.getAxis("left"), self.p1.getAxis("right")

        if not self.split:
            self._band_lab_v.setVisible(False)
            self._band_lab_i.setVisible(False)
            left.two_tone = False
            left.setTextPen(theme.VOLT)
            left.setWidth(None)        # back to fitting its own numbers
            left.setTicks(None)
            right.setTicks(None)
            self.setLabel("left", "voltage (V)", color=theme.VOLT)
            self.setLabel("right", "current (A)", color=theme.CURR)
            (self.p1.showAxis if v_on else self.p1.hideAxis)("left")
            (self.p1.showAxis if i_on else self.p1.hideAxis)("right")
            return

        ticks = self._band_ticks(vpk)[0] if v_on else []
        if i_on:
            for amps, text in self._band_ticks(ipk)[0]:
                ticks.append((self._i_to_p1(amps, vspan, ispan), text))

        left.two_tone = v_on and i_on
        left.setTextPen(theme.VOLT if v_on else theme.CURR)
        # A fixed width in split mode keeps the band titles clear of the
        # tick numbers, and stops the axis resizing when a trace is hidden.
        left.setWidth(self.SPLIT_AXIS_W)
        left.setTicks([ticks, []])
        left.showLabel(False)          # the band titles stand in for it
        self.p1.showAxis("left")
        self.p1.hideAxis("right")

        self._band_lab_v.setVisible(v_on)
        self._band_lab_i.setVisible(i_on)
        self._place_band_labels()

    def blank(self) -> None:
        """Remove both traces from the display."""
        self.curve_v.setData([], [])
        self.curve_i.setData([], [])
        self._peak_v.setText("")
        self._peak_i.setText("")


class MainWindow(QWidget):

    # Blocks arrive at 50 Hz, so anything past a few hundred ms of silence
    # means the link is down rather than merely slow.
    SIGNAL_TIMEOUT = 0.75

    def __init__(self, source, title: str = "Hybrid supply monitor", parent=None):
        super().__init__(parent)
        self.source = source
        self.analyzer = Analyzer(n_channels=8, cycles_shown=2)
        self.holding = False
        self._last_data = None      # monotonic time of last decoded frame
        self._blanked = False
        self.fs = 4000
        self._stats = None
        self._dc = {}

        self.scope_point = chmap.POINTS[chmap.SCOPE_POINT]

        self.setWindowTitle(title)
        self.setStyleSheet(theme.QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header(title))

        body = QHBoxLayout()
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(16)

        self.scope = ScopePane()
        self.scope.setSizePolicy(SIZE_EXPANDING, SIZE_EXPANDING)
        body.addWidget(self.scope, stretch=1)
        body.addWidget(self._build_sidebar())

        wrap = QWidget()
        wrap.setLayout(body)
        root.addWidget(wrap, stretch=1)
        self.status_bar = self._build_status()
        root.addWidget(self.status_bar)

        # Redraw on a timer rather than per block: blocks arrive at 50 Hz but
        # the panel cannot show more than ~60 fps, and decoupling the two keeps
        # the interface responsive if the link speeds up later.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(33)

        source.ac_block.connect(self._on_ac)
        source.status.connect(self._on_status)
        source.stats.connect(self._on_stats)
        source.failed.connect(self._on_failed)

        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence("Space"), self, lambda: self.btn_hold.toggle())

    def _build_header(self, title: str) -> QWidget:
        head = QWidget()
        head.setObjectName("Header")
        lay = QHBoxLayout(head)
        lay.setContentsMargins(18, 10, 18, 10)

        t = QLabel(title)
        t.setObjectName("Title")

        # The plot states peak values; these are the rms figures, which are
        # the conventional way to quote an AC quantity. Keeping them apart
        # means neither place repeats the other.
        self.vrms_read = MiniReadout("Probe voltage", " V", 3)
        self.irms_read = MiniReadout("Probe current", " A", 3)
        self.power_read = MiniReadout("Probe power", " W", 2)
        self.freq_read = MiniReadout("Frequency", " Hz", 2)

        lay.addWidget(t)
        lay.addStretch(1)
        for w in (self.vrms_read, self.irms_read, self.power_read,
                  self.freq_read):
            lay.addWidget(w)
            lay.addSpacing(26)
        return head

    def _build_sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(340)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 0, 0, 0)
        lay.setSpacing(8)

        # Inverter output power, with its own recent history
        pp = chmap.POINTS[chmap.POWER_POINT]
        self.power_tile = TrendTile(f"{pp.name} power", "W", 2)
        lay.addWidget(self.power_tile)

        # Solar and wind as voltage/current pairs
        self.summary_tiles = {}
        for idx in chmap.SUMMARY_POINTS:
            point = chmap.POINTS[idx]
            v = chmap.ac_channel(point.v_ch)
            i = chmap.ac_channel(point.i_ch)
            tile = PairTile(
                f"{point.name}, {'rms' if point.ac else 'dc'}",
                "Voltage", "Current", f" {v.unit}", f" {i.unit}")
            self.summary_tiles[idx] = tile
            lay.addWidget(tile)

        # Battery and capacitor, from the status frame. The battery is shown
        # as a voltage and the capacitor as a charge bar.
        self.batt_tile = None
        bch = chmap.dc_channel(chmap.BATTERY_DC)
        if bch is not None:
            self.batt_tile = ValueTile(bch.name, bch.unit)
            lay.addWidget(self.batt_tile)

        self.cap_tile = None
        cch = chmap.dc_channel(chmap.CAPACITOR_DC)
        if cch is not None:
            self.cap_tile = ChargeTile(cch.name, chmap.CAP_VMIN,
                                       chmap.CAP_VMAX, cch.unit)
            lay.addWidget(self.cap_tile)

        lay.addStretch(1)

        self.btn_hold = QPushButton("Hold")
        self.btn_hold.setCheckable(True)
        self.btn_hold.toggled.connect(self._on_hold)

        self.btn_span = QPushButton("2 cycles")
        self.btn_span.clicked.connect(self._cycle_span)

        self.btn_split = QPushButton("Split traces")
        self.btn_split.setCheckable(True)
        self.btn_split.toggled.connect(self.scope.set_split)

        btn_quit = QPushButton("Close")
        btn_quit.setObjectName("Danger")
        btn_quit.clicked.connect(self.close)

        rows = QGridLayout()
        rows.setSpacing(8)
        rows.addWidget(self.btn_span, 0, 0)
        rows.addWidget(self.btn_split, 0, 1)
        rows.addWidget(self.btn_hold, 1, 0)
        rows.addWidget(btn_quit, 1, 1)
        lay.addLayout(rows)
        return bar

    def _build_status(self) -> QWidget:
        """A strip that only appears when something is wrong.
        """
        bar = QWidget()
        bar.setObjectName("Status")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 7, 18, 7)
        self.link_lbl = QLabel("Waiting for data")
        self.link_lbl.setObjectName("StatusOk")
        self.err_lbl = QLabel("")
        self.err_lbl.setObjectName("StatusOk")
        lay.addWidget(self.link_lbl)
        lay.addStretch(1)
        lay.addWidget(self.err_lbl)
        bar.setVisible(False)
        return bar

    def _on_ac(self, payload) -> None:
        fs, chans = payload
        self.fs = fs
        self._last_data = time.monotonic()
        if not self.holding:
            self.analyzer.add_block(fs, chans)

    def _on_status(self, d: dict) -> None:
        # The link is alive whether or not the display is held, so the
        # timestamp is always taken -- otherwise Hold would eventually
        # declare the signal lost and blank the very reading being held.
        self._last_data = time.monotonic()
        if self.holding:
            return
        self._dc = d.get("dc", {})
        if self.batt_tile is not None:
            self.batt_tile.set_value(self._dc.get(chmap.BATTERY_DC))
        if self.cap_tile is not None:
            self.cap_tile.set_value(self._dc.get(chmap.CAPACITOR_DC))

    def _on_stats(self, s) -> None:
        self._stats = s

    def _on_failed(self, msg: str) -> None:
        self.link_lbl.setObjectName("StatusAlert")
        self.link_lbl.setStyleSheet(f"color: {theme.ALERT};")
        self.link_lbl.setText(msg)
        self.status_bar.setVisible(True)

    def _blank(self) -> None:
        """Drop everything on screen.

        Clear the traces, blank every readout, and drop the sample history so
        a reconnect starts from real data rather than splicing onto a stale
        buffer.
        """
        self.scope.blank()
        self.analyzer.clear()
        self.power_tile.clear()
        if self.batt_tile is not None:
            self.batt_tile.set_value(None)
        if self.cap_tile is not None:
            self.cap_tile.set_value(None)
        for tile in self.summary_tiles.values():
            tile.set_values(None, None)
        for read in (self.vrms_read, self.irms_read, self.power_read,
                     self.freq_read):
            read.set_value(None)

    def _refresh(self) -> None:
        now = time.monotonic()
        stale = (self._last_data is None
                 or now - self._last_data > self.SIGNAL_TIMEOUT)

        if stale:
            if not self._blanked:
                self._blank()
                self._blanked = True
            waiting = self._last_data is None
            self.link_lbl.setText("Waiting for data" if waiting else "Signal lost")
            self.link_lbl.setStyleSheet(
                f"color: {theme.MUTED if waiting else theme.ALERT};")
            self.err_lbl.setText("")
            self.status_bar.setVisible(True)
            return

        self._blanked = False

        if not self.holding:
            p = self.scope_point
            v_unit = chmap.ac_channel(p.v_ch).unit
            i_unit = chmap.ac_channel(p.i_ch).unit
            m = self.analyzer.metrics(p.v_ch, p.i_ch)

            got = self.analyzer.triggered(p.v_ch, p.i_ch)
            if got:
                v, i = got
                self.scope.draw(v, i, self.fs, v_unit, i_unit)

            self.vrms_read.set_value(m.vrms if p.ac else m.vmean)
            self.irms_read.set_value(m.irms if p.ac else m.imean)
            self.power_read.set_value(m.real_power)
            self.freq_read.set_value(m.freq if (p.ac and m.freq) else None)

            pp = chmap.POINTS[chmap.POWER_POINT]
            self.power_tile.set_value(
                self.analyzer.metrics(pp.v_ch, pp.i_ch).real_power)

            for idx, tile in self.summary_tiles.items():
                point = chmap.POINTS[idx]
                pm = self.analyzer.metrics(point.v_ch, point.i_ch)
                tile.set_values(pm.vrms if point.ac else pm.vmean,
                                pm.irms if point.ac else pm.imean)

        s = self._stats
        if s is not None:
            bad = s.lost or s.crc_errors
            if bad:
                self.link_lbl.setText(f"Receiving {s.blocks_per_s:.0f} blocks/s")
                self.link_lbl.setStyleSheet(f"color: {theme.GOOD};")
                self.err_lbl.setText(
                    f"dropped {s.lost}   crc errors {s.crc_errors}")
                self.err_lbl.setStyleSheet(f"color: {theme.ALERT};")
            self.status_bar.setVisible(bool(bad))

    def _on_hold(self, on: bool) -> None:
        self.holding = on
        self.btn_hold.setText("Resume" if on else "Hold")

    def _cycle_span(self) -> None:
        order = [1, 2, 4, 8]
        nxt = order[(order.index(self.analyzer.cycles_shown) + 1) % len(order)]
        self.analyzer.cycles_shown = nxt
        self.analyzer.history_cycles = max(6, nxt + 2)
        self.btn_span.setText(f"{nxt} cycle" + ("" if nxt == 1 else "s"))

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.source.stop()
        super().closeEvent(event)