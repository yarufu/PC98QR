#define _POSIX_C_SOURCE 200809L
/*
 * 実際のQMANと制御部を観察するホスト専用試験。
 * 別の入力状態機械やDOS用フックは加えない。
 * BIOS・描画・最終書き込み・システムコール境界を模擬し、故障注入以外は実APIへ委譲する。
 */
/* Host-only observation of the real QMAN main and manual_transfer controller. No DOS test hooks or
 * alternate input state machine. BIOS, graphics, final font stores and file syscalls
 * are observed/simulated; wrappers delegate to the real APIs unless injecting a fault.
 */
#include <assert.h>
#include <fcntl.h>
#include <unistd.h>
#include "../src/file_size.h"
#include "../src/frame_count.h"
#include "../src/frame_offset.h"
#include "../src/file_chunk.h"
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
#include "../src/manual_transfer.h"

static int observed_size(const char *, unsigned long *);
/*
 * 観測用境界を維持する。
 * CURRENTの人工的故障では制御関数の実行中だけ出力ポインタを借用し、復帰時に消去する。
 */
/* Preserve this test observer boundary. The synthetic CURRENT fault borrows
 * the output pointer only during the live controller call; cleared on return. */
static __attribute__((noinline)) int observed_count(unsigned long, unsigned short *);
static int observed_offset(unsigned short, unsigned short, unsigned long *);
static int observed_chunk(const char *, unsigned long, unsigned char *, unsigned int, unsigned int *);
static ssize_t observed_write(int, const void *, size_t);
#define pc98qr_file_size observed_size
#define pc98qr_frame_count_for_size observed_count
#define pc98qr_frame_offset_for_index observed_offset
#define pc98qr_read_file_chunk observed_chunk
#define write observed_write
static int observed_init(struct pc98qr_manual_selection *, unsigned short);
static int observed_input(struct pc98qr_manual_selection *, unsigned int);
static int observed_poll(unsigned short *);
static unsigned int observed_classify(unsigned short);
static int observed_header(unsigned short, unsigned short, unsigned char *);
static int observed_encode(const unsigned char *, unsigned int, unsigned char *);
static int observed_text(unsigned int, unsigned int, const char *, unsigned int);
#define pc98qr_manual_selection_init observed_init
#define pc98qr_manual_selection_input observed_input
#define pc98qr_key_poll observed_poll
#define pc98qr_key_classify observed_classify
#define pc98qr_frame_header_serialize observed_header
#define qr_encode_v40l_bytes observed_encode
#define pc98qr_status_text observed_text
#include "../src/manual_transfer.c"
#undef pc98qr_manual_selection_init
#undef pc98qr_manual_selection_input
#undef pc98qr_key_poll
#undef pc98qr_key_classify
#undef pc98qr_frame_header_serialize
#undef qr_encode_v40l_bytes
#undef pc98qr_status_text

#undef pc98qr_file_size
#undef pc98qr_frame_count_for_size
#undef pc98qr_frame_offset_for_index
#undef pc98qr_read_file_chunk
#undef write

static enum pc98qr_transfer_result observed_transfer(const char *, unsigned char *,
    unsigned int, unsigned char *, unsigned int);
#define pc98qr_manual_transfer observed_transfer
#define write observed_write
#define main qman_main
#include "../tools/manual_file_probe.c"
#undef main
#undef write
#undef pc98qr_manual_transfer

/*
 * 差し替えるのはシステムコール境界だけ。
 * 二つのファイルヘルパーは実装そのものを動かす。
 */
/* Only the syscall boundary is substituted. Both real file helpers run. */
static int io_open(const char *, int);
static off_t io_seek(int, off_t, int);
static ssize_t io_read(int, void *, size_t);
static int io_close(int);
#define open io_open
#define lseek io_seek
#define read io_read
#define close io_close
#include "../src/file_size.c"
#include "../src/file_chunk.c"
#undef open
#undef lseek
#undef read
#undef close

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"
#define PC98QR_STATUS_FONT_HOST_TEST
#include "../src/pc98_status_font.c"

enum phase { BOOT, SIZED, COUNTED, STATE, OFFSET, CHUNK, HEADER, ENCODED, INIT, QR, TEXT, READY, VISIBLE,
             POLLED, CLASSIFIED, INPUT, FAILED, DONE, WAITING, START_POLLED, REQUESTED, CANCELLED };
static enum phase phase;
static unsigned char *transfer_frame, *transfer_matrix;
static unsigned short *count_object; /* Synthetic CURRENT guard fault only. */ /* CURRENTの防御処理に対する人工的故障だけ。 */
static int internal_result = -1;
static unsigned int start_polls, start_peeks, start_reads, information_length;
static char information[4096];
static unsigned short consumed[4096];
static unsigned int consumed_count;
static long output_budget = -1;
static unsigned int output_limit;
static int output_failed, output_zero, error_output_failed;
static struct { unsigned char before[16], bytes[2954], after[16]; } large_frame;
static struct { unsigned char before[16], bytes[3918], after[16]; } large_matrix;
static int use_large;
static struct {
    unsigned char before[32], bytes[32000], after[32];
} planes[4];
static unsigned char before_text[4][32000], text_stores[4][32000];
static unsigned char encoded_frame[QR_V40L_BYTE_MAX], encoded_matrix[QR_V40_PACKED_BYTES];
static struct pc98qr_manual_selection saved_state;
static char spelling[5] = {'-', ' ', ' ', ' ', ' '};
static char shown_select[5] = {'-', ' ', ' ', ' ', ' '};
static char shown_current[11] = "           ";
static const char *events, *fault;
static char *pending_end;
static unsigned short pending_ax, candidate;
static int queued, event_open, state_initialized, have_encoding;
static int input_result = -1, poll_result = -1, event_ax = -1;
static int encoded_index = -1, displayed_index = -1;
static unsigned int event_key, text_y, text_length, text_step;
static unsigned int state_inits, headers, encodes, inits, clears, shows, shutdowns;
static unsigned int polls, peeks, reads, classifications, inputs, texts, edits;
static unsigned int completed_redraws, snapshots, boxes, pass_boxes;
static unsigned int event_headers, event_encodes, event_clears, event_texts;
static unsigned long writes[4];
static char trace[131072];
static unsigned int sizes, counts, offsets, chunks, complete_length;
static unsigned int event_sizes, event_chunks, event_offsets;
static unsigned int io_opens, io_seeks, io_reads, io_closes, role_call, chunk_reads;
static unsigned int chunk_bytes;
static int active_fd = -1, role; /* 1=size, 2=chunk */ /* 1はサイズ取得、2はchunk読み取り。 */
static unsigned long initial_size, last_offset;
static unsigned short file_count;
static const char *file_path;


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
    char label[48];
    assert(snprintf(label, sizeof(label), "%s%u", name, number) < (int)sizeof(label));
    return failing(label);
}

static void check_guards(void)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p)
        for (i = 0; i < 32; ++i) {
            assert(planes[p].before[i] == 0xA5);
            assert(planes[p].after[i] == 0x5A);
        }
}

static void check_encoded(void)
{
    check_guards();
    assert(have_encoding);
    assert(memcmp(transfer_frame, encoded_frame, QR_V40L_BYTE_MAX) == 0);
    assert(memcmp(transfer_matrix, encoded_matrix, QR_V40_PACKED_BYTES) == 0);
}

static void snapshot(int initial)
{
    unsigned int p;
    check_guards();
    printf("{\"kind\":\"snapshot\",\"initial\":%d,\"poll\":%u,\"poll_result\":%d,"
           "\"ax\":%d,\"key\":%u,\"result\":%d,\"digits\":\"%s\",\"length\":%u,"
           "\"selected\":%u,\"error\":%u,\"exited\":%u,\"select\":\"%.5s\","
           "\"spelling\":\"%.5s\",\"current\":\"%.11s\",\"encoded_index\":%d,"
           "\"displayed_index\":%d,\"headers\":%u,\"encodes\":%u,\"clears\":%u,"
           "\"texts\":%u,\"writes\":[%lu,%lu,%lu,%lu]}\n",
           initial, polls, poll_result, event_ax, event_key, input_result,
           saved_state.digits, saved_state.length, saved_state.selected_number,
           saved_state.error, saved_state.exited, shown_select, spelling, shown_current,
           encoded_index, displayed_index, headers, encodes, clears, texts,
           writes[0], writes[1], writes[2], writes[3]);
    for (p = 0; p < 4; ++p)
        assert(fwrite(planes[p].bytes, 1, 32000, stdout) == 32000);
    assert(fwrite(transfer_matrix, 1, QR_V40_PACKED_BYTES, stdout) == QR_V40_PACKED_BYTES);
    assert(fwrite(transfer_frame, 1, QR_V40L_BYTE_MAX, stdout) == QR_V40L_BYTE_MAX);
    printf("{\"kind\":\"io\",\"sizes\":%u,\"chunks\":%u,\"offsets\":%u,"
           "\"offset\":%lu,\"complete_length\":%u}\n",
           sizes, chunks, offsets, last_offset, complete_length);
    ++snapshots;
}

static void finish_event(void)
{
    if (!event_open) return;
    if (input_result != PC98QR_MANUAL_SELECTED) {
        assert(sizes == event_sizes && chunks == event_chunks && offsets == event_offsets);
    }
    if (input_result == PC98QR_MANUAL_EDITED) {
        assert(headers == event_headers && encodes == event_encodes && clears == event_clears);
        assert(texts == event_texts + 1);
    } else if (input_result == PC98QR_MANUAL_SELECTED) {
        assert(sizes == event_sizes + 1);
        assert(headers - event_headers <= 1);
        if (phase != FAILED) {
            assert(chunks == event_chunks + 1 && offsets == event_offsets + 1);
            assert(headers == event_headers + 1);
        }
        assert(encodes - event_encodes <= 1 && clears - event_clears <= 1);
        assert(texts - event_texts <= 4);
        if (phase != FAILED) {
            assert(encodes == event_encodes + 1 && clears == event_clears + 1);
            assert(texts == event_texts + 4);
        }
    } else {
        assert(headers == event_headers && encodes == event_encodes && clears == event_clears);
        assert(texts == event_texts);
        assert(writes[0] == 0 && writes[1] == 0 && writes[2] == 0 && writes[3] == 0);
    }
    snapshot(0);
    event_open = 0;
}

static int observed_init(struct pc98qr_manual_selection *state, unsigned short count)
{
    int result;
    assert(phase == SIZED && sizes == 2 && state_inits == 0 && count == file_count);
    ++state_inits;
    record("state_init");
    if (failing("init")) { phase = FAILED; return PC98QR_MANUAL_ERROR_ARGUMENT; }
    result = pc98qr_manual_selection_init(state, count);
    assert(result == PC98QR_MANUAL_UNCHANGED);
    saved_state = *state;
    state_initialized = 1;
    phase = STATE;
    return result;
}

static int observed_header(unsigned short index, unsigned short count, unsigned char *out)
{
    assert(phase == CHUNK && count == file_count && index < count && out == transfer_frame);
    assert(index == (event_open ? saved_state.selected_number - 1u : 0u));
    candidate = index;
    ++headers;
    record("header");
    phase = failing_number("header", headers) ? FAILED : HEADER;
    return phase == FAILED ? PC98QR_FRAME_HEADER_ERROR_ARGUMENT
                           : pc98qr_frame_header_serialize(index, count, out);
}

static int observed_encode(const unsigned char *data, unsigned int length, unsigned char *out)
{
    int result;
    assert(phase == HEADER && data == transfer_frame && length == complete_length && out == transfer_matrix);
    ++encodes;
    record("encode");
    if (failing_number("encoder", encodes)) { phase = FAILED; return QR_ERROR_LENGTH; }
    memcpy(encoded_frame, data, QR_V40L_BYTE_MAX);
    result = qr_encode_v40l_bytes(data, length, out);
    assert(result == QR_OK && memcmp(data, encoded_frame, QR_V40L_BYTE_MAX) == 0);
    memcpy(encoded_matrix, out, QR_V40_PACKED_BYTES);
    encoded_index = candidate;
    have_encoding = 1;
    phase = ENCODED;
    /*
     * 符号化成功後に総数を意図的に壊し、CURRENTの防御分岐を試す。
     * 実ファイルやAPIの故障モデルではない。
     */
    /* Deliberately corrupt the live count AFTER successful encoding to exercise
     * the defensive CURRENT failure branch. Not a real file/API failure model. */
    if (failing_number("format", encodes)) { *count_object = 0; phase = FAILED; }
    return result;
}

void graphics_init(void)
{
    assert(phase == ENCODED && inits == 0 && !event_open);
    check_encoded();
    ++inits;
    record("init");
    phase = INIT;
}

void screen_clear(void)
{
    unsigned int p;
    assert(inits == 1 && (phase == INIT || (phase == ENCODED && event_open)));
    check_encoded();
    ++clears;
    record("clear");
    for (p = 0; p < 4; ++p) {
        memset(planes[p].bytes, 255, 32000);
        writes[p] += 32000;
    }
    pass_boxes = text_step = 0;
    phase = QR;
}

void box_fill(unsigned int x, unsigned int y, unsigned int width, unsigned int height)
{
    unsigned int p, dx, dy;
    assert(phase == QR && width == 2 && height == 2);
    assert(x >= 143 && x <= 495 && (x - 143) % 2 == 0);
    assert(y >= 23 && y <= 375 && (y - 23) % 2 == 0);
    if (pass_boxes == 0) record("qr");
    ++pass_boxes;
    ++boxes;
    for (dy = 0; dy < 2; ++dy)
        for (dx = 0; dx < 2; ++dx) {
            unsigned int offset = (y + dy) * 80 + (x + dx) / 8;
            unsigned char bit = (unsigned char)(0x80u >> ((x + dx) % 8));
            for (p = 0; p < 4; ++p) {
                assert(planes[p].bytes[offset] & bit);
                planes[p].bytes[offset] &= (unsigned char)~bit;
                ++writes[p];
            }
        }
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
    assert(phase == TEXT && offset < 32000);
    assert(offset / 80 >= text_y && offset / 80 < text_y + 16);
    assert(offset % 80 >= 65 && offset % 80 < 65 + text_length);
    assert(text_stores[p][offset]++ == 0);
    ++writes[p];
    planes[p].bytes[offset] = value;
}

static int observed_text(unsigned int x, unsigned int y, const char *text, unsigned int length)
{
    static const unsigned int rows[4] = {32, 48, 80, 96};
    static const unsigned int lengths[4] = {7, 11, 6, 5};
    static const char *roles[4] = {"current_label", "current", "select_label", "select"};
    unsigned int p, i;
    int result, edit = phase == INPUT, fail;
    char expected_current[12];
    assert(x == 520 && text != NULL);
    if (edit) {
        assert(input_result == PC98QR_MANUAL_EDITED && y == 96 && length == 5);
        ++edits;
        fail = failing_number("edit", edits);
        record("edit");
    } else {
        assert(phase == QR || phase == TEXT);
        assert(text_step < 4 && y == rows[text_step] && length == lengths[text_step]);
        if (text_step == 0) {
            unsigned int black = 0;
            for (i = 0; i < 31329; ++i) black += (transfer_matrix[i / 8] >> (7 - i % 8)) & 1;
            assert(pass_boxes == black);
            assert(memcmp(text, "CURRENT", 7) == 0);
        } else if (text_step == 1) {
            int used = snprintf(expected_current, sizeof(expected_current), "%u/%u", candidate + 1u, file_count);
            assert(used >= 3 && used <= 11);
            memset(expected_current + used, ' ', 11u - (unsigned int)used);
            assert(memcmp(text, expected_current, 11) == 0);
        } else if (text_step == 2) assert(memcmp(text, "SELECT", 6) == 0);
        fail = failing_number(roles[text_step], clears);
        record(roles[text_step]);
    }
    if (y == 96) assert(length == 5 && memcmp(text, spelling, 5) == 0);
    check_encoded();
    ++texts;
    text_y = y;
    text_length = length;
    memset(text_stores, 0, sizeof(text_stores));
    for (p = 0; p < 4; ++p) memcpy(before_text[p], planes[p].bytes, 32000);
    phase = TEXT;
    result = fail ? PC98QR_STATUS_ERROR_ARGUMENT : pc98qr_status_text(x, y, text, length);
    for (p = 0; p < 4; ++p)
        for (i = 0; i < 32000; ++i) {
            unsigned int expected = !fail && i / 80 >= y && i / 80 < y + 16 &&
                                    i % 80 >= 65 && i % 80 < 65 + length;
            assert(text_stores[p][i] == expected);
            if (!expected) assert(planes[p].bytes[i] == before_text[p][i]);
        }
    check_encoded();
    if (result != PC98QR_STATUS_OK) { phase = FAILED; return result; }
    if (y == 48) memcpy(shown_current, text, 11);
    if (y == 96) memcpy(shown_select, text, 5);
    if (edit) phase = INPUT;
    else if (++text_step == 4) {
        ++completed_redraws;
        displayed_index = candidate;
        phase = event_open ? INPUT : READY;
    }
    return result;
}

void graphics_show(void)
{
    assert(phase == READY && shows == 0 && !event_open);
    ++shows;
    record("show");
    phase = VISIBLE;
    snapshot(1);
}

static uint16_t bios_peek_key(unsigned char *bh)
{
    unsigned long value;
    assert((phase == POLLED || phase == START_POLLED) && !queued);
    if (phase == START_POLLED) ++start_peeks; else ++peeks;
    while (*events == ' ') ++events;
    assert(*events); /* Unexpected extra polling fails rather than hanging. */ /* 予期しない追加ポーリングはハングせず失敗させる。 */
    if (*events == '-') {
        ++events;
        assert(*events == ' ' || !*events);
        *bh = 0;
    } else {
        int bad = *events == '!';
        const char *start = events + bad;
        errno = 0;
        value = strtoul(start, &pending_end, 16);
        assert(errno == 0 && pending_end != start && value <= (bad ? 255u : 65535u));
        assert(*pending_end == ' ' || !*pending_end);
        if (bad) {
            assert(value > 1);
            events = pending_end;
            *bh = (unsigned char)value;
        } else {
            pending_ax = (unsigned short)value;
            queued = 1;
            *bh = 1;
        }
    }
    return 0x001B; /* Intentionally stale Esc, never the consumed event. */ /* 意図的に古いEscを返す。実際に消費したイベントとは異なる。 */
}

static uint16_t bios_read_key(void)
{
    assert((phase == POLLED || phase == START_POLLED) && queued);
    if (phase == START_POLLED) ++start_reads; else ++reads;
    assert(consumed_count < sizeof(consumed) / sizeof(consumed[0]));
    consumed[consumed_count++] = pending_ax;
    queued = 0;
    events = pending_end;
    return pending_ax;
}

static int observed_poll(unsigned short *ax)
{
    assert(shutdowns == 0);
    if (!inits) {
        int result;
        assert(phase == COUNTED || phase == WAITING);
        assert(sizes == 1 && counts == 1 && state_inits == 0);
        assert(chunks == 0 && encodes == 0 && io_reads == 0);
        assert(start_polls < 4096);
        ++start_polls;
        record("start_poll");
        phase = START_POLLED;
        result = failing("start_poll") ? PC98QR_KEY_ERROR_ARGUMENT : pc98qr_key_poll(ax);
        if (result == PC98QR_KEY_EMPTY) phase = WAITING;
        else if (result != PC98QR_KEY_READY) phase = FAILED;
        else if ((*ax & 255u) == 'S' || (*ax & 255u) == 's') phase = REQUESTED;
        return result;
    }
    finish_event();
    assert(phase == VISIBLE || phase == INPUT);
    check_encoded();
    assert(polls < 4096);
    ++polls;
    record("poll");
    phase = POLLED;
    event_open = 1;
    event_sizes = sizes;
    event_chunks = chunks;
    event_offsets = offsets;
    event_headers = headers;
    event_encodes = encodes;
    event_clears = clears;
    event_texts = texts;
    input_result = event_ax = -1;
    event_key = 0;
    memset(writes, 0, sizeof(writes));
    poll_result = failing_number("poll", polls) ? PC98QR_KEY_ERROR_ARGUMENT : pc98qr_key_poll(ax);
    if (poll_result == PC98QR_KEY_READY) event_ax = *ax;
    else phase = poll_result == PC98QR_KEY_EMPTY ? VISIBLE : FAILED;
    return poll_result;
}

static unsigned int observed_classify(unsigned short ax)
{
    if (phase == START_POLLED) {
        unsigned int key = pc98qr_key_classify(ax);
        assert(start_reads != 0 && consumed[consumed_count - 1] == ax);
        record("start_classify");
        phase = key == PC98QR_MANUAL_KEY_ESCAPE ? CANCELLED : WAITING;
        return key;
    }
    assert(phase == POLLED && poll_result == PC98QR_KEY_READY && ax == event_ax);
    ++classifications;
    record("classify");
    phase = CLASSIFIED;
    event_key = pc98qr_key_classify(ax);
    return event_key;
}

static int observed_input(struct pc98qr_manual_selection *state, unsigned int key)
{
    unsigned int i;
    assert(phase == CLASSIFIED && state_initialized && key == event_key);
    assert(memcmp(state, &saved_state, sizeof(saved_state)) == 0);
    ++inputs;
    record("input");
    input_result = failing_number("input", inputs) ? PC98QR_MANUAL_ERROR_ARGUMENT
                                                  : pc98qr_manual_selection_input(state, key);
    saved_state = *state; /* Copy at the API boundary, never retain a local pointer. */ /* API境界で値をコピーし、ローカル変数へのポインタを保持しない。 */
    if (input_result == PC98QR_MANUAL_EDITED) {
        for (i = 0; i < 5; ++i) spelling[i] = i < state->length ? state->digits[i] : ' ';
        if (!state->length) spelling[0] = '-';
    }
    phase = input_result == PC98QR_MANUAL_ERROR_ARGUMENT ? FAILED : INPUT;
    return input_result;
}

void wait_key(void) { assert(0); }

void graphics_shutdown(void)
{
    assert(inits == 1 && shutdowns == 0);
    assert(phase == FAILED || (phase == INPUT && input_result == PC98QR_MANUAL_EXIT));
    if (event_open) finish_event();
    else snapshot(0); /* Initial status failure before show. */ /* 画面を表示する前の初期状態描画失敗。 */
    ++shutdowns;
    record("shutdown");
    phase = DONE;
}

/*
 * 故障名の末尾は役割ごとの呼出し番号。
 * 例はchunk_seek2。
 */
/* Fault names end in the per-role call number, e.g. chunk_seek2. */
static int io_fault(const char *operation)
{
    char label[64];
    assert(snprintf(label, sizeof(label), "%s_%s%u", role == 1 ? "size" : "chunk",
                    operation, role_call) < (int)sizeof(label));
    return failing(label);
}

static int io_open(const char *path, int flags)
{
    assert(active_fd == -1 && strcmp(path, file_path) == 0 && flags == O_RDONLY);
    ++io_opens;
    if (io_fault("open")) return -1;
    active_fd = open(path, flags);
    return active_fd;
}

static off_t io_seek(int fd, off_t offset, int whence)
{
    off_t result;
    assert(fd == active_fd && fd >= 0);
    assert((role == 1 && offset == 0 && whence == SEEK_END) ||
           (role == 2 && offset == (off_t)last_offset && whence == SEEK_SET));
    ++io_seeks;
    if (io_fault("seek")) return -1;
    result = lseek(fd, offset, whence);
    return io_fault("wrong_seek") ? result + 1 : result;
}

static ssize_t io_read(int fd, void *buffer, size_t length)
{
    ssize_t result;
    assert(role == 2 && fd == active_fd && fd >= 0);
    assert(buffer == transfer_frame + 9 + chunk_bytes && length == 2944u - chunk_bytes);
    ++io_reads;
    ++chunk_reads;
    if (io_fault("read") || (io_fault("partial_error") && chunk_reads == 3)) return -1;
    if (io_fault("zero") || (io_fault("short") && chunk_reads > 1)) return 0;
    if (failing("partial") || io_fault("partial_error") || io_fault("short"))
        if (length > 47) length = 47;
    result = read(fd, buffer, length);
    if (result > 0) chunk_bytes += (unsigned int)result;
    return result;
}

static int io_close(int fd)
{
    int result;
    assert(fd == active_fd && fd >= 0);
    ++io_closes;
    result = close(fd); /* Release the real host fd even when injecting failure. */ /* 故障注入時も実際のホスト側ファイル記述子は閉じる。 */
    active_fd = -1;
    return io_fault("close") ? -1 : result;
}

static int observed_size(const char *path, unsigned long *out)
{
    int result;
    assert(phase == BOOT || phase == REQUESTED ||
           (phase == INPUT && input_result == PC98QR_MANUAL_SELECTED));
    assert(active_fd == -1 && shutdowns == 0);
    ++sizes;
    record("size");
    if (sizes == 2) {
        if (failing("delete_start")) assert(unlink(path) == 0);
        if (failing("shrink_start")) assert(truncate(path, (off_t)initial_size - 1) == 0);
        if (failing("grow_start")) assert(truncate(path, (off_t)initial_size + 1) == 0);
        if (failing("same_size_change")) {
            int fd = open(path, O_WRONLY);
            unsigned char byte = 0xF3;
            assert(fd >= 0 && write(fd, &byte, 1) == 1 && close(fd) == 0);
        }
    }
    if (sizes == 3) {
        if (failing("delete")) assert(unlink(path) == 0);
        if (failing("shrink")) assert(truncate(path, (off_t)initial_size - 1) == 0);
        if (failing("grow")) assert(truncate(path, (off_t)initial_size + 1) == 0);
    }
    role = 1;
    role_call = sizes;
    result = pc98qr_file_size(path, out);
    assert(active_fd == -1);
    phase = result == PC98QR_FILE_SIZE_OK ? SIZED : FAILED;
    if (result == PC98QR_FILE_SIZE_OK) {
        if (sizes == 1) initial_size = *out;
        else if (*out != initial_size) phase = FAILED;
    }
    return result;
}

static int observed_count(unsigned long size, unsigned short *out)
{
    int result;
    assert(phase == SIZED && sizes == 1 && counts == 0);
    ++counts;
    record("count");
    result = failing("count") ? PC98QR_FRAME_COUNT_ERROR_SIZE
                              : pc98qr_frame_count_for_size(size, out);
    count_object = out;
    phase = result == PC98QR_FRAME_COUNT_OK ? COUNTED : FAILED;
    if (result == PC98QR_FRAME_COUNT_OK) file_count = *out;
    return result;
}

static int observed_offset(unsigned short index, unsigned short count, unsigned long *out)
{
    int result;
    assert(phase == STATE || (phase == SIZED && event_open));
    assert(count == file_count && index == (event_open ? saved_state.selected_number - 1u : 0u));
    ++offsets;
    record("offset");
    result = failing_number("offset", offsets) ? PC98QR_FRAME_OFFSET_ERROR_INDEX
                                               : pc98qr_frame_offset_for_index(index, count, out);
    phase = result == PC98QR_FRAME_OFFSET_OK ? OFFSET : FAILED;
    if (result == PC98QR_FRAME_OFFSET_OK) {
        if (failing_number("offset_end", offsets)) { *out = initial_size; phase = FAILED; }
        last_offset = *out;
    }
    return result;
}

static int observed_chunk(const char *path, unsigned long offset, unsigned char *out,
                          unsigned int capacity, unsigned int *length)
{
    int result;
    assert(phase == OFFSET && active_fd == -1 && offset == last_offset);
    assert(out == transfer_frame + 9 && capacity == 2944 && shutdowns == 0);
    ++chunks;
    record("chunk");
    if (chunks == 2) {
        if (failing("delete_after_size")) assert(unlink(path) == 0);
        if (failing("shrink_after_size")) assert(truncate(path, (off_t)initial_size - 1) == 0);
        if (failing("grow_after_size")) assert(truncate(path, (off_t)initial_size + 1) == 0);
    }
    role = 2;
    role_call = chunks;
    chunk_reads = chunk_bytes = 0;
    result = pc98qr_read_file_chunk(path, offset, out, capacity, length);
    assert(active_fd == -1);
    phase = result == PC98QR_FILE_CHUNK_OK ? CHUNK : FAILED;
    if (result == PC98QR_FILE_CHUNK_OK) {
        unsigned long expected = initial_size - offset;
        if (expected > 2944) expected = 2944;
        if (*length != expected) phase = FAILED;
        else complete_length = 9 + *length;
    }
    return result;
}

static ssize_t observed_write(int fd, const void *buffer, size_t length)
{
    assert(length > 0 && active_fd == -1);
    assert(inits == 0 || (shutdowns == 1 && phase == DONE));
    if (fd == STDOUT_FILENO) {
        assert(!inits && !chunks && !encodes && !state_inits && !output_failed);
        assert(phase == COUNTED || phase == CANCELLED);
        if (information_length == 0) record("info");
        if (output_budget == 0) {
            output_failed = 1;
            phase = FAILED;
            return output_zero ? 0 : -1;
        }
        if (output_limit && length > output_limit) length = output_limit;
        if (output_budget > 0 && length > (size_t)output_budget) length = (size_t)output_budget;
        assert(information_length + length < sizeof(information));
        memcpy(information + information_length, buffer, length);
        information_length += (unsigned int)length;
        if (output_budget > 0) output_budget -= (long)length;
        return (ssize_t)length;
    }
    assert(fd == STDERR_FILENO);
    record("error");
    if (error_output_failed || failing("output")) return -1;
    if (output_limit && length > output_limit) length = output_limit;
    return write(fd, buffer, length);
}

static enum pc98qr_transfer_result observed_transfer(const char *path,
    unsigned char *frame_arg, unsigned int frame_capacity,
    unsigned char *matrix_arg, unsigned int matrix_capacity)
{
    enum pc98qr_transfer_result result;
    assert(frame_arg == frame && matrix_arg == matrix);
    assert(frame_capacity == sizeof(frame) && matrix_capacity == sizeof(matrix));
    transfer_frame = frame_arg; transfer_matrix = matrix_arg;
    if (failing("large_buffers")) {
        use_large = 1;
        memset(&large_frame, 0xA7, sizeof(large_frame));
        memset(&large_matrix, 0xB9, sizeof(large_matrix));
        transfer_frame = large_frame.bytes; transfer_matrix = large_matrix.bytes;
        frame_capacity = sizeof(large_frame.bytes); matrix_capacity = sizeof(large_matrix.bytes);
    }
    result = pc98qr_manual_transfer(path, transfer_frame, frame_capacity,
                                    transfer_matrix, matrix_capacity);
    count_object = NULL; /* Borrowed only while the controller's call is live. */ /* 制御関数の実行中だけ借用する。 */
    internal_result = result;
    if (use_large) {
        unsigned int i;
        assert(large_frame.bytes[2953] == 0xA7 && large_matrix.bytes[3917] == 0xB9);
        for (i = 0; i < 16; ++i) {
            assert(large_frame.before[i] == 0xA7 && large_frame.after[i] == 0xA7);
            assert(large_matrix.before[i] == 0xB9 && large_matrix.after[i] == 0xB9);
        }
    }
    return result;
}

static int argument_tests(void)
{
    unsigned int i;
    struct args { const char *path; unsigned char *f; unsigned int fc;
                  unsigned char *m; unsigned int mc; } cases[] = {
        {NULL, frame, 2953, matrix, 3917}, {"", frame, 2953, matrix, 3917},
        {"unused", NULL, 2953, matrix, 3917}, {"unused", frame, 2953, NULL, 3917},
        {"unused", frame, 2952, matrix, 3917}, {"unused", frame, 2953, matrix, 3916},
        {"unused", frame, 0, matrix, 3917}, {"unused", frame, 2953, matrix, 0}};
    memset(frame, 0xA5, sizeof(frame)); memset(matrix, 0x5A, sizeof(matrix));
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
        assert(pc98qr_manual_transfer(cases[i].path, cases[i].f, cases[i].fc,
                                     cases[i].m, cases[i].mc) == PC98QR_TRANSFER_ERROR_ARGUMENT);
    assert(!sizes && !counts && !chunks && !inits && !start_polls && !*trace);
    for (i = 0; i < sizeof(frame); ++i) assert(frame[i] == 0xA5);
    for (i = 0; i < sizeof(matrix); ++i) assert(matrix[i] == 0x5A);
    puts("eight invalid arguments: no I/O or buffer writes");
    return 0;
}

/*
 * 本番の整形関数を独立したsnprintf結果と境界監視値で検査する。
 * 不正引数や追記境界の防御も含む。
 */
/* Exercise the production formatter against independent snprintf text and
 * canaries, including invalid arguments and its append boundary guard.
 */
static int format_tests(void)
{
    struct { unsigned char before; char text[11]; unsigned char after; } guarded;
    unsigned int count, index, position;
    char expected[12];
    guarded.before = 0xA5; guarded.after = 0x5A;
    for (count = 1; count <= 65535; ++count) {
        unsigned int choices[3] = {0, count / 2, count - 1};
        for (index = 0; index < 3; ++index) {
            int length = snprintf(expected, sizeof(expected), "%u/%u", choices[index] + 1, count);
            memset(expected + length, ' ', 11u - (unsigned int)length);
            assert(current_text((unsigned short)choices[index], (unsigned short)count, guarded.text));
            assert(memcmp(expected, guarded.text, 11) == 0);
            assert(guarded.before == 0xA5 && guarded.after == 0x5A);
        }
    }
    memset(guarded.text, '?', 11);
    assert(!current_text(0, 0, guarded.text));
    assert(!current_text(24, 24, guarded.text));
    position = 11;
    assert(!append_decimal(1, guarded.text, &position));
    for (index = 0; index < 11; ++index) assert(guarded.text[index] == '?');
    assert(guarded.before == 0xA5 && guarded.after == 0x5A);
    puts("formatter: 196605 valid pairs, invalid and boundary guards OK");
    return 0;
}

int main(int argc, char **argv)
{
    unsigned int p, i;
    int result, probe_argc = 2;
    char *probe_argv[] = {"QMAN", NULL, "extra", NULL};
    if (argc == 2 && strcmp(argv[1], "format") == 0) return format_tests();
    if (argc == 2 && strcmp(argv[1], "arguments") == 0) return argument_tests();
    if (argc == 3 && strcmp(argv[1], "decimal") == 0) {
        unsigned long value = strcmp(argv[2], "max") == 0 ? ULONG_MAX : strtoul(argv[2], NULL, 10);
        phase = COUNTED;
        fault = "none";
        assert(emit_decimal(value));
        return fwrite(information, 1, information_length, stdout) == information_length ? 0 : 1;
    }
    assert(argc == 5 && strcmp(argv[1], "capture") == 0);
    file_path = argv[2];
    probe_argv[1] = argv[2];
    events = argv[3];
    fault = argv[4];
    if (getenv("QMAN_OUTPUT_BUDGET")) output_budget = strtol(getenv("QMAN_OUTPUT_BUDGET"), NULL, 10);
    if (getenv("QMAN_OUTPUT_LIMIT")) output_limit = (unsigned int)strtoul(getenv("QMAN_OUTPUT_LIMIT"), NULL, 10);
    output_zero = getenv("QMAN_OUTPUT_ZERO") != NULL;
    error_output_failed = getenv("QMAN_ERROR_OUTPUT_FAIL") != NULL;
    if (failing("usage_none")) probe_argc = 1;
    if (failing("usage_extra")) probe_argc = 3;
    for (p = 0; p < 4; ++p) {
        memset(planes[p].before, 0xA5, 32);
        memset(planes[p].bytes, 0x21 + 0x33 * p, 32000);
        memset(planes[p].after, 0x5A, 32);
    }
    result = qman_main(probe_argc, probe_argv);
    assert(!queued && !event_open && shutdowns == inits && active_fd == -1);
    assert(phase == (inits ? DONE : result == 1 ? BOOT : result == 0 ? CANCELLED : FAILED));
    assert(result != 0 || (inits ? saved_state.exited : internal_result == PC98QR_TRANSFER_CANCELLED));
    check_guards();
    if (!inits)
        for (p = 0; p < 4; ++p)
            for (i = 0; i < 32000; ++i) assert(planes[p].bytes[i] == 0x21 + 0x33 * p);
    printf("{\"kind\":\"startup\",\"length\":%u,\"polls\":%u,\"peeks\":%u,"
           "\"reads\":%u,\"internal\":%d,\"consumed\":[", information_length,
           start_polls, start_peeks, start_reads, internal_result);
    for (i = 0; i < consumed_count; ++i) printf("%s%u", i ? "," : "", consumed[i]);
    puts("]}");
    assert(fwrite(information, 1, information_length, stdout) == information_length);
    printf("{\"kind\":\"summary\",\"result\":%d,\"trace\":\"%s\",\"headers\":%u,\"encodes\":%u,"
           "\"inits\":%u,\"clears\":%u,\"shows\":%u,\"shutdowns\":%u,\"polls\":%u,"
           "\"peeks\":%u,\"reads\":%u,\"classifications\":%u,\"inputs\":%u,\"texts\":%u,"
           "\"edits\":%u,\"redraws\":%u,\"boxes\":%u,\"snapshots\":%u,"
           "\"sizes\":%u,\"counts\":%u,\"chunks\":%u,\"offsets\":%u,"
           "\"io\":[%u,%u,%u,%u]}\n",
           result, trace, headers, encodes, inits, clears, shows, shutdowns, polls, peeks,
           reads, classifications, inputs, texts, edits, completed_redraws, boxes, snapshots,
           sizes, counts, chunks, offsets, io_opens, io_seeks, io_reads, io_closes);
    return result;
}
