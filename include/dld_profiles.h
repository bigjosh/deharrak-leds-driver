#ifndef DLD_PROFILES_H
#define DLD_PROFILES_H

// Changing the meaning of these profile IDs requires an ABI bump.
#define DLD_PROFILE_WS2812B 1
#define DLD_PROFILE_WS2811_HS 2
#define DLD_PROFILE_WS2812B_BGR 3
#define DLD_PROFILE_WS2811_HS_BGR 4
#define DLD_PRU_HZ 200000000
#define DLD_T0H_CYCLES 70
#define DLD_T1H_CYCLES 140
#define DLD_BIT_CYCLES 240
#define DLD_RESET_CYCLES 60000

// Diagnostic engineering allowances, NOT measured propagation maxima.
// All initial variants: 1 us/pixel + 100 us positive chain-end margin.
// Qualify actual pixels, wiring and reset behavior before production use.
// Wire order: ws2812b GRB, ws2811-hs RGB; -bgr variants are BGR.
#define DLD_PROPAGATION_CYCLES 200
#define DLD_SETTLE_MARGIN_CYCLES 20000
#define DLD_DRAIN_ATTEMPTS 8
#endif
