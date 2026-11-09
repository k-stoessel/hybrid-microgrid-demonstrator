#ifndef TELEMETRY_H_
#define TELEMETRY_H_

#include <stdint.h>

#include "ads131m08.h"

/*
 * Binary framed telemetry to the Raspberry Pi over USART1. The frame format
 * is described in telemetry.c.
 *
 * Board 0's eight channels are streamed as waveforms. Channels on boards 1
 * and up carry slow DC quantities (battery, capacitor), so they are averaged
 * and sent twice a second in the status frame. This keeps the link at about
 * 65 kB/s; streaming all 16 channels would need about 1.3 Mbaud.
 */

#define TELEM_AC_CHANNELS   ADS_CH_PER_BOARD
#define TELEM_DC_CHANNELS   (ADS_CHANNELS - ADS_CH_PER_BOARD)

int  telem_init(void);

/* uv points at ADS_CHANNELS readings in microvolts. Never blocks. */
int  telem_push(const int32_t *uv);

/* Call freely from the acquisition loop; rate-limits itself to 2 Hz. */
void telem_status_tick(void);

/* Count a failed ADC read so it shows up in the status frame. */
void telem_note_error(void);

#endif /* TELEMETRY_H_ */