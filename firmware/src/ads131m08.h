#ifndef ADS131M08_H_
#define ADS131M08_H_

#include <stdint.h>

/*
 * Number of ADC modules on the shared SPI bus. Each needs its own chip
 * select and drdy-gpios entry in app.overlay. The telemetry channel counts
 * follow from this value.
 */
#define ADS_BOARDS          2

#define ADS_CH_PER_BOARD    8
#define ADS_CHANNELS        (ADS_BOARDS * ADS_CH_PER_BOARD)

/* Boards 1 and up are read once every this many board-0 samples (10 Hz). */
#define ADS_SLOW_DIVIDER    400

int  ads_init(void);

/* Waits for board 0's next DRDY, then fills uv_out with ADS_CHANNELS
 * readings in microvolts. */
int  ads_read_channels(int32_t *uv_out);

int  ads_read_reg(uint8_t board, uint8_t addr, uint16_t *val);
int  ads_write_reg(uint8_t board, uint8_t addr, uint16_t val);
void ads_hw_reset(void);

#endif /* ADS131M08_H_ */