/*
 * Reader beep synthesizer: cues are sequences of pure sine tones rendered on
 * the fly into the I2S speaker channel.
 *
 * Measured from recordings (FFT; pitch +-1 Hz, timing +-10 ms):
 *   OK       1000 Hz 205 ms - next-gen Cubic validators, many recordings
 *   TAG OFF  1000 Hz 200 ms, 46 ms gap, 1200 Hz 205 ms - next-gen, tap-offs
 *   LOCATOR  1000/1200/1000 Hz 93/105/93 ms, gaps 61/42 ms, ~-12 dB -
 *            clippercard.com accessibility sample (legacy reader)
 *   REJECT   3 x 1400 Hz 115 ms, 53 ms gaps - legacy reader error tone;
 *            no next-gen recording found, official docs say "three beeps"
 * Inferred (no recording found):
 *   LOW BALANCE  2 x 1000 Hz 200 ms, 50 ms gap - docs say "two beeps"
 *   WARNING      2 x 1400 Hz (reject timing) - Cubic manual: separate
 *                "warning" tone for try-again / one-card-only
 * The recordings are near-pure sines (3rd harmonic 30-40 dB down).
 */
#include "audio.h"

#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_log.h"

#include "board.h"
#include "settings.h"

static const char *TAG = "audio";

#define SAMPLE_RATE     24000
#define CHUNK_FRAMES    240         /* 10 ms */
#define RAMP_MS         5           /* fade in/out, avoids clicks */
#define MAX_SEGMENTS    8

typedef struct {
    float freq_hz;      /* 0 = silence */
    uint32_t ms;
    float gain;
} segment_t;

#define TONE(f, ms, g) {(f), (ms), (g)}
#define GAP(ms)        {0, (ms), 0}
#define LOCATOR_GAIN   0.5f        /* ~ -12 dB */

typedef struct {
    int n;
    segment_t seg[MAX_SEGMENTS];
} cue_def_t;

static const cue_def_t CUES[] = {
    [AUDIO_CUE_OK]          = {1, {TONE(1000, 205, 1)}},
    [AUDIO_CUE_TAG_OFF]     = {3, {TONE(1000, 200, 1), GAP(46), TONE(1200, 205, 1)}},
    [AUDIO_CUE_LOW_BALANCE] = {3, {TONE(1000, 200, 1), GAP(50), TONE(1000, 200, 1)}},
    [AUDIO_CUE_REJECT]      = {5, {TONE(1400, 115, 1), GAP(53), TONE(1400, 115, 1), GAP(53),
                                   TONE(1400, 115, 1)}},
    [AUDIO_CUE_WARNING]     = {3, {TONE(1400, 115, 1), GAP(53), TONE(1400, 115, 1)}},
    [AUDIO_CUE_LOCATOR]     = {5, {TONE(1000, 93, LOCATOR_GAIN * 0.6f), GAP(61),
                                   TONE(1200, 105, LOCATOR_GAIN), GAP(42),
                                   TONE(1000, 93, LOCATOR_GAIN * 0.6f)}},
};

static i2s_chan_handle_t s_tx;
static QueueHandle_t s_queue;
static int16_t s_chunk[CHUNK_FRAMES * 2];

/* Returns false if a newer cue arrived and this one should be abandoned. */
static bool play_segment(const segment_t *seg, float amplitude)
{
    const uint32_t total = seg->ms * SAMPLE_RATE / 1000;
    const uint32_t ramp = RAMP_MS * SAMPLE_RATE / 1000;
    const float step = 2.0f * (float)M_PI * seg->freq_hz / SAMPLE_RATE;
    float phase = 0;

    for (uint32_t done = 0; done < total;) {
        if (uxQueueMessagesWaiting(s_queue)) {
            return false;
        }
        uint32_t frames = total - done;
        if (frames > CHUNK_FRAMES) {
            frames = CHUNK_FRAMES;
        }
        for (uint32_t i = 0; i < frames; i++) {
            int16_t v = 0;
            if (seg->freq_hz > 0) {
                uint32_t t = done + i;
                float env = 1.0f;
                if (t < ramp) {
                    env = (float)t / ramp;
                } else if (t > total - ramp) {
                    env = (float)(total - t) / ramp;
                }
                v = (int16_t)(sinf(phase) * env * amplitude * seg->gain);
                phase += step;
                if (phase > 2.0f * (float)M_PI) {
                    phase -= 2.0f * (float)M_PI;
                }
            }
            s_chunk[2 * i] = v;
            s_chunk[2 * i + 1] = v;
        }
        size_t written;
        i2s_channel_write(s_tx, s_chunk, frames * 2 * sizeof(int16_t), &written, portMAX_DELAY);
        done += frames;
    }
    return true;
}

static void audio_task(void *arg)
{
    audio_cue_t cue;
    static const segment_t tail = GAP(30); /* let the DMA drain before the next cue */

    while (true) {
        xQueueReceive(s_queue, &cue, portMAX_DELAY);

        /* Perceptual volume curve; leave a little headroom for the amp. */
        const float v = g_settings.volume / 100.0f;
        const float amplitude = 30000.0f * v * v;

        const cue_def_t *c = &CUES[cue];
        bool done = true;
        for (int i = 0; i < c->n && done; i++) {
            done = play_segment(&c->seg[i], amplitude);
        }
        if (done) {
            play_segment(&tail, amplitude);
        }
    }
}

esp_err_t audio_init(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 4;
    chan_cfg.dma_frame_num = CHUNK_FRAMES;
    chan_cfg.auto_clear = true;  /* output silence when we stop feeding it */
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_tx, NULL), TAG, "i2s channel");

    const i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = BOARD_I2S_GPIO_BCLK,
            .ws = BOARD_I2S_GPIO_WS,
            .dout = BOARD_I2S_GPIO_DOUT,
            .din = I2S_GPIO_UNUSED,
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_tx, &std_cfg), TAG, "i2s std");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_tx), TAG, "i2s enable");

    /* Keep the amp on permanently: toggling it per cue pops and adds latency. */
    ESP_RETURN_ON_ERROR(board_set_amp(true), TAG, "amp");

    s_queue = xQueueCreate(4, sizeof(audio_cue_t));
    ESP_RETURN_ON_FALSE(s_queue, ESP_ERR_NO_MEM, TAG, "queue");
    BaseType_t ok = xTaskCreate(audio_task, "audio", 4096, NULL, 6, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}

void audio_play(audio_cue_t cue)
{
    if (s_queue) {
        xQueueSend(s_queue, &cue, 0);
    }
}
