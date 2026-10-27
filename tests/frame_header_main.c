/*
 * 公開APIのホスト専用ドライバー。
 * frame_header.cは別モジュールとしてコンパイルする。
 */
/* Host-only public API driver; compile frame_header.c as a separate module. */
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/frame_header.h"

static unsigned short number(const char *text)
{
    char *end;
    unsigned long value = strtoul(text, &end, 10);
    assert(text[0] != '\0' && *end == '\0' && value <= 65535UL);
    return (unsigned short)value;
}

static void hex(const unsigned char *bytes, unsigned int length)
{
    unsigned int i;
    putchar('"');
    for (i = 0; i < length; ++i)
        printf("%02x", (unsigned int)bytes[i]);
    putchar('"');
}

static void serialize(unsigned short index, unsigned short count, int null_out)
{
    struct {
        unsigned char before[16];
        unsigned char bytes[PC98QR_FRAME_HEADER_SIZE];
        unsigned char after[16];
    } output;
    unsigned int i;
    int result;

    memset(&output, 0xA5, sizeof(output));
    result = pc98qr_frame_header_serialize(index, count,
                                           null_out ? NULL : output.bytes);
    for (i = 0; i < sizeof(output.before); ++i) {
        assert(output.before[i] == 0xA5);
        assert(output.after[i] == 0xA5);
    }
    if (result != PC98QR_FRAME_HEADER_OK)
        for (i = 0; i < sizeof(output.bytes); ++i)
            assert(output.bytes[i] == 0xA5);
    printf("{\"result\":%d,\"bytes\":", result);
    hex(output.bytes, sizeof(output.bytes));
    puts("}");
}

static void parse(const char *text, unsigned int null_mask)
{
    unsigned int length = (unsigned int)(strlen(text) / 2);
    unsigned char *bytes = malloc(length ? length : 1);
    unsigned char *original = malloc(length ? length : 1);
    unsigned short outputs[] = {0x1357, 0xA55A, 0x2468, 0x5AA5, 0x369C};
    unsigned int i;
    int result;

    assert(strlen(text) % 2 == 0 && bytes != NULL && original != NULL);
    assert(null_mask <= 7);
    for (i = 0; i < length; ++i) {
        unsigned int value;
        assert(sscanf(text + 2 * i, "%2x", &value) == 1);
        bytes[i] = (unsigned char)value;
    }
    memcpy(original, bytes, length);
    result = pc98qr_frame_header_parse(null_mask & 1 ? NULL : bytes, length,
                                        null_mask & 2 ? NULL : &outputs[1],
                                        null_mask & 4 ? NULL : &outputs[3]);
    assert(memcmp(bytes, original, length) == 0);
    assert(outputs[0] == 0x1357 && outputs[2] == 0x2468 && outputs[4] == 0x369C);
    if (result != PC98QR_FRAME_HEADER_OK)
        assert(outputs[1] == 0xA55A && outputs[3] == 0x5AA5);
    printf("{\"result\":%d,\"index\":%u,\"count\":%u}\n", result,
           (unsigned int)outputs[1], (unsigned int)outputs[3]);
    free(original);
    free(bytes);
}

static void read_bound(void)
{
    const unsigned char header[9] = {0x50, 0x39, 0x38, 0x51, 1, 1, 0xF4, 1, 0xF5};
    unsigned char *bytes = malloc(sizeof(header));
    unsigned short index = 0, count = 0;

    assert(bytes != NULL);
    memcpy(bytes, header, sizeof(header));
    /*
     * アクセス可能なのは9バイトだけ。
     * 申告長が大きくてもASanで境界を検査し、ペイロードを読まないことを確認する。
     */
    /* Read-footprint probe: only 9 bytes are accessible. ASan checks the bound
     * even when the advertised length is larger; payload must not be read. */
    assert(pc98qr_frame_header_parse(bytes, UINT_MAX, &index, &count) ==
           PC98QR_FRAME_HEADER_OK);
    assert(index == 500 && count == 501);
    free(bytes);
    puts("true");
}

int main(int argc, char **argv)
{
    assert(argc >= 2);
    if (strcmp(argv[1], "constants") == 0) {
        printf("{\"size\":%d,\"version\":%d,\"uchar_max\":%u,"
               "\"ushort_max\":%u,\"char_bit\":%d,\"ushort_size\":%u}\n",
               PC98QR_FRAME_HEADER_SIZE, PC98QR_FRAME_PROTOCOL_VERSION,
               (unsigned int)UCHAR_MAX, (unsigned int)USHRT_MAX, CHAR_BIT,
               (unsigned int)sizeof(unsigned short));
    } else if (strcmp(argv[1], "serialize") == 0) {
        assert(argc == 4 || argc == 5);
        serialize(number(argv[2]), number(argv[3]), argc == 5);
    } else if (strcmp(argv[1], "parse") == 0) {
        assert(argc == 3 || argc == 4);
        parse(argv[2], argc == 4 ? number(argv[3]) : 0);
    } else if (strcmp(argv[1], "read-bound") == 0) {
        read_bound();
    } else {
        return 2;
    }
    return 0;
}
