#pragma once

#include "esp_err.h"

/*
 * Reader sounds. Timings/pitches measured from recordings of next-generation
 * Clipper (Cubic) validators, see audio.c for sources and confidence.
 */
typedef enum {
    AUDIO_CUE_OK,           /* fare paid / tag on: one 1000 Hz beep */
    AUDIO_CUE_TAG_OFF,      /* tag off: rising 1000 -> 1200 Hz pair */
    AUDIO_CUE_LOW_BALANCE,  /* paid, balance low: two beeps */
    AUDIO_CUE_REJECT,       /* not accepted: three high beeps */
    AUDIO_CUE_WARNING,      /* read error / tap again */
    AUDIO_CUE_LOCATOR,      /* quiet low-high-low chirp, every 15 s while idle */
} audio_cue_t;

esp_err_t audio_init(void);

/* Queue a cue; returns immediately. A new cue cuts off any cue still playing. */
void audio_play(audio_cue_t cue);
