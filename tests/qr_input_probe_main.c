/*
 * 実QINPUT・入力・フォント・ヘッダー・符号化器を試すホスト境界。
 * 大きな画面領域や観測用領域はDOS実行ファイルへ含めない。
 */
/* Host-only boundary for the real QINPUT main, input, font, header and encoder.
 * Large planes and observations below are never linked into the DOS EXE.
 */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/manual_selection.h"
#include "../src/pc98_key_input.h"
#include "../src/pc98_status_font.h"
#include "../src/frame_header.h"
#include "../src/qr_encode.h"

static int observed_init(struct pc98qr_manual_selection *, unsigned short);
static int observed_input(struct pc98qr_manual_selection *, unsigned int);
static int observed_poll(unsigned short *);
static unsigned int observed_classify(unsigned short);
static int observed_header(unsigned short, unsigned short, unsigned char *);
static int observed_encode(const unsigned char *, unsigned int, unsigned char *);
static int observed_fixed(void);
static int observed_text(unsigned int, unsigned int, const char *, unsigned int);
#define pc98qr_manual_selection_init observed_init
#define pc98qr_manual_selection_input observed_input
#define pc98qr_key_poll observed_poll
#define pc98qr_key_classify observed_classify
#define pc98qr_frame_header_serialize observed_header
#define qr_encode_v40l_bytes observed_encode
#define pc98qr_status_fixed observed_fixed
#define pc98qr_status_text observed_text
#define main qinput_main
#include "../tools/qr_input_probe.c"
#undef main
#undef pc98qr_manual_selection_init
#undef pc98qr_manual_selection_input
#undef pc98qr_key_poll
#undef pc98qr_key_classify
#undef pc98qr_frame_header_serialize
#undef qr_encode_v40l_bytes
#undef pc98qr_status_fixed
#undef pc98qr_status_text

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"
#define PC98QR_STATUS_FONT_HOST_TEST
#include "../src/pc98_status_font.c"

enum phase { BOOT, STATE, HEADER, ENCODED, INIT, QR, INITIAL, READY,
             VISIBLE, POLLED, CLASSIFIED, INPUT, UPDATE, FAILED, DONE };
static enum phase phase;
static struct {
    unsigned char before[32];
    unsigned char bytes[32000];
    unsigned char after[32];
} planes[4];
static unsigned char before_planes[4][32000], stores[4][32000];
static unsigned char saved_matrix[QR_V40_PACKED_BYTES];
static unsigned char saved_frame[QR_V40L_BYTE_MAX];
static struct pc98qr_manual_selection saved_state;
static int state_initialized;
static char displayed[5] = {'-', ' ', ' ', ' ', ' '};
static char submitted[5] = {'-', ' ', ' ', ' ', ' '};
static const char *events, *fault;
static char *pending_end;
static unsigned short pending_ax;
static int queued, event_open, encoded;
static int poll_result, input_result, event_ax;
static unsigned int event_key, event_updates, event_completed;
static unsigned int state_inits, headers, encodes, inits, clears, qr_passes, boxes;
static unsigned int initials, shows, polls, peeks, reads, classifications, inputs;
static unsigned int updates, completed, shutdowns, snapshots, writes[4];
static char trace[32768];

static void record(const char *name)
{
    assert(strlen(trace) + strlen(name) + 2 < sizeof(trace));
    if (*trace) strcat(trace, ",");
    strcat(trace, name);
}

static int failing(const char *name)
{
    return strcmp(fault, name) == 0;
}

static int failing_number(const char *name, unsigned int number)
{
    char label[32];
    assert(snprintf(label, sizeof(label), "%s%u", name, number) < (int)sizeof(label));
    return failing(label);
}

static void check_memory(void)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p)
        for (i = 0; i < 32; ++i) {
            assert(planes[p].before[i] == 0xA5);
            assert(planes[p].after[i] == 0x5A);
        }
    if (encoded) {
        assert(memcmp(fixed_frame, saved_frame, sizeof(saved_frame)) == 0);
        assert(memcmp(fixed_matrix, saved_matrix, sizeof(saved_matrix)) == 0);
    }
}

/*
 * API引数を観察し、mainのローカルポインタは保持しない。
 * 入力状態は前回結果と比較し、フォント読み取り前に実表示5バイトを確認する。
 */
/* Observe through API arguments; never retain pointers to main's locals.
 * observed_input compares the next incoming state against the last result.
 * observed_text checks all five actual display bytes before the font reads.
 */
static void check_state(void)
{
    if (state_initialized) {
        assert(saved_state.length <= 5 && saved_state.frame_count == 24);
        assert(saved_state.digits[saved_state.length] == '\0');
        assert(strlen(saved_state.digits) == saved_state.length);
        assert(saved_state.selected_number <= 24);
    }
    check_memory();
}

static int update_byte(unsigned int offset)
{
    unsigned int row = offset / 80, col = offset % 80;
    return row >= 96 && row < 112 && col >= 65 && col < 70;
}

static int initial_byte(unsigned int offset)
{
    unsigned int row = offset / 80, col = offset % 80;
    if (col < 65) return 0;
    return (row >= 32 && row < 48 && col < 72)
        || (row >= 48 && row < 64 && col < 69)
        || (row >= 80 && row < 96 && col < 71)
        || (row >= 96 && row < 112 && col == 65);
}

static void start_stores(void)
{
    unsigned int p;
    memset(stores, 0, sizeof(stores));
    memset(writes, 0, sizeof(writes));
    for (p = 0; p < 4; ++p) memcpy(before_planes[p], planes[p].bytes, 32000);
}

static void check_stores(int initial, int drew)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p) {
        assert(writes[p] == (drew ? (initial ? 288u : 80u) : 0u));
        for (i = 0; i < 32000; ++i) {
            unsigned int expected = drew && (initial ? initial_byte(i) : update_byte(i));
            assert(stores[p][i] == expected);
            if (!expected) assert(planes[p].bytes[i] == before_planes[p][i]);
        }
    }
    check_memory();
}

void pc98qr_status_font_store(uint16_t segment, uint16_t offset, uint8_t value)
{
    unsigned int p;
    switch (segment) {
    case 0xA800: p = 0; break;
    case 0xB000: p = 1; break;
    case 0xB800: p = 2; break;
    case 0xE000: p = 3; break;
    default: assert(0); return;
    }
    assert(phase == INITIAL || phase == UPDATE);
    assert(offset < 32000);
    assert(phase == INITIAL ? initial_byte(offset) : update_byte(offset));
    assert(stores[p][offset] == 0);
    ++stores[p][offset];
    ++writes[p];
    planes[p].bytes[offset] = value;
}

static void output_bytes(const void *bytes, size_t length)
{
    assert(fwrite(bytes, 1, length, stdout) == length);
}

static void output_planes(void)
{
    unsigned int p;
    for (p = 0; p < 4; ++p) output_bytes(planes[p].bytes, 32000);
}

static void snapshot(int initial)
{
    check_state();
    printf("{\"kind\":\"snapshot\",\"initial\":%d,\"poll\":%u,\"poll_result\":%d,"
           "\"ax\":%d,\"key\":%u,\"result\":%d,\"digits\":\"%s\",\"length\":%u,"
           "\"selected\":%u,\"error\":%u,\"exited\":%u,\"display\":\"%.5s\","
           "\"buffer\":\"%.5s\",\"writes\":[%u,%u,%u,%u]}\n",
           initial, polls, initial ? -1 : poll_result, initial ? -1 : event_ax,
           initial ? 0 : event_key, initial ? 0 : input_result, saved_state.digits,
           saved_state.length, saved_state.selected_number, saved_state.error,
           saved_state.exited, displayed, submitted, writes[0], writes[1], writes[2], writes[3]);
    output_planes();
    output_bytes(fixed_matrix, sizeof(fixed_matrix));
    output_bytes(fixed_frame, sizeof(fixed_frame));
    ++snapshots;
}

static void finish_event(void)
{
    if (!event_open) return;
    assert(updates - event_updates == (input_result == PC98QR_MANUAL_EDITED ? 1u : 0u));
    assert(completed - event_completed <= 1);
    check_stores(0, completed != event_completed);
    snapshot(0);
    event_open = 0;
}

static int observed_init(struct pc98qr_manual_selection *state, unsigned short count)
{
    int result;
    assert(phase == BOOT && state_inits == 0 && count == 24);
    ++state_inits;
    record("state_init");
    if (failing("init")) { phase = FAILED; return PC98QR_MANUAL_ERROR_ARGUMENT; }
    result = pc98qr_manual_selection_init(state, count);
    assert(result == PC98QR_MANUAL_UNCHANGED);
    state_initialized = 1;
    memcpy(&saved_state, state, sizeof(saved_state));
    phase = STATE;
    return result;
}

static int observed_header(unsigned short index, unsigned short count, unsigned char *out)
{
    assert(phase == STATE && headers == 0 && index == 0 && count == 24 && out == fixed_frame);
    check_state();
    ++headers;
    record("header");
    phase = failing("header") ? FAILED : HEADER;
    return failing("header") ? PC98QR_FRAME_HEADER_ERROR_ARGUMENT
                             : pc98qr_frame_header_serialize(index, count, out);
}

static int observed_encode(const unsigned char *data, unsigned int length, unsigned char *out)
{
    int result;
    assert(phase == HEADER && encodes == 0 && data == fixed_frame && length == 2953 && out == fixed_matrix);
    check_state();
    ++encodes;
    record("encode");
    if (failing("encoder")) {
        phase = FAILED;
        return QR_ERROR_LENGTH;
    }
    result = qr_encode_v40l_bytes(data, length, out);
    assert(result == QR_OK);
    memcpy(saved_frame, data, sizeof(saved_frame));
    memcpy(saved_matrix, out, sizeof(saved_matrix));
    encoded = 1;
    phase = ENCODED;
    return result;
}

void graphics_init(void)
{
    assert(phase == ENCODED && inits == 0);
    ++inits;
    record("init");
    phase = INIT;
}

void screen_clear(void)
{
    unsigned int p;
    assert(phase == INIT && clears == 0);
    ++clears;
    record("clear");
    for (p = 0; p < 4; ++p) memset(planes[p].bytes, 255, 32000);
    phase = QR;
}

void box_fill(unsigned int x, unsigned int y, unsigned int width, unsigned int height)
{
    unsigned int p, dx, dy;
    assert(phase == QR && width == 2 && height == 2);
    assert(x >= 143 && x <= 495 && (x - 143) % 2 == 0);
    assert(y >= 23 && y <= 375 && (y - 23) % 2 == 0);
    if (boxes == 0) { ++qr_passes; record("qr"); }
    ++boxes;
    for (dy = 0; dy < 2; ++dy)
        for (dx = 0; dx < 2; ++dx) {
            unsigned int offset = (y + dy) * 80 + (x + dx) / 8;
            unsigned char bit = (unsigned char)(0x80u >> ((x + dx) % 8));
            for (p = 0; p < 4; ++p) {
                assert(planes[p].bytes[offset] & bit);
                planes[p].bytes[offset] &= (unsigned char)~bit;
            }
        }
}

static int observed_fixed(void)
{
    unsigned int i, black = 0;
    int result;
    assert(phase == QR && initials == 0 && qr_passes == 1);
    check_state();
    for (i = 0; i < 31329; ++i) black += (fixed_matrix[i / 8] >> (7 - i % 8)) & 1;
    assert(boxes == black);
    puts("{\"kind\":\"qr\"}");
    output_planes();
    ++initials;
    record("fixed");
    phase = INITIAL;
    start_stores();
    result = failing("initial") ? PC98QR_STATUS_ERROR_ARGUMENT : pc98qr_status_fixed();
    check_stores(1, !failing("initial"));
    phase = result == PC98QR_STATUS_OK ? READY : FAILED;
    return result;
}

void graphics_show(void)
{
    assert(phase == READY && shows == 0);
    ++shows;
    record("show");
    phase = VISIBLE;
    snapshot(1);
}

/*
 * BIOS境界だけを模擬し、pollとclassifyは本番処理を使う。
 */
/* Only these BIOS boundaries are simulated; poll and classify remain real. */
static uint16_t bios_peek_key(unsigned char *bh)
{
    unsigned long value;
    assert(phase == POLLED && !queued);
    ++peeks;
    while (*events == ' ') ++events;
    assert(*events != '\0'); /* An unexpected extra poll fails instead of hanging. */ /* 予期しない追加ポーリングはハングせず失敗させる。 */
    if (*events == '-') {
        ++events;
        assert(*events == ' ' || *events == '\0');
        *bh = 0;
    } else if (*events == '!') {
        value = strtoul(events + 1, &pending_end, 16);
        assert(value > 1 && value <= 255 && pending_end != events + 1);
        assert(*pending_end == ' ' || *pending_end == '\0');
        events = pending_end;
        *bh = (unsigned char)value;
    } else {
        errno = 0;
        value = strtoul(events, &pending_end, 16);
        assert(errno == 0 && pending_end != events && value <= 65535);
        assert(*pending_end == ' ' || *pending_end == '\0');
        pending_ax = (unsigned short)value;
        queued = 1;
        *bh = 1;
    }
    return 0x001B; /* Stale/different Esc must never replace the consumed AX. */ /* 古い別のEscで、実際に消費したAXを置き換えてはならない。 */
}

static uint16_t bios_read_key(void)
{
    assert(phase == POLLED && queued);
    ++reads;
    queued = 0;
    events = pending_end;
    return pending_ax;
}

static int observed_poll(unsigned short *ax)
{
    assert(shutdowns == 0);
    finish_event();
    assert(phase == VISIBLE || phase == INPUT);
    check_state();
    assert(polls < 1024);
    ++polls;
    record("poll");
    phase = POLLED;
    event_open = 1;
    event_updates = updates;
    event_completed = completed;
    input_result = -1;
    event_key = 0;
    event_ax = -1;
    start_stores();
    /*
     * 通常は到達しない引数エラーを、API境界で注入する。
     */
    /* Argument failure is normally unreachable; inject at the API boundary. */
    poll_result = failing("key_argument") ? PC98QR_KEY_ERROR_ARGUMENT : pc98qr_key_poll(ax);
    if (poll_result == PC98QR_KEY_READY) event_ax = *ax;
    else phase = poll_result == PC98QR_KEY_EMPTY ? VISIBLE : FAILED;
    return poll_result;
}

static unsigned int observed_classify(unsigned short ax)
{
    assert(phase == POLLED && poll_result == PC98QR_KEY_READY && ax == event_ax);
    ++classifications;
    record("classify");
    phase = CLASSIFIED;
    event_key = pc98qr_key_classify(ax);
    return event_key;
}

static int observed_input(struct pc98qr_manual_selection *state, unsigned int key)
{
    assert(phase == CLASSIFIED && state != NULL && key == event_key);
    assert(memcmp(state, &saved_state, sizeof(saved_state)) == 0);
    check_state();
    ++inputs;
    record("input");
    input_result = failing_number("input", inputs) ? PC98QR_MANUAL_ERROR_ARGUMENT
                                                  : pc98qr_manual_selection_input(state, key);
    memcpy(&saved_state, state, sizeof(saved_state));
    phase = input_result == PC98QR_MANUAL_ERROR_ARGUMENT ? FAILED : INPUT;
    return input_result;
}

static int observed_text(unsigned int x, unsigned int y, const char *text, unsigned int length)
{
    unsigned int i;
    int result;
    assert(phase == INPUT && input_result == PC98QR_MANUAL_EDITED);
    assert(x == 520 && y == 96 && length == 5 && text != NULL);
    for (i = 0; i < 5; ++i) {
        char expected = i < saved_state.length ? saved_state.digits[i] : ' ';
        if (saved_state.length == 0 && i == 0) expected = '-';
        assert(text[i] == expected);
    }
    memcpy(submitted, text, 5);
    ++updates;
    record("update");
    phase = UPDATE;
    result = failing_number("update", updates) ? PC98QR_STATUS_ERROR_ARGUMENT
                                               : pc98qr_status_text(x, y, text, length);
    if (result == PC98QR_STATUS_OK) {
        ++completed;
        memcpy(displayed, text, 5);
        phase = INPUT;
    } else phase = FAILED;
    return result;
}

void wait_key(void)
{
    assert(0); /* QINPUT must have exactly one consumer: the polling adapter. */ /* QINPUTのキー消費者はポーリングアダプター一つだけでなければならない。 */
}

void graphics_shutdown(void)
{
    assert(inits == 1 && shutdowns == 0);
    assert(phase == FAILED || (phase == INPUT && input_result == PC98QR_MANUAL_EXIT));
    finish_event();
    check_state();
    ++shutdowns;
    record("shutdown");
    phase = DONE;
}

int main(int argc, char **argv)
{
    unsigned int p, i;
    int result;
    char *probe_argv[] = {"QINPUT", "unexpected", NULL};
    assert(argc == 3 || argc == 4);
    assert(strcmp(argv[1], "capture") == 0);
    events = argv[2];
    fault = argc == 4 ? argv[3] : "none";
    for (p = 0; p < 4; ++p) {
        memset(planes[p].before, 0xA5, 32);
        memset(planes[p].bytes, 0x21 + 0x33 * p, 32000);
        memset(planes[p].after, 0x5A, 32);
    }
    result = qinput_main(failing("usage") ? 2 : 1, probe_argv);
    /*
     * 実mainのローカル変数は寿命を終えているので、ここではグローバルなコピーだけを使う。
     */
    /* The real main's locals have expired: only global copies are used here. */
    assert(!queued && !event_open && shutdowns == inits);
    assert(phase == (inits ? DONE : failing("usage") ? BOOT : FAILED));
    assert(result != 0 || saved_state.exited);
    check_memory();
    if (!inits)
        for (p = 0; p < 4; ++p)
            for (i = 0; i < 32000; ++i) assert(planes[p].bytes[i] == 0x21 + 0x33 * p);
    printf("{\"kind\":\"summary\",\"result\":%d,\"trace\":\"%s\",\"state_inits\":%u,"
           "\"headers\":%u,\"encodes\":%u,\"inits\":%u,\"clears\":%u,\"qr_passes\":%u,"
           "\"boxes\":%u,\"initials\":%u,\"shows\":%u,\"polls\":%u,\"peeks\":%u,"
           "\"reads\":%u,\"classifications\":%u,\"inputs\":%u,\"updates\":%u,"
           "\"completed\":%u,\"shutdowns\":%u,\"snapshots\":%u}\n",
           result, trace, state_inits, headers, encodes, inits, clears, qr_passes,
           boxes, initials, shows, polls, peeks, reads, classifications, inputs,
           updates, completed, shutdowns, snapshots);
    return result;
}
