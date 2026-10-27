/*
 * 実QUPD・フォント・ヘッダー・符号化器と模擬BIOS/GVRAMを組み合わせる。
 * 大きな画面・スナップショット・書き込み表はホスト専用。
 */
/* QUPD host boundary: real main/font/header/encoder, simulated BIOS and GVRAM.
 * All large planes, snapshots and store maps below are host-only storage.
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../src/frame_header.h"
#include "../src/qr_encode.h"
#include "../src/pc98_status_font.h"

static int observed_header(unsigned short, unsigned short, unsigned char *);
static int observed_encode(const unsigned char *, unsigned int, unsigned char *);
static int observed_fixed(void);
static int observed_text(unsigned int, unsigned int, const char *, unsigned int);
#define pc98qr_frame_header_serialize observed_header
#define qr_encode_v40l_bytes observed_encode
#define pc98qr_status_fixed observed_fixed
#define pc98qr_status_text observed_text
#define main qupd_main
#include "../tools/qr_status_update_probe.c"
#undef main
#undef pc98qr_frame_header_serialize
#undef qr_encode_v40l_bytes
#undef pc98qr_status_fixed
#undef pc98qr_status_text

#define PC98QR_STATUS_FONT_HOST_TEST
#include "../src/pc98_status_font.c"

enum phase { BOOT, INIT, QR, INITIAL, READY, VISIBLE, KEY, UPDATE, FAILED, DONE };
static enum phase phase;
static struct {
    unsigned char before[32];
    unsigned char bytes[32000];
    unsigned char after[32];
} planes[4];
static unsigned char qr_planes[4][32000];
static unsigned char snapshots[5][4][32000];
static unsigned char stores[5][4][32000];
static unsigned char matrix_before[QR_V40_PACKED_BYTES];
static unsigned char matrix_snapshots[5][QR_V40_PACKED_BYTES];
static unsigned char frame_before[QR_V40L_BYTE_MAX];
static unsigned int writes[5][4];
static unsigned int epoch, headers, encodes, inits, clears, qr_passes, boxes;
static unsigned int initials, shows, updates, completed, waits, shutdowns;
static char trace[256];
static const char *fault;

static int failing(const char *name)
{
    return fault != NULL && strcmp(fault, name) == 0;
}

static void record(const char *event)
{
    assert(strlen(trace) + strlen(event) + 2 < sizeof(trace));
    if (*trace) strcat(trace, ",");
    strcat(trace, event);
}

static int update_byte(unsigned int offset)
{
    unsigned int row = offset / 80, col = offset % 80;
    return row >= 96 && row <= 111 && col >= 65 && col <= 69;
}

static int initial_byte(unsigned int offset)
{
    unsigned int row = offset / 80, col = offset % 80;
    if (col < 65) return 0;
    return (row >= 32 && row <= 47 && col <= 71)
        || (row >= 48 && row <= 63 && col <= 68)
        || (row >= 80 && row <= 95 && col <= 70)
        || (row >= 96 && row <= 111 && col == 65);
}

static void check_integrity(void)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p) {
        for (i = 0; i < 32; ++i) {
            assert(planes[p].before[i] == 0xA5);
            assert(planes[p].after[i] == 0x5A);
        }
    }
    if (inits) {
        assert(memcmp(fixed_matrix, matrix_before, sizeof(matrix_before)) == 0);
        assert(memcmp(fixed_frame, frame_before, sizeof(frame_before)) == 0);
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
    assert(offset < 32000 && epoch < 5);
    assert(phase == INITIAL || phase == UPDATE);
    assert(phase == INITIAL ? initial_byte(offset) : update_byte(offset));
    /*
     * 描画段階ごとに別の表を使う。
     * 更新をまたぐ同位置書き込みは許すが、同じ段階での重複は常に不正とする。
     */
    /* Separate map per epoch: cross-update writes legal, duplicates within
     * the same initial/update drawing stage always illegal. */
    assert(stores[epoch][p][offset] == 0);
    ++stores[epoch][p][offset];
    ++writes[epoch][p];
    planes[p].bytes[offset] = value;
}

static int observed_header(unsigned short index, unsigned short count,
                           unsigned char *out)
{
    assert(phase == BOOT && headers == 0 && encodes == 0);
    assert(index == 0 && count == 24 && out == fixed_frame);
    ++headers;
    record("header");
    return failing("header") ? PC98QR_FRAME_HEADER_ERROR_ARGUMENT
                             : pc98qr_frame_header_serialize(index, count, out);
}

static int observed_encode(const unsigned char *data, unsigned int length,
                           unsigned char *out)
{
    int result;
    assert(phase == BOOT && headers == 1 && encodes == 0 && !failing("header"));
    assert(data == fixed_frame && length == 2953 && out == fixed_matrix);
    ++encodes;
    record("encode");
    if (failing("encoder")) return QR_ERROR_LENGTH;
    result = qr_encode_v40l_bytes(data, length, out);
    assert(result == QR_OK);
    memcpy(matrix_before, out, sizeof(matrix_before));
    memcpy(frame_before, data, sizeof(frame_before));
    return result;
}

void graphics_init(void)
{
    assert(phase == BOOT && encodes == 1 && !failing("encoder") && inits == 0);
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

void box_fill(unsigned int x, unsigned int y,
              unsigned int width, unsigned int height)
{
    unsigned int p, dx, dy;
    assert(phase == QR && width == 2 && height == 2);
    assert(x >= 143 && x <= 495 && (x - 143) % 2 == 0);
    assert(y >= 23 && y <= 375 && (y - 23) % 2 == 0);
    if (boxes == 0) {
        ++qr_passes;
        record("qr");
    }
    ++boxes;
    for (dy = 0; dy < 2; ++dy) {
        for (dx = 0; dx < 2; ++dx) {
            unsigned int offset = (y + dy) * 80 + (x + dx) / 8;
            unsigned char bit = (unsigned char)(0x80u >> ((x + dx) % 8));
            for (p = 0; p < 4; ++p) {
                assert(planes[p].bytes[offset] & bit); /* No duplicate QR pixels. */ /* QR画素の重複書き込みを許さない。 */
                planes[p].bytes[offset] &= (unsigned char)~bit;
            }
        }
    }
}

/*
 * 白や値が変わらない書き込みも含め、全位置を検証する。
 * 最終画面だけでは余計な書き込みや白での上書き漏れを検出できない。
 */
/* Verify ALL offsets, including white/no-change stores; checking the final
 * framebuffer alone would miss an extra store or an omitted white overwrite. */
static void check_epoch(const unsigned char before[4][32000], int failed)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p) {
        assert(writes[epoch][p] == (failed ? 0u : epoch == 0 ? 288u : 80u));
        for (i = 0; i < 32000; ++i) {
            unsigned int wanted = !failed && (epoch == 0 ? initial_byte(i) : update_byte(i));
            assert(stores[epoch][p][i] == wanted);
            if (!wanted) assert(planes[p].bytes[i] == before[p][i]);
        }
    }
    check_integrity();
}

static int observed_fixed(void)
{
    unsigned int p, i, black = 0;
    int failed = failing("initial"), result;
    assert(phase == QR && initials == 0 && qr_passes == 1);
    ++initials;
    record("initial");
    check_integrity();
    for (i = 0; i < 31329; ++i) black += (fixed_matrix[i / 8] >> (7 - i % 8)) & 1;
    assert(boxes == black);
    for (p = 0; p < 4; ++p) memcpy(qr_planes[p], planes[p].bytes, 32000);
    phase = INITIAL;
    epoch = 0;
    result = failed ? PC98QR_STATUS_ERROR_ARGUMENT : pc98qr_status_fixed();
    assert(result == (failed ? PC98QR_STATUS_ERROR_ARGUMENT : PC98QR_STATUS_OK));
    check_epoch(qr_planes, failed);
    phase = failed ? FAILED : READY;
    return result;
}

void graphics_show(void)
{
    assert(phase == READY && shows == 0 && initials == 1);
    ++shows;
    record("show");
    phase = VISIBLE;
}

void wait_key(void)
{
    unsigned int p;
    static const char *const names[] = {"wait1", "wait2", "wait3", "wait4", "wait5"};
    assert(phase == VISIBLE && shows == 1 && waits < 5);
    assert(updates == waits && completed == updates);
    check_integrity();
    for (p = 0; p < 4; ++p) memcpy(snapshots[waits][p], planes[p].bytes, 32000);
    memcpy(matrix_snapshots[waits], fixed_matrix, sizeof(fixed_matrix));
    record(names[waits]);
    ++waits; /* One simulated consumed event, no keyboard classification. */ /* 消費済みイベント一つを模擬し、キー分類は行わない。 */
    phase = KEY;
}

static int observed_text(unsigned int x, unsigned int y, const char *text,
                         unsigned int length)
{
    static const char *const names[] = {"update1", "update2", "update3", "update4"};
    static const char *const expected[] = {"23   ", "2    ", "00023", "-    "};
    int failed, result;
    assert(phase == KEY && updates < 4 && waits == updates + 1 && shows == 1);
    assert(x == 520 && y == 96 && length == 5 && text != NULL);
    assert(memcmp(text, expected[updates], 5) == 0);
    check_integrity();
    failed = failing(names[updates]);
    record(names[updates]);
    epoch = ++updates;
    phase = UPDATE;
    /*
     * 不正なフォント入力を作らず、呼出し境界で戻り値だけを注入する。
     */
    /* Inject the return at the call boundary; never manufacture bad font input. */
    result = failed ? PC98QR_STATUS_ERROR_ARGUMENT : pc98qr_status_text(x, y, text, length);
    assert(result == (failed ? PC98QR_STATUS_ERROR_ARGUMENT : PC98QR_STATUS_OK));
    check_epoch(snapshots[epoch - 1], failed);
    if (!failed) ++completed;
    phase = failed ? FAILED : VISIBLE;
    return result;
}

void graphics_shutdown(void)
{
    assert(inits == 1 && shutdowns == 0);
    assert(phase == FAILED || (phase == KEY && waits == 5 && completed == 4));
    ++shutdowns;
    record("shutdown");
    check_integrity();
    phase = DONE;
}

static void output_info(int result)
{
    unsigned int e;
    printf("{\"result\":%d,\"trace\":\"%s\",\"headers\":%u,\"encodes\":%u,"
           "\"inits\":%u,\"clears\":%u,\"qr_passes\":%u,\"boxes\":%u,"
           "\"initials\":%u,\"shows\":%u,\"updates\":%u,\"completed\":%u,"
           "\"waits\":%u,\"shutdowns\":%u,\"writes\":[",
           result, trace, headers, encodes, inits, clears, qr_passes, boxes,
           initials, shows, updates, completed, waits, shutdowns);
    for (e = 0; e < 5; ++e)
        printf("%s[%u,%u,%u,%u]", e ? "," : "", writes[e][0], writes[e][1], writes[e][2], writes[e][3]);
    puts("]}");
}

int main(int argc, char **argv)
{
    unsigned int p, i;
    int result;
    char *probe_argv[] = {"QUPD", "unexpected", NULL};
    assert((argc == 2 || argc == 3) && strcmp(argv[1], "capture") == 0);
    if (argc == 3) {
        fault = argv[2];
        assert(failing("usage") || failing("header") || failing("encoder") || failing("initial")
               || failing("update1") || failing("update2") || failing("update3") || failing("update4"));
    }
    for (p = 0; p < 4; ++p) {
        memset(planes[p].before, 0xA5, 32);
        memset(planes[p].bytes, 0x21 + 0x33 * p, 32000);
        memset(planes[p].after, 0x5A, 32);
    }
    result = qupd_main(failing("usage") ? 2 : 1, probe_argv);
    check_integrity();
    if (result == 0) {
        assert(fault == NULL && phase == DONE && waits == 5 && completed == 4);
    } else {
        assert(fault != NULL);
        assert(result == (failing("usage") ? 1 : failing("header") ? 2 : failing("encoder") ? 3 : 4));
        assert(phase == (inits ? DONE : BOOT));
        assert(shutdowns == inits);
        if (!inits)
            for (p = 0; p < 4; ++p)
                for (i = 0; i < 32000; ++i)
                    assert(planes[p].bytes[i] == 0x21 + 0x33 * p);
    }
    output_info(result);
    if (result != 0) return result;
    /*
     * JSON行に続けて固定長バイナリを出力する。
     * 5枚の画面は一度の実行中、5番目のイベントで終了する前に取得したもの。
     */
    /* Fixed-size binary blocks after the JSON line; all five snapshots came
     * from one complete run, before the fifth event shuts graphics down. */
#define OUTPUT(value) assert(fwrite(value, 1, sizeof(value), stdout) == sizeof(value))
    OUTPUT(fixed_frame);
    OUTPUT(matrix_before);
    OUTPUT(qr_planes);
    OUTPUT(snapshots);
    OUTPUT(matrix_snapshots);
    OUTPUT(stores);
#undef OUTPUT
    return 0;
}
