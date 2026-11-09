/*
 * Start-up and acquisition loop. Each pass waits for the next ADC sample
 * (every 250 us), queues it for the Pi and sends a status frame when due.
 * If any initialisation step fails, the error is printed and the firmware
 * stops.
 */

#include <zephyr/kernel.h>

#include "ads131m08.h"
#include "hbridge.h"
#include "telemetry.h"

/*
 * Set to 1 to print a set of samples to the console every 400 samples
 * instead of streaming them. Useful for bring-up only: printk is too slow
 * to leave on while the 250 us acquisition loop is running.
 */
#define DEBUG_PRINT  0

int main(void)
{
	int32_t uv[ADS_CHANNELS];

	if (hbridge_init() < 0) {
		printk("hbridge init failed\n");
		return 0;
	}

	if (ads_init() < 0) {
		printk("ads init failed\n");
		return 0;
	}

	if (telem_init() < 0) {
		printk("telem init failed\n");
		return 0;
	}

	while (1) {
		if (ads_read_channels(uv) < 0) {
			telem_note_error();
			continue;
		}

#if DEBUG_PRINT
		static uint32_t n;

		if ((n++ % 400) == 0) {
			for (int ch = 0; ch < ADS_CHANNELS; ch++) {
				printk("%d%s", uv[ch] / 100,
				       ch < ADS_CHANNELS - 1 ? "," : "\n");
			}
		}
#else
		telem_push(uv);
		telem_status_tick();
#endif
	}

	return 0;
}