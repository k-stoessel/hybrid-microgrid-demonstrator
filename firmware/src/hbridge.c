/*
 * SPWM gate signals for the partner subsystem's H-bridge inverter.
 *
 * Timer 3 runs a 10 kHz carrier. At the end of every carrier period the
 * interrupt loads the next duty value from a 200-entry |sin| table, so one
 * pass through the table is one 50 Hz cycle. Channel 1 is modulated during
 * the first half-cycle and channel 2 during the second, with the idle
 * channel held at 0.
 */

#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/irq.h>
#include <stm32_ll_tim.h>
#include <math.h>
#include <errno.h>

#include "hbridge.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define PWM_PERIOD_NS 100000U     /* 10 kHz */
#define SINE_SAMPLES  200         /* 10 kHz / 200 = 50 Hz output */

static uint32_t sine_tbl[SINE_SAMPLES];   /* duty in timer counts */
static volatile uint16_t sine_idx;

static const struct pwm_dt_spec legA = PWM_DT_SPEC_GET_BY_IDX(DT_PATH(zephyr_user), 0);
static const struct pwm_dt_spec legB = PWM_DT_SPEC_GET_BY_IDX(DT_PATH(zephyr_user), 1);

/* Timer 3 update interrupt: once per carrier period, load the next duty. */
static void tim3_isr(const void *arg)
{
    ARG_UNUSED(arg);

    if (!LL_TIM_IsActiveFlag_UPDATE(TIM3)) {
        return;
    }
    LL_TIM_ClearFlag_UPDATE(TIM3);

    uint32_t duty = sine_tbl[sine_idx];

    if (sine_idx < SINE_SAMPLES / 2) {
        LL_TIM_OC_SetCompareCH1(TIM3, duty);   /* leg A active */
        LL_TIM_OC_SetCompareCH2(TIM3, 0);
    } else {
        LL_TIM_OC_SetCompareCH1(TIM3, 0);
        LL_TIM_OC_SetCompareCH2(TIM3, duty);   /* leg B active */
    }

    if (++sine_idx >= SINE_SAMPLES) {
        sine_idx = 0;
    }
}

int hbridge_init(void)
{
    int ret;

    if (!pwm_is_ready_dt(&legA) || !pwm_is_ready_dt(&legB)) {
        printk("hbridge: PWM not ready\n");
        return -ENODEV;
    }

    /* let the driver configure period and enable both channels */
    ret = pwm_set_dt(&legA, PWM_PERIOD_NS, PWM_PERIOD_NS / 2);
    if (ret < 0) {
        printk("hbridge: legA set failed: %d\n", ret);
        return ret;
    }
    ret = pwm_set_dt(&legB, PWM_PERIOD_NS, PWM_PERIOD_NS / 2);
    if (ret < 0) {
        printk("hbridge: legB set failed: %d\n", ret);
        return ret;
    }

    /* build the table in timer counts, 0% to 100% of |sin| */
    uint32_t arr = LL_TIM_GetAutoReload(TIM3);
    for (int i = 0; i < SINE_SAMPLES; i++) {
        sine_tbl[i] = (uint32_t)(arr * fabs(sin(2.0 * M_PI * i / SINE_SAMPLES)));
    }
    printk("hbridge: ARR = %u\n", arr);

    sine_idx = 0;

    IRQ_CONNECT(DT_IRQN(DT_NODELABEL(timers3)), 2, tim3_isr, NULL, 0);
    irq_enable(DT_IRQN(DT_NODELABEL(timers3)));
    LL_TIM_EnableIT_UPDATE(TIM3);

    return 0;
}

void hbridge_stop(void)
{
    LL_TIM_DisableIT_UPDATE(TIM3);
    LL_TIM_OC_SetCompareCH1(TIM3, 0);
    LL_TIM_OC_SetCompareCH2(TIM3, 0);
}