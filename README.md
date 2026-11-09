# Hybrid solar–wind microgrid demonstrator: instrumentation subsystem

Source code for the instrumentation subsystem of a hybrid solar–wind microgrid
demonstrator, developed for the REIT4841 thesis at the University of Queensland
(2026).

The subsystem measures voltage and current at four power points on the rig
(probe, solar panel, wind turbine and inverter output) and voltage at two storage
points (battery and supercapacitor). It shows them live on a touchscreen as
waveforms, RMS values, real power, frequency and state of charge. The same
microcontroller also generates the 50 Hz SPWM gate signals for the partner
subsystem's inverter.

## Hardware

- STM32 Nucleo-F446RE running Zephyr RTOS
- Two ADS131M08 24-bit ADC modules on a shared SPI bus (connected through a custom hub PCB)
- Raspberry Pi 5 with a 12.3-inch 1920 × 720 touchscreen
- UART link from the Nucleo (PB6, USART1 TX) to the Pi (GPIO 15, RX) at 921 600 baud, with a shared ground

## Repository layout

```
firmware/                Zephyr firmware for the Nucleo-F446RE
  src/main.c             start-up and acquisition loop
  src/ads131m08.c/.h     ADC driver: configuration and DRDY-paced reads
  src/telemetry.c/.h     framed binary telemetry to the Pi, with CRC-16
  src/hbridge.c/.h       50 Hz SPWM gate signals on Timer 3
  app.overlay            pin assignments (SPI, chip selects, DRDY, PWM, UART)
  prj.conf               Zephyr configuration

pi_app/                  Raspberry Pi display application (Python)
  main.py                entry point
  reader.py              serial reader thread, and the simulated demo source
  protocol.py            frame parsing, CRC check and signal analysis
  channels.py            channel map and calibration factors
  ui.py                  touchscreen interface (PySide6 + pyqtgraph)
  theme.py, qtcompat.py  colours, styles and Qt compatibility helpers
```

## Firmware

Build and flash with the Zephyr toolchain (`west`):

```
cd firmware
west build -b nucleo_f446re
west flash
```

The ADCs run at 4000 samples per second (80 samples per 50 Hz cycle). Board 0's
eight channels are streamed as one frame per 50 Hz cycle. Board 1's channels are
averaged and sent twice a second in a status frame.

## Raspberry Pi application

Install the dependencies and run on the Pi:

```
sudo apt install python3-pyside6 python3-pyqtgraph python3-serial
cd pi_app
python3 main.py --port /dev/serial0 --baud 921600 --fullscreen
```

The Pi's serial port must be enabled first (`sudo raspi-config` → Interface
Options → Serial Port: login shell **no**, serial hardware **yes**).

To run the interface without any hardware, using simulated data:

```
python3 main.py --demo
```

Channel assignments and calibration factors are set in `pi_app/channels.py`. The
firmware applies no scaling, so recalibrating needs no change to the firmware.
