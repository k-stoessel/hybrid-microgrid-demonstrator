#!/usr/bin/env python3
"""
Live monitor for the STM32F446RE hybrid rig.

On the Pi:
    sudo apt install python3-pyside6 python3-pyqtgraph python3-serial
    python3 main.py --port /dev/serial0 --baud 921600 --fullscreen

Without hardware (simulation):
    python3 main.py --demo

Calibration and channel assignment live in channels.py, not here.
"""

import argparse
import sys

from qtcompat import QT_LIB, QApplication

import theme
from reader import DemoReader, SerialReader
from ui import MainWindow


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--demo", action="store_true",
                    help="synthesise the stream instead of reading the port")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--phase", type=float, default=None,
                    help="override every demo point's current phase lag, in "
                         "degrees; 0 = purely resistive")
    args = ap.parse_args()

    print(f"Qt binding: {QT_LIB}")
    app = QApplication(sys.argv)
    app.setApplicationName("Hybrid supply monitor")
    app.setStyleSheet(theme.QSS)

    source = (DemoReader(args.phase) if args.demo
              else SerialReader(args.port, args.baud))
    win = MainWindow(source)

    if args.fullscreen:
        win.showFullScreen()
    else:
        win.resize(1600, 700)
        win.show()

    source.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())