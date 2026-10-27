/*
 * 実probeと字形ループ・物理書き込み引数を使うホスト試験。
 * VRAMの代わりに境界を保護した32000バイトの4プレーンを用意する。
 */
/* Host-only hardware boundary: actual probe, glyph loops and physical store
 * arguments; four guarded 32000-byte planes instead of memory-mapped VRAM.
 */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/frame_header.h"
#include "../src/qr_encode.h"
#include "../src/pc98_status_font.h"

static int observed_header(unsigned short, unsigned short, unsigned char *);
static int observed_encode(const unsigned char *, unsigned int, unsigned char *);
static int observed_status(void);
#define pc98qr_frame_header_serialize observed_header
#define qr_encode_v40l_bytes observed_encode
#define pc98qr_status_fixed observed_status
#define main qstatus_main
#include "../tools/qr_status_probe.c"
#undef main
#undef pc98qr_frame_header_serialize
#undef qr_encode_v40l_bytes
#undef pc98qr_status_fixed

#define PC98QR_STATUS_FONT_HOST_TEST
#include "../src/pc98_status_font.c"

static struct {
    unsigned char before[32];
    unsigned char bytes[32000];
    unsigned char after[32];
} planes[4];
static unsigned char stores[4][32000];
static unsigned char before_overlay[4][32000];
static unsigned char matrix_before[QR_V40_PACKED_BYTES];
static unsigned int writes[4];
static unsigned int stage, headers, encodes, boxes, status_calls, shutdowns;
static int font_only, qr_only;
static const char *fault;

static int failing(const char *name)
{
    return fault != NULL && strcmp(fault, name) == 0;
}

static void initialize_planes(void)
{
    unsigned int p;
    for (p = 0; p < 4; ++p) {
        memset(planes[p].before, 0xA5, 32);
        memset(planes[p].bytes, 0x21 + 0x33 * p, 32000);
        memset(planes[p].after, 0x5A, 32);
    }
}

static void check_guards(void)
{
    unsigned int p, i;
    for (p = 0; p < 4; ++p) {
        for (i = 0; i < 32; ++i) {
            assert(planes[p].before[i] == 0xA5);
            assert(planes[p].after[i] == 0x5A);
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
    assert(font_only || stage == 3);
    assert(offset < 32000 && offset % 80 >= 65); /* Includes X=512..519 guard. */ /* X=512～519の境界監視領域も含む。 */
    assert(stores[p][offset] == 0); /* No hidden clear/redraw or duplicate stores. */ /* 隠れた消去・再描画・重複書き込みを許さない。 */
    ++stores[p][offset];
    ++writes[p];
    planes[p].bytes[offset] = value;
}

static int observed_header(unsigned short index, unsigned short count,
                           unsigned char *out)
{
    assert(stage == 0 && headers == 0 && encodes == 0);
    assert(index == 0 && count == 24 && out == fixed_frame);
    ++headers;
    return failing("header") ? PC98QR_FRAME_HEADER_ERROR_ARGUMENT
                             : pc98qr_frame_header_serialize(index, count, out);
}

static int observed_encode(const unsigned char *data, unsigned int length,
                           unsigned char *out)
{
    int result;
    assert(stage == 0 && headers == 1 && encodes == 0);
    assert(length == 2953 && data == fixed_frame && out == fixed_matrix);
    ++encodes;
    if (failing("encoder"))
        return QR_ERROR_LENGTH;
    result = qr_encode_v40l_bytes(data, length, out);
    assert(result == QR_OK);
    memcpy(matrix_before, out, sizeof(matrix_before));
    return result;
}

void graphics_init(void)
{
    assert(stage == 0 && encodes == 1 && !failing("encoder"));
    stage = 1;
}

void screen_clear(void)
{
    unsigned int p;
    assert(stage == 1);
    for (p = 0; p < 4; ++p)
        memset(planes[p].bytes, 255, 32000);
    stage = 2;
}

void box_fill(unsigned int x, unsigned int y,
              unsigned int width, unsigned int height)
{
    unsigned int p, dx, dy;
    assert(stage == 2 && width == 2 && height == 2);
    assert(x >= 143 && x <= 495 && (x - 143) % 2 == 0);
    assert(y >= 23 && y <= 375 && (y - 23) % 2 == 0);
    ++boxes;
    for (dy = 0; dy < 2; ++dy) {
        for (dx = 0; dx < 2; ++dx) {
            unsigned int offset = (y + dy) * 80 + (x + dx) / 8;
            unsigned char mask = (unsigned char)(0x80u >> ((x + dx) % 8));
            for (p = 0; p < 4; ++p) {
                assert((planes[p].bytes[offset] & mask) != 0);
                planes[p].bytes[offset] &= (unsigned char)~mask;
            }
        }
    }
}

static int observed_status(void)
{
    unsigned int p, i, black = 0;
    int result;
    assert(stage == 2 && status_calls == 0);
    ++status_calls;
    for (i = 0; i < 31329; ++i)
        black += (fixed_matrix[i / 8] >> (7 - i % 8)) & 1;
    assert(boxes == black);
    assert(memcmp(matrix_before, fixed_matrix, sizeof(matrix_before)) == 0);
    for (p = 0; p < 4; ++p)
        memcpy(before_overlay[p], planes[p].bytes, 32000);
    stage = 3;
    result = failing("status") ? PC98QR_STATUS_ERROR_ARGUMENT
           : qr_only ? PC98QR_STATUS_OK : pc98qr_status_fixed();
    assert(memcmp(matrix_before, fixed_matrix, sizeof(matrix_before)) == 0);
    for (p = 0; p < 4; ++p) {
        assert(writes[p] == (failing("status") || qr_only ? 0u : 18u * 16u));
        for (i = 0; i < 32000; ++i) {
            if (stores[p][i] == 0 || i % 80 < 65)
                assert(planes[p].bytes[i] == before_overlay[p][i]);
        }
    }
    return result;
}

void graphics_show(void)
{
    assert(stage == 3 && status_calls == 1 && !failing("status"));
    stage = 4;
}

void wait_key(void)
{
    assert(stage == 4);
    stage = 5;
}

void graphics_shutdown(void)
{
    assert(stage == 5 || (stage == 3 && failing("status")));
    assert(shutdowns++ == 0);
    stage = 6;
}

static unsigned int number(const char *text)
{
    char *end;
    unsigned long value;
    errno = 0;
    value = strtoul(text, &end, 10);
    assert(errno == 0 && *text != '\0' && *end == '\0' && value <= UINT_MAX);
    return (unsigned int)value;
}

static int font_request(int argc, char **argv)
{
    size_t size, i;
    unsigned char *bytes;
    int result;
    assert(argc == 6 || argc == 7);
    font_only = 1;
    size = strlen(argv[4]);
    assert(size % 2 == 0);
    bytes = malloc(size / 2 + (size == 0)); /* Host-only exact-sized input. */ /* ホスト専用の正確な長さの入力。 */
    assert(bytes != NULL);
    for (i = 0; i < size / 2; ++i) {
        char pair[3] = {argv[4][2*i], argv[4][2*i+1], 0};
        char *end;
        bytes[i] = (unsigned char)strtoul(pair, &end, 16);
        assert(*end == '\0');
    }
    result = pc98qr_status_text(number(argv[2]), number(argv[3]),
                               argc == 7 ? NULL : (char *)bytes, number(argv[5]));
    free(bytes);
    check_guards();
    printf("{\"result\":%d,\"writes\":[%u,%u,%u,%u]}\n", result,
           writes[0], writes[1], writes[2], writes[3]);
    for (i = 0; i < 4; ++i)
        assert(fwrite(planes[i].bytes, 1, 32000, stdout) == 32000);
    return 0;
}

int main(int argc, char **argv)
{
    unsigned int x, y, p;
    int result;
    char *probe_argv[] = {"QSTATUS", "unexpected", NULL};
    assert(argc >= 2);
    initialize_planes();
    if (strcmp(argv[1], "font") == 0)
        return font_request(argc, argv);
    assert(argc == 2 || argc == 3);
    if (argc == 3)
        fault = argv[2];
    qr_only = strcmp(argv[1], "qr") == 0;
    result = qstatus_main(failing("usage") ? 2 : 1, probe_argv);
    check_guards();
    if (result != 0) {
        assert(result == (failing("usage") ? 1 : failing("header") ? 2
                         : failing("encoder") ? 3 : 4));
        assert(headers == (failing("usage") ? 0u : 1u));
        assert(encodes == (failing("usage") || failing("header") ? 0u : 1u));
        assert(stage == (failing("status") ? 6u : 0u));
        assert(shutdowns == (failing("status") ? 1u : 0u));
        for (p = 0; p < 4; ++p) assert(writes[p] == 0);
        return result;
    }
    assert(stage == 6 && headers == 1 && encodes == 1 && status_calls == 1 && shutdowns == 1);
    if (strcmp(argv[1], "wire") == 0)
        return fwrite(fixed_frame, 1, sizeof(fixed_frame), stdout) == sizeof(fixed_frame) ? 0 : 1;
    if (strcmp(argv[1], "matrix") == 0)
        return fwrite(fixed_matrix, 1, sizeof(fixed_matrix), stdout) == sizeof(fixed_matrix) ? 0 : 1;
    printf("P5\n640 400\n255\n");
    for (y = 0; y < 400; ++y) {
        for (x = 0; x < 640; ++x) {
            unsigned int offset = y * 80 + x / 8;
            unsigned int bit = 0x80u >> (x % 8);
            for (p = 1; p < 4; ++p)
                assert(planes[p].bytes[offset] == planes[0].bytes[offset]);
            assert(putchar((planes[0].bytes[offset] & bit) ? 255 : 0) != EOF);
        }
    }
    return 0;
}
