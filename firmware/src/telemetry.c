/*
 * Telemetry link: Nucleo-F446RE -> Raspberry Pi, binary frames on USART1.
 *
 * Frame header (little-endian, native on both the STM32 and the Pi):
 *
 *   offset  size  field
 *   0       2     sync = 0x5AA5
 *   2       1     type (1 = AC block, 2 = status)
 *   3       1     version = 2
 *   4       2     payload_len
 *   6       4     seq              (per-type counter, lets the Pi spot drops)
 *   10      N     payload
 *   10+N    2     CRC-16/CCITT-FALSE over bytes [0 .. 10+N-1]
 *
 * AC payload (type 1):
 *   0       2     n_samples
 *   2       2     sample_rate_hz
 *   4       1     n_channels
 *   5       1     reserved
 *   6       ...   n_samples * n_channels int16, sample-major,
 *                 in units of 100 uV at the ADC input
 *
 * Status payload (type 2):
 *   0       4     uptime_ms
 *   4       4     seq_ac
 *   8       4     dropped_frames
 *   12      4     sample_errors
 *   16      2     adc_status
 *   18      1     n_dc
 *   19      1     reserved
 *   20      ...   n_dc int32, mean microvolts, boards 1+ channels in order
 *
 * The MCU applies no scaling: it sends the voltage at each ADC pin, and the
 * Pi's channels.py decides what each channel measures and how to scale it.
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/ring_buffer.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>

#include "telemetry.h"

#define UART_NODE          DT_NODELABEL(usart1)

/* Must match the rate set by CLOCK_CFG in ads131m08.c. */
#define SAMPLE_RATE_HZ     4000

/* 80 samples at 4000 Sa/s is exactly one 50 Hz cycle per frame. */
#define BLOCK_SAMPLES      80

/* Must hold several whole frames. One AC frame is 1298 B at 8 channels. */
#define TX_RING_SIZE       8192

#define STATUS_PERIOD_MS   500

#define FRAME_SYNC         0x5AA5u
#define FRAME_VERSION      2
#define FRAME_TYPE_AC      1
#define FRAME_TYPE_STATUS  2

struct frame_hdr {
	uint16_t sync;
	uint8_t  type;
	uint8_t  version;
	uint16_t payload_len;
	uint32_t seq;
} __packed;

struct ac_hdr {
	uint16_t n_samples;
	uint16_t sample_rate_hz;
	uint8_t  n_channels;
	uint8_t  reserved;
} __packed;

/* Zero-length arrays are not legal C, so the DC slot always has room for at
 * least one entry; n_dc says how many are actually present and payload_len
 * is computed from it. */
#define DC_SLOTS  (TELEM_DC_CHANNELS > 0 ? TELEM_DC_CHANNELS : 1)

struct status_payload {
	uint32_t uptime_ms;
	uint32_t seq_ac;
	uint32_t dropped_frames;
	uint32_t sample_errors;
	uint16_t adc_status;
	uint8_t  n_dc;
	uint8_t  reserved;
	int32_t  dc_uv[DC_SLOTS];
} __packed;

#define AC_PAYLOAD_LEN  (sizeof(struct ac_hdr) + \
			 (size_t)BLOCK_SAMPLES * TELEM_AC_CHANNELS * sizeof(int16_t))
#define AC_FRAME_LEN    (sizeof(struct frame_hdr) + AC_PAYLOAD_LEN + 2)

#define ST_PAYLOAD_LEN  (offsetof(struct status_payload, dc_uv) + \
			 (size_t)TELEM_DC_CHANNELS * sizeof(int32_t))
#define ST_FRAME_LEN    (sizeof(struct frame_hdr) + ST_PAYLOAD_LEN + 2)

#define ST_BUF_LEN      (sizeof(struct frame_hdr) + \
			 sizeof(struct status_payload) + 2)

static const struct device *const uart_dev = DEVICE_DT_GET(UART_NODE);

RING_BUF_DECLARE(tx_rb, TX_RING_SIZE);

/* The AC frame is built in place: samples are written straight into the
 * payload area as they arrive, then the header and CRC are added. */
static uint8_t ac_buf[AC_FRAME_LEN] __aligned(4);
static uint8_t st_buf[ST_BUF_LEN] __aligned(4);

static int16_t *const ac_samples =
	(int16_t *)(ac_buf + sizeof(struct frame_hdr) + sizeof(struct ac_hdr));

static uint32_t fill;              /* samples accumulated in current block */
static uint32_t seq_ac;
static uint32_t seq_status;
static uint32_t dropped_frames;
static uint32_t sample_errors;
static int64_t  last_status;

/* Running mean of the boards 1+ channels, reset each status frame */
#if TELEM_DC_CHANNELS > 0
static int64_t  dc_sum[TELEM_DC_CHANNELS];
static uint32_t dc_count;
#endif

/*
 * CRC-16/CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR.
 * The 16-entry table processes four bits per step: faster than working one
 * bit at a time, and much smaller than a 256-entry table. This runs in the
 * acquisition loop, so it needs to be quick. Check value for "123456789"
 * is 0x29B1.
 */
static const uint16_t crc_tab[16] = {
	0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
	0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
};

static uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
	uint16_t crc = 0xFFFF;

	for (size_t i = 0; i < len; i++) {
		crc = (uint16_t)((crc << 4) ^
				 crc_tab[((crc >> 12) ^ (data[i] >> 4)) & 0x0F]);
		crc = (uint16_t)((crc << 4) ^
				 crc_tab[((crc >> 12) ^ (data[i] & 0x0F)) & 0x0F]);
	}
	return crc;
}

/* UART transmit: interrupt driven, fed from the ring buffer. */

static void uart_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);

	uart_irq_update(dev);

	while (uart_irq_tx_ready(dev)) {
		uint8_t *chunk;
		uint32_t avail = MIN(ring_buf_get_ptr(&tx_rb, &chunk, 0), 64);

		if (avail == 0) {
			uart_irq_tx_disable(dev);
			break;
		}

		int sent = uart_fifo_fill(dev, chunk, (int)avail);

		if (sent > 0) {
			ring_buf_consume(&tx_rb, (uint32_t)sent);
		}

		if (sent < (int)avail) {
			break;   /* hardware FIFO full, we'll be called again */
		}
	}
}

/*
 * Frames go in whole or not at all. A partial frame would desynchronise the
 * host, so if the ring is full we discard and count instead.
 */
static int frame_queue(const uint8_t *buf, size_t len)
{
	if (ring_buf_space_get(&tx_rb) < len) {
		dropped_frames++;
		return -ENOMEM;
	}

	ring_buf_put(&tx_rb, buf, len);
	uart_irq_tx_enable(uart_dev);

	return 0;
}

/*
 * Microvolts at the ADC input -> int16 in units of 100 uV.
 *
 * The +/-1.2 V input range maps to +/-12000 of the available +/-32767.
 * Sending int16 rather than int32 halves the data rate on the link.
 */
static inline int16_t to_code(int32_t uv)
{
	int32_t c = uv / 100;

	if (c > 32767) {
		return 32767;
	}
	if (c < -32768) {
		return -32768;
	}
	return (int16_t)c;
}

static int ac_flush(void)
{
	struct frame_hdr *h = (struct frame_hdr *)ac_buf;
	struct ac_hdr *a = (struct ac_hdr *)(ac_buf + sizeof(*h));
	uint16_t crc;

	h->sync        = FRAME_SYNC;
	h->type        = FRAME_TYPE_AC;
	h->version     = FRAME_VERSION;
	h->payload_len = (uint16_t)AC_PAYLOAD_LEN;
	h->seq         = seq_ac++;

	a->n_samples      = BLOCK_SAMPLES;
	a->sample_rate_hz = SAMPLE_RATE_HZ;
	a->n_channels     = TELEM_AC_CHANNELS;
	a->reserved       = 0;

	crc = crc16_ccitt_false(ac_buf, AC_FRAME_LEN - 2);
	ac_buf[AC_FRAME_LEN - 2] = (uint8_t)(crc & 0xFF);
	ac_buf[AC_FRAME_LEN - 1] = (uint8_t)(crc >> 8);

	return frame_queue(ac_buf, AC_FRAME_LEN);
}

int telem_push(const int32_t *uv)
{
	int16_t *dst = ac_samples + (size_t)fill * TELEM_AC_CHANNELS;

	for (int ch = 0; ch < TELEM_AC_CHANNELS; ch++) {
		dst[ch] = to_code(uv[ch]);
	}

#if TELEM_DC_CHANNELS > 0
	for (int ch = 0; ch < TELEM_DC_CHANNELS; ch++) {
		dc_sum[ch] += uv[TELEM_AC_CHANNELS + ch];
	}
	dc_count++;
#endif

	if (++fill < BLOCK_SAMPLES) {
		return 0;
	}

	fill = 0;
	return ac_flush();
}

void telem_status_tick(void)
{
	struct frame_hdr *h = (struct frame_hdr *)st_buf;
	struct status_payload *p =
		(struct status_payload *)(st_buf + sizeof(*h));
	int64_t now = k_uptime_get();
	uint16_t crc;

	if (now - last_status < STATUS_PERIOD_MS) {
		return;
	}
	last_status = now;

	h->sync        = FRAME_SYNC;
	h->type        = FRAME_TYPE_STATUS;
	h->version     = FRAME_VERSION;
	h->payload_len = (uint16_t)ST_PAYLOAD_LEN;
	h->seq         = seq_status++;

	p->uptime_ms      = (uint32_t)now;
	p->seq_ac         = seq_ac;
	p->dropped_frames = dropped_frames;
	p->sample_errors  = sample_errors;
	p->adc_status     = 0;    /* not used, always 0 */
	p->n_dc           = TELEM_DC_CHANNELS;
	p->reserved       = 0;

#if TELEM_DC_CHANNELS > 0
	for (int ch = 0; ch < TELEM_DC_CHANNELS; ch++) {
		p->dc_uv[ch] = dc_count ? (int32_t)(dc_sum[ch] / dc_count) : 0;
		dc_sum[ch] = 0;
	}
	dc_count = 0;
#endif

	crc = crc16_ccitt_false(st_buf, ST_FRAME_LEN - 2);
	st_buf[ST_FRAME_LEN - 2] = (uint8_t)(crc & 0xFF);
	st_buf[ST_FRAME_LEN - 1] = (uint8_t)(crc >> 8);

	frame_queue(st_buf, ST_FRAME_LEN);
}

void telem_note_error(void)
{
	sample_errors++;
}

int telem_init(void)
{
	unsigned int fps = SAMPLE_RATE_HZ / BLOCK_SAMPLES;

	if (!device_is_ready(uart_dev)) {
		printk("telem: USART1 not ready\n");
		return -ENODEV;
	}

	uart_irq_rx_disable(uart_dev);
	uart_irq_tx_disable(uart_dev);
	uart_irq_callback_user_data_set(uart_dev, uart_isr, NULL);

	fill           = 0;
	seq_ac         = 0;
	seq_status     = 0;
	dropped_frames = 0;
	sample_errors  = 0;
	last_status    = k_uptime_get();

	printk("telem: %u ac ch + %u dc ch @ %u Sa/s\n",
	       TELEM_AC_CHANNELS, TELEM_DC_CHANNELS, SAMPLE_RATE_HZ);
	printk("telem: %u B frames, %u frames/s, %u B/s\n",
	       (unsigned)AC_FRAME_LEN, fps, (unsigned)(AC_FRAME_LEN * fps));

	return 0;
}