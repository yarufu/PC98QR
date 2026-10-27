/*
 * 実QRTESTと別コンパイルしたmanual_transferを使う。
 * ハードウェア境界だけを模擬し、wrap観測は実ヘルパーへ委譲する。
 * QMANのmain・バッファ・解析・終了コード変換はリンクしない。
 */
/* Real QRTEST main and separately compiled manual_transfer.c. Only hardware
 * boundaries are simulated; --wrap observers delegate to the real helpers.
 * No QMAN main, buffers, parser or ERRORLEVEL mapping is linked here.
 */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "../src/manual_transfer.h"
#include "../src/manual_selection.h"
#include "../src/pc98_status_font.h"
#include "../src/frame_v2.h"

static enum pc98qr_transfer_result observed_transfer(const char *, unsigned char *,
    unsigned int, unsigned char *, unsigned int);
#define pc98qr_manual_transfer_v2 observed_transfer
#define main qrtest_main
#include "../src/main.c"
#undef main
#undef pc98qr_manual_transfer_v2

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"
#define PC98QR_STATUS_FONT_HOST_TEST
#include "../src/pc98_status_font.c"

static unsigned int manual_calls, sizes, chunks, encodes, inits, shows, shutdowns;
static unsigned int clears, waits, polls, statuses, inputs, headers, offsets;
static unsigned int frame_capacity_seen, matrix_capacity_seen;
static int active, preflight, redraw_pending, internal_result = -1;
static unsigned char planes[4][32000];
static unsigned char *frame_seen, *matrix_seen;
static const unsigned char *legacy_buffer;
static const char *events, *fault, *expected_path;
static char information[8192], trace[8192], current[12], selection[6];
static size_t information_length;
static unsigned long last_offset;
static unsigned int wire_length;
static struct pc98qr_frame_v2_layout last_layout;
static unsigned long initial_size;
static unsigned int serializes, serialized_length;
static unsigned int chunk_reads;
static int io_role, io_fd = -1, prepared;
static struct { unsigned char before[16], bytes[2954], after[16]; } guarded_frame;
static struct { unsigned char before[16], bytes[3918], after[16]; } guarded_matrix;

/*
 * DOSパス表記をシステムコール境界でだけ変換する。
 * 両ファイルヘルパーは元のパスポインタで動き、open/seek/read/closeも実処理を使う。
 */
/* Map DOS spelling only at the syscall boundary. Both real file helpers run
 * with the original path pointer, including real open/seek/read/close.
 */
static const char *disk_path(const char *path)
{
    const char *mapped = getenv("QRTEST_DISK_PATH");
    return manual_calls && mapped && strcmp(path, expected_path) == 0 ? mapped : path;
}

static int disjoint(const void *a, size_t an, const void *b, size_t bn)
{
    return (uintptr_t)a + an <= (uintptr_t)b || (uintptr_t)b + bn <= (uintptr_t)a;
}

static void record(const char *name)
{
    assert(strlen(trace) + strlen(name) + 2 < sizeof(trace));
    if (*trace) strcat(trace, ",");
    strcat(trace, name);
}

static int failing(const char *name, unsigned int call)
{
    char key[64];
    assert(snprintf(key, sizeof(key), "%s%u", name, call) < (int)sizeof(key));
    return strcmp(fault, key) == 0;
}

static void hex(const void *data, size_t length)
{
    const unsigned char *bytes = data;
    size_t i;
    for (i = 0; i < length; ++i) printf("%02x", bytes[i]);
}

int __real_open(const char *, int, ...);
int __wrap_open(const char *path, int flags, ...)
{
    io_fd = __real_open(disk_path(path), flags);
    return io_fd;
}

ssize_t __real_read(int, void *, size_t);
ssize_t __wrap_read(int fd, void *buffer, size_t length)
{
    if (manual_calls && io_role == 2 && fd == io_fd) {
        const char *limit = getenv("QRTEST_READ_LIMIT");
        ++chunk_reads;
        if (failing("readsys", chunks) || (failing("readpartial", chunks) && chunk_reads > 1)) {
            errno = EIO;
            return -1;
        }
        if (failing("readpartial", chunks) && length > 17) length = 17;
        if (limit && length > strtoul(limit, NULL, 10)) length = strtoul(limit, NULL, 10);
    }
    return __real_read(fd, buffer, length);
}

int __real_close(int);
int __wrap_close(int fd)
{
    int result = __real_close(fd);
    if (manual_calls && io_role == 2 && failing("close", chunks)) { errno = EIO; return -1; }
    return result;
}

int __real_pc98qr_filename_v2_from_path(const char *, unsigned char *, unsigned int, unsigned int *);
int __wrap_pc98qr_filename_v2_from_path(const char *path, unsigned char *out,
                                      unsigned int capacity, unsigned int *length)
{
    record("filename");
    assert(!sizes && !chunks && !inits && path == expected_path);
    return __real_pc98qr_filename_v2_from_path(path, out, capacity, length);
}

static uint16_t bios_peek_key(unsigned char *bh)
{
    assert(*events); /* Exhaustion is a test failure, never implicit Esc. */ /* イベント枯渇は試験失敗であり、暗黙のEscとは扱わない。 */
    *bh = 1;
    return 0;
}

static uint16_t bios_read_key(void)
{
    char *end;
    unsigned long key = strtoul(events, &end, 16);
    assert(end != events && key <= 65535);
    events = end;
    while (*events == ' ') ++events;
    return (uint16_t)key;
}

int __wrap_pc98qr_key_poll(unsigned short *ax)
{
    ++polls; record("poll");
    if (failing("key", polls)) return PC98QR_KEY_ERROR_STATUS;
    return pc98qr_key_poll(ax);
}

int __real_pc98qr_file_size(const char *, unsigned long *);
int __wrap_pc98qr_file_size(const char *path, unsigned long *size)
{
    int result;
    ++sizes; record("size");
    if (manual_calls) assert(path == expected_path);
    if (failing("size", sizes)) return PC98QR_FILE_SIZE_ERROR_OPEN;
    if (manual_calls && failing("sizegrow", sizes))
        assert(truncate(disk_path(path), (off_t)initial_size + 1) == 0);
    io_role = 1;
    result = __real_pc98qr_file_size(path, size);
    io_role = 0;
    if (manual_calls && sizes == 1 && result == PC98QR_FILE_SIZE_OK) initial_size = *size;
    if (failing("changed", sizes) && result == PC98QR_FILE_SIZE_OK) ++*size;
    return result;
}

int __real_pc98qr_frame_v2_count_for_size(unsigned long, unsigned int, unsigned short *);
int __wrap_pc98qr_frame_v2_count_for_size(unsigned long size, unsigned int n, unsigned short *count)
{
    record("count");
    if (failing("count", 1)) return PC98QR_FRAME_V2_ERROR_SIZE;
    return __real_pc98qr_frame_v2_count_for_size(size, n, count);
}

int __real_pc98qr_frame_v2_layout_for_index(unsigned long, unsigned int, unsigned short,
                                          struct pc98qr_frame_v2_layout *);
int __wrap_pc98qr_frame_v2_layout_for_index(unsigned long size, unsigned int n,
                                          unsigned short index, struct pc98qr_frame_v2_layout *out)
{
    int result;
    ++offsets; record("layout");
    if (failing("offset", offsets)) return PC98QR_FRAME_V2_ERROR_INDEX;
    result = __real_pc98qr_frame_v2_layout_for_index(size, n, index, out);
    if (result == PC98QR_FRAME_V2_OK) {
        last_layout = *out; last_offset = out->file_offset;
        if (failing("layoutcount", offsets)) ++out->frame_count;
    }
    return result;
}

int __real_pc98qr_frame_count_for_size(unsigned long, unsigned short *);
int __wrap_pc98qr_frame_count_for_size(unsigned long size, unsigned short *count)
{
    record("count");
    if (failing("count", 1)) return PC98QR_FRAME_COUNT_ERROR_SIZE;
    return __real_pc98qr_frame_count_for_size(size, count);
}

int __real_pc98qr_frame_offset_for_index(unsigned short, unsigned short, unsigned long *);
int __wrap_pc98qr_frame_offset_for_index(unsigned short index, unsigned short count,
                                        unsigned long *offset)
{
    int result;
    ++offsets; record("offset");
    if (failing("offset", offsets)) return PC98QR_FRAME_OFFSET_ERROR_INDEX;
    result = __real_pc98qr_frame_offset_for_index(index, count, offset);
    if (result == PC98QR_FRAME_OFFSET_OK) last_offset = *offset;
    return result;
}

int __real_pc98qr_read_file_chunk(const char *, unsigned long, unsigned char *,
                                 unsigned int, unsigned int *);
int __wrap_pc98qr_read_file_chunk(const char *path, unsigned long offset,
    unsigned char *buffer, unsigned int capacity, unsigned int *length)
{
    int result;
    ++chunks; record("chunk");
    if (manual_calls) {
        assert(path == expected_path && buffer == matrix_seen);
        assert(capacity == last_layout.file_capacity && offset == last_offset);
        assert(capacity <= 2944 && capacity <= matrix_capacity_seen);
        prepared = 0; chunk_reads = 0;
        if (failing("shrink", chunks))
            assert(truncate(disk_path(path), (off_t)initial_size - 1) == 0);
        if (failing("grow", chunks))
            assert(truncate(disk_path(path), (off_t)initial_size + 1) == 0);
        if (failing("delete", chunks)) assert(unlink(disk_path(path)) == 0);
    }
    if (failing("read", chunks)) return PC98QR_FILE_CHUNK_ERROR_READ;
    io_role = 2;
    result = __real_pc98qr_read_file_chunk(path, offset, buffer, capacity, length);
    io_role = 0;
    if (result == PC98QR_FILE_CHUNK_OK && failing("short", chunks)) --*length;
    return result;
}

int __real_pc98qr_frame_v2_serialize(const unsigned char *, unsigned int, unsigned long,
    unsigned short, const unsigned char *, unsigned int, unsigned char *, unsigned int, unsigned int *);
int __wrap_pc98qr_frame_v2_serialize(const unsigned char *name, unsigned int n, unsigned long size,
    unsigned short index, const unsigned char *chunk, unsigned int length,
    unsigned char *out, unsigned int capacity, unsigned int *complete)
{
    int result;
    ++serializes; record("serialize");
    assert(manual_calls && !prepared && chunk == matrix_seen && out == frame_seen);
    assert(length == last_layout.file_length && capacity == 2953);
    assert(disjoint(out, capacity, chunk, length) && disjoint(out, capacity, name, n));
    assert(disjoint(out, capacity, complete, sizeof(*complete)));
    assert(disjoint(complete, sizeof(*complete), chunk, length));
    assert(disjoint(complete, sizeof(*complete), name, n));
    if (failing("header", serializes)) return PC98QR_FRAME_V2_ERROR_LENGTH;
    result = __real_pc98qr_frame_v2_serialize(name, n, size, index, chunk, length, out, capacity, complete);
    if (result == PC98QR_FRAME_V2_OK) {
        serialized_length = *complete;
        if (failing("badlength", serializes)) --*complete;
    }
    return result;
}

int __real_pc98qr_frame_header_serialize(unsigned short, unsigned short, unsigned char *);
int __wrap_pc98qr_frame_header_serialize(unsigned short index, unsigned short count,
                                        unsigned char *out)
{
    ++headers; record("header");
    if (failing("header", headers)) return PC98QR_FRAME_HEADER_ERROR_INDEX;
    return __real_pc98qr_frame_header_serialize(index, count, out);
}

int __real_pc98qr_manual_selection_init(struct pc98qr_manual_selection *, unsigned short);
int __wrap_pc98qr_manual_selection_init(struct pc98qr_manual_selection *state, unsigned short count)
{
    record("state");
    if (failing("init", 1)) return PC98QR_MANUAL_ERROR_ARGUMENT;
    return __real_pc98qr_manual_selection_init(state, count);
}

int __real_pc98qr_manual_selection_input(struct pc98qr_manual_selection *, unsigned int);
int __wrap_pc98qr_manual_selection_input(struct pc98qr_manual_selection *state, unsigned int key)
{
    ++inputs; record("input");
    if (failing("input", inputs)) return PC98QR_MANUAL_ERROR_ARGUMENT;
    return __real_pc98qr_manual_selection_input(state, key);
}

int __real_qr_encode_v40l_bytes(const unsigned char *, unsigned int, unsigned char *);
int __wrap_qr_encode_v40l_bytes(const unsigned char *data, unsigned int length, unsigned char *out)
{
    int result;
    ++encodes; record("encode");
    if (preflight) legacy_buffer = data;
    if (manual_calls) {
        assert(data == frame_seen && out == matrix_seen && !shutdowns);
        assert(!prepared && length == serialized_length && length == last_layout.frame_length);
    }
    if (failing("encode", encodes)) return QR_ERROR_LENGTH;
    result = __real_qr_encode_v40l_bytes(data, length, out);
    if (manual_calls && result == QR_OK) {
        prepared = 1;
        wire_length = length;
        printf("{\"kind\":\"encode\",\"offset\":%lu,\"wire\":\"", last_offset);
        hex(data, length);
        printf("\",\"matrix\":\""); hex(out, 3917);
        printf("\",\"tail\":\""); hex(data + length, 2954 - length);
        puts("\"}");
    }
    return result;
}

int __real_qr_encode_v1q_bytes(const unsigned char *, unsigned int, unsigned char [21][21]);
int __wrap_qr_encode_v1q_bytes(const unsigned char *data, unsigned int length, unsigned char out[21][21])
{
    if (preflight) legacy_buffer = data;
    return __real_qr_encode_v1q_bytes(data, length, out);
}

void graphics_init(void)
{
    assert(!active && !inits && !shutdowns);
    if (manual_calls) assert(encodes == 1 && prepared);
    active = 1; ++inits; record("init");
}

void screen_clear(void)
{
    assert(active); ++clears; record("clear");
    if (manual_calls) assert(prepared);
    memset(planes, 255, sizeof(planes)); redraw_pending = 1;
}

void box_fill(unsigned int x, unsigned int y, unsigned int w, unsigned int h)
{
    unsigned int p, row, col;
    assert(active && x + w <= 640 && y + h <= 400);
    if (manual_calls) assert(w == 2 && h == 2 && x >= 143 && x < 497 && y >= 23 && y < 377);
    for (p = 0; p < 4; ++p)
        for (row = y; row < y + h; ++row)
            for (col = x; col < x + w; ++col)
                planes[p][row * 80 + col / 8] &= (unsigned char)~(128u >> (col % 8));
}

void pc98qr_status_font_store(uint16_t segment, uint16_t offset, uint8_t value)
{
    unsigned int p = segment == 0xA800 ? 0 : segment == 0xB000 ? 1 : segment == 0xB800 ? 2 : 3;
    assert(active && offset < 32000 && (p != 3 || segment == 0xE000));
    planes[p][offset] = value;
}

int __wrap_pc98qr_status_text(unsigned int x, unsigned int y, const char *text, unsigned int length)
{
    int result;
    ++statuses; record("status");
    if (failing("status", statuses)) return PC98QR_STATUS_ERROR_ARGUMENT;
    result = pc98qr_status_text(x, y, text, length);
    if (result == PC98QR_STATUS_OK && y == 48) { assert(length == 11); memcpy(current, text, 11); }
    if (result == PC98QR_STATUS_OK && y == 96) {
        assert(length == 5); memcpy(selection, text, 5);
        if (redraw_pending) {
            unsigned int p;
            for (p = 1; p < 4; ++p) assert(memcmp(planes[0], planes[p], 32000) == 0);
            printf("{\"kind\":\"display\",\"current\":\"%s\",\"select\":\"%s\",\"length\":%u,\"plane\":\"",
                   current, selection, wire_length);
            hex(planes[0], 32000); puts("\"}");
            redraw_pending = 0;
        }
    }
    return result;
}

void graphics_show(void) { assert(active && !shows); ++shows; record("show"); }
void wait_key(void) { assert(active && !manual_calls); ++waits; record("wait"); }
void graphics_shutdown(void) { assert(active && !shutdowns); active = 0; ++shutdowns; record("shutdown"); }

ssize_t __real_write(int, const void *, size_t);
ssize_t __wrap_write(int fd, const void *data, size_t length)
{
    const char *budget_text = getenv("QRTEST_OUTPUT_BUDGET");
    const char *limit_text = getenv("QRTEST_WRITE_LIMIT");
    assert(!active);
    if (fd == STDERR_FILENO) {
        record("error");
        if (getenv("QRTEST_STDERR_FAIL")) return -1;
        if (getenv("QRTEST_STDERR_ZERO")) return 0;
    }
    if (limit_text && length > strtoul(limit_text, NULL, 10)) length = strtoul(limit_text, NULL, 10);
    if (fd != STDOUT_FILENO) return __real_write(fd, data, length);
    assert(!inits && !chunks && !encodes);
    if (budget_text) {
        size_t budget = strtoul(budget_text, NULL, 10);
        if (information_length >= budget) return getenv("QRTEST_STDOUT_ZERO") ? 0 : -1;
        if (length > budget - information_length) length = budget - information_length;
    }
    assert(information_length + length < sizeof(information));
    memcpy(information + information_length, data, length); information_length += length;
    return (ssize_t)length;
}

static enum pc98qr_transfer_result observed_transfer(const char *path, unsigned char *frame,
    unsigned int fc, unsigned char *output, unsigned int mc)
{
    const char *override = getenv("QRTEST_RESULT");
    ++manual_calls;
    assert(manual_calls == 1 && path == expected_path);
    assert(fc == 2954 && mc == 3917 && output == matrix.v40);
    assert((uintptr_t)frame + fc <= (uintptr_t)output || (uintptr_t)output + mc <= (uintptr_t)frame);
    if (legacy_buffer) assert(frame == legacy_buffer);
    frame_capacity_seen = fc; matrix_capacity_seen = mc;
    /*
     * 任意のAPI境界試験。
     * mainの実引数は先に検証済み。
     * 境界保護した領域はホスト専用で、本番は既存の二つの領域を使う。
     */
    /* Optional API boundary test: main's real arguments were checked above.
     * The guarded storage is host-only; production keeps its two old objects.
     */
    if (getenv("QRTEST_GUARDS")) {
        memset(&guarded_frame, 0xA5, sizeof(guarded_frame));
        memset(&guarded_matrix, 0x5A, sizeof(guarded_matrix));
        frame = guarded_frame.bytes; output = guarded_matrix.bytes;
        fc = !strcmp(getenv("QRTEST_GUARDS"), "exact") ? 2953 : 2954;
        mc = !strcmp(getenv("QRTEST_GUARDS"), "exact") ? 3917 : 3918;
    }
    frame_seen = frame; matrix_seen = output;
    memset(frame, 0xA5, fc); memset(output, 0x5A, mc);
    /*
     * enum注入は呼出し側の終了コード変換だけを試す。
     * 操作試験では実際に別コンパイルした制御部を動かす。
     */
    /* Enum injection tests only the caller mapping; all interaction tests
     * below this boundary execute the actual separate controller object. */
    {
        const char *bad = getenv("QRTEST_BAD_ARGUMENT");
        internal_result = override ? atoi(override) : (int)pc98qr_manual_transfer_v2(
            bad && !strcmp(bad, "path") ? NULL : path,
            bad && !strcmp(bad, "frame") ? NULL : frame,
            bad && !strcmp(bad, "fc") ? 2952 : fc,
            bad && !strcmp(bad, "matrix") ? NULL : output,
            bad && !strcmp(bad, "mc") ? 3916 : mc);
    }
    assert(frame[2953] == 0xA5);
    if (getenv("QRTEST_GUARDS")) {
        unsigned int i;
        assert(guarded_matrix.bytes[3917] == 0x5A);
        for (i = 0; i < 16; ++i) {
            assert(guarded_frame.before[i] == 0xA5 && guarded_frame.after[i] == 0xA5);
            assert(guarded_matrix.before[i] == 0x5A && guarded_matrix.after[i] == 0x5A);
        }
    }
    return (enum pc98qr_transfer_result)internal_result;
}

int main(int argc, char **argv)
{
    int result;
    const char *reuse = getenv("QRTEST_REUSE_FILE");
    events = getenv("QRTEST_KEYS"); if (!events) events = "001b";
    fault = getenv("QRTEST_FAULT"); if (!fault) fault = "none";
    if (reuse) {
        char *args[] = {argv[0], "/F", (char *)reuse};
        preflight = 1;
        assert(qrtest_main(3, args) == 0 && legacy_buffer);
        preflight = 0;
        inits = clears = shows = waits = shutdowns = encodes = 0;
        trace[0] = '\0';
    }
    expected_path = argc > 2 ? argv[2] : NULL;
    result = qrtest_main(argc, argv);
    assert(!active);
    printf("{\"kind\":\"summary\",\"result\":%d,\"internal\":%d,\"manual\":%u,"
           "\"capacity\":[%u,%u],\"reuse\":%d,\"sizes\":%u,\"chunks\":%u,\"encodes\":%u,"
           "\"inits\":%u,\"clears\":%u,\"shows\":%u,\"waits\":%u,\"shutdowns\":%u,\"trace\":\"%s\",\"information\":\"",
           result, internal_result, manual_calls, frame_capacity_seen, matrix_capacity_seen,
           legacy_buffer != NULL, sizes, chunks, encodes, inits, clears, shows, waits, shutdowns, trace);
    hex(information, information_length); puts("\"}");
    return result;
}
