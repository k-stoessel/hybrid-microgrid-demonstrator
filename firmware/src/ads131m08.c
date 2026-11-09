/*
 * ADS131M08 driver for one or more boards on a shared SPI bus.
 *
 * Each frame is 10 words of 24 bits (STATUS, 8 channels, CRC), 30 bytes in
 * all. A command goes in word 1 and its response comes back in the
 * *following* frame, so every register access takes two transfers.
 * SPI mode 1 (CPOL = 0, CPHA = 1).
 *
 * Board 0's DRDY paces the acquisition loop, and board 0 is read on every
 * edge. Boards 1 and up carry only slow DC quantities, so they are read once
 * every ADS_SLOW_DIVIDER samples and their last values held in between.
 * Reading every board on every edge overran the 250 us sample interval and
 * silently lost samples.
 */

#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>
#include <string.h>
#include <errno.h>

#include "ads131m08.h"

#define USER_NODE  DT_PATH(zephyr_user)
#define FRAME_LEN  30      /* 10 words x 24 bits */

#define ADS_OP (SPI_OP_MODE_CONTROLLER | SPI_WORD_SET(8) | \
                SPI_TRANSFER_MSB | SPI_MODE_CPHA)

/* Command codes (ADS131M08 datasheet, Table 8-11) */
#define CMD_NULL           0x0000U
#define CMD_RESET          0x0011U
#define RREG_CMD(addr, n)  (0xA000U | ((addr) << 7) | ((n) - 1))
#define WREG_CMD(addr, n)  (0x6000U | ((addr) << 7) | ((n) - 1))

#define REG_ID     0x00
#define REG_STATUS 0x01
#define REG_MODE   0x02
#define REG_CLOCK  0x03

/*
 * CLOCK: 0xFF0E enables all 8 channels, OSR = 1024 (bits 4:2 = 011) and
 * high-resolution power mode. With the module's 8.192 MHz crystal that is
 * 8192000 / 2 / 1024 = 4000 Sa/s, exactly 80 samples per 50 Hz cycle.
 */
#define CLOCK_CFG  0xFF0EU

static const struct spi_dt_spec ads_spec[ADS_BOARDS] = {
	SPI_DT_SPEC_GET(DT_NODELABEL(ads0), ADS_OP),
#if ADS_BOARDS > 1
	SPI_DT_SPEC_GET(DT_NODELABEL(ads1), ADS_OP),
#endif
#if ADS_BOARDS > 2
	SPI_DT_SPEC_GET(DT_NODELABEL(ads2), ADS_OP),
#endif
};

static const struct gpio_dt_spec drdy[ADS_BOARDS] = {
	GPIO_DT_SPEC_GET_BY_IDX(USER_NODE, drdy_gpios, 0),
#if ADS_BOARDS > 1
	GPIO_DT_SPEC_GET_BY_IDX(USER_NODE, drdy_gpios, 1),
#endif
#if ADS_BOARDS > 2
	GPIO_DT_SPEC_GET_BY_IDX(USER_NODE, drdy_gpios, 2),
#endif
};

/* One reset line driving every board */
static const struct gpio_dt_spec rst = GPIO_DT_SPEC_GET(USER_NODE, rst_gpios);

static struct gpio_callback drdy_cb;
static K_SEM_DEFINE(drdy_sem, 0, 2);

static uint8_t tx[FRAME_LEN];
static uint8_t rx[FRAME_LEN];

static void drdy_handler(const struct device *port, struct gpio_callback *cb,
			 gpio_port_pins_t pins)
{
	ARG_UNUSED(port); ARG_UNUSED(cb); ARG_UNUSED(pins);
	k_sem_give(&drdy_sem);
}

static int ads_frame(uint8_t board)
{
	struct spi_buf txb = { .buf = tx, .len = FRAME_LEN };
	struct spi_buf rxb = { .buf = rx, .len = FRAME_LEN };
	struct spi_buf_set tx_set = { .buffers = &txb, .count = 1 };
	struct spi_buf_set rx_set = { .buffers = &rxb, .count = 1 };

	return spi_transceive_dt(&ads_spec[board], &tx_set, &rx_set);
}

void ads_hw_reset(void)
{
	gpio_pin_set_dt(&rst, 1);      /* assert (drives low) */
	k_usleep(400);                 /* 2048 tCLKIN is ~250 us at 8.192 MHz */
	gpio_pin_set_dt(&rst, 0);      /* release */
	k_msleep(1);
}

int ads_read_reg(uint8_t board, uint8_t addr, uint16_t *val)
{
	uint16_t cmd = RREG_CMD(addr, 1);
	int ret;

	if (board >= ADS_BOARDS) {
		return -EINVAL;
	}

	memset(tx, 0, sizeof(tx));
	tx[0] = cmd >> 8;
	tx[1] = cmd & 0xFF;
	ret = ads_frame(board);
	if (ret < 0) {
		return ret;
	}

	k_usleep(50);                  /* tREGACQ is 5 us */

	memset(tx, 0, sizeof(tx));     /* NULL command collects the response */
	ret = ads_frame(board);
	if (ret < 0) {
		return ret;
	}

	*val = ((uint16_t)rx[0] << 8) | rx[1];
	return 0;
}

int ads_write_reg(uint8_t board, uint8_t addr, uint16_t val)
{
	uint16_t cmd = WREG_CMD(addr, 1);

	if (board >= ADS_BOARDS) {
		return -EINVAL;
	}

	memset(tx, 0, sizeof(tx));
	tx[0] = cmd >> 8;
	tx[1] = cmd & 0xFF;
	tx[3] = val >> 8;              /* data word, MSB aligned in word 2 */
	tx[4] = val & 0xFF;

	return ads_frame(board);
}

/* Decode the 8 channels sitting in rx[] into microvolts */
static void decode_frame(int32_t *uv_out)
{
	for (int ch = 0; ch < ADS_CH_PER_BOARD; ch++) {
		int off = 3 + ch * 3;      /* skip STATUS, 3 bytes per channel */
		int32_t v = ((int32_t)rx[off] << 16) |
			    ((int32_t)rx[off + 1] << 8) |
			     (int32_t)rx[off + 2];

		if (v & 0x800000) {
			v |= 0xFF000000;   /* sign extend 24 -> 32 bit */
		}

		/* +/-1.2 V full scale at gain 1, 2^23 counts per full scale */
		uv_out[ch] = (int32_t)(((int64_t)v * 1200000) / 8388608);
	}
}

int ads_read_channels(int32_t *uv_out)
{
	int ret;

	k_sem_take(&drdy_sem, K_FOREVER);

	memset(tx, 0, sizeof(tx));           /* NULL command */
	ret = ads_frame(0);
	if (ret < 0) {
		return ret;
	}
	decode_frame(uv_out);

#if ADS_BOARDS > 1
	/*
	 * Boards 1 and up are read on a rate divider and their last values
	 * held in between, so an ordinary sample costs one SPI transfer
	 * instead of one per board.
	 */
	static int32_t slow[(ADS_BOARDS - 1) * ADS_CH_PER_BOARD];
	static uint32_t tick;

	if (++tick >= ADS_SLOW_DIVIDER) {
		tick = 0;
		for (uint8_t b = 1; b < ADS_BOARDS; b++) {
			memset(tx, 0, sizeof(tx));
			ret = ads_frame(b);
			if (ret < 0) {
				return ret;
			}
			decode_frame(slow + (b - 1) * ADS_CH_PER_BOARD);
		}
	}

	/* The caller always gets a full set, fresh or held */
	memcpy(uv_out + ADS_CH_PER_BOARD, slow, sizeof(slow));
#endif

	return 0;
}

static int board_init(uint8_t b)
{
	uint16_t id = 0, clk = 0;
	int ret;

	if (!spi_is_ready_dt(&ads_spec[b])) {
		printk("ads%u: SPI not ready\n", b);
		return -ENODEV;
	}

	/* Software reset, then let it settle */
	memset(tx, 0, sizeof(tx));
	tx[1] = CMD_RESET & 0xFF;
	ads_frame(b);
	k_msleep(1);

	/*
	 * Drain the two-deep output FIFO with two back-to-back frames. Left
	 * full, DRDY fires at half the real rate.
	 */
	memset(tx, 0, sizeof(tx));
	ads_frame(b);
	ads_frame(b);

	ret = ads_read_reg(b, REG_ID, &id);
	if (ret < 0) {
		return ret;
	}
	if ((id & 0xFF00) != 0x2800) {
		printk("ads%u: bad ID 0x%04X (expected 0x28xx)\n", b, id);
		return -EIO;
	}

	ret = ads_write_reg(b, REG_CLOCK, CLOCK_CFG);
	if (ret < 0) {
		return ret;
	}
	k_usleep(50);

	ret = ads_read_reg(b, REG_CLOCK, &clk);
	if (ret < 0) {
		return ret;
	}
	if (clk != CLOCK_CFG) {
		printk("ads%u: CLOCK readback 0x%04X, wanted 0x%04X\n",
		       b, clk, CLOCK_CFG);
		return -EIO;
	}

	printk("ads%u: ID 0x%04X, CLOCK 0x%04X\n", b, id, clk);
	return 0;
}

int ads_init(void)
{
	int ret;

	if (!gpio_is_ready_dt(&rst)) {
		printk("ads: reset gpio not ready\n");
		return -ENODEV;
	}

	ret = gpio_pin_configure_dt(&rst, GPIO_OUTPUT_INACTIVE);
	if (ret < 0) {
		return ret;
	}

	ads_hw_reset();

	for (int b = 0; b < ADS_BOARDS; b++) {
		if (!gpio_is_ready_dt(&drdy[b])) {
			printk("ads%d: drdy gpio not ready\n", b);
			return -ENODEV;
		}
		ret = gpio_pin_configure_dt(&drdy[b], GPIO_INPUT);
		if (ret < 0) {
			return ret;
		}
	}

	/*
	 * Only board 0's DRDY raises an interrupt; it paces the acquisition
	 * loop and the other boards are read alongside it.
	 */
	ret = gpio_pin_interrupt_configure_dt(&drdy[0], GPIO_INT_EDGE_TO_ACTIVE);
	if (ret < 0) {
		return ret;
	}
	gpio_init_callback(&drdy_cb, drdy_handler, BIT(drdy[0].pin));
	gpio_add_callback(drdy[0].port, &drdy_cb);

	k_msleep(10);                  /* let the ADCs finish power-on reset */

	for (uint8_t b = 0; b < ADS_BOARDS; b++) {
		ret = board_init(b);
		if (ret < 0) {
			return ret;
		}
	}

	k_sem_reset(&drdy_sem);        /* discard any stale DRDY event */

	return 0;
}