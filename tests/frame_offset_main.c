/*
 * 公開APIのホスト専用ドライバー。
 * frame_offset.cは別モジュールとしてコンパイルする。
 */
/* Host-only public API driver; compile frame_offset.c as a separate module. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/frame_offset.h"
#include "../src/frame_count.h"

#define SENTINEL 0xA55A5AA5UL

static unsigned short parse_ushort(const char *text)
{
    char *end;
    unsigned long value;

    errno = 0;
    value = strtoul(text, &end, 10);
    assert(text[0] >= '0' && text[0] <= '9' && *end == '\0');
    assert(errno == 0 && value <= USHRT_MAX);
    return (unsigned short)value;
}

int main(int argc, char **argv)
{
    unsigned long output[3] = {0x13579BDFUL, SENTINEL, 0x2468ACE0UL};
    unsigned short index;
    unsigned short count;
    int result;

    assert(argc >= 2);
    if (strcmp(argv[1], "constants") == 0) {
        assert(argc == 2);
        printf("{\"payload_max\":%lu,\"count_max\":%u,\"char_bit\":%d,"
               "\"ushort_max\":%u,\"ulong_max\":%lu}\n",
               PC98QR_FRAME_PAYLOAD_MAX, PC98QR_FRAME_COUNT_MAX, CHAR_BIT,
               (unsigned int)USHRT_MAX, ULONG_MAX);
        return 0;
    }

    assert(argc == 3 || (argc == 4 && strcmp(argv[3], "null") == 0));
    index = parse_ushort(argv[1]);
    count = parse_ushort(argv[2]);
    result = pc98qr_frame_offset_for_index(index, count,
                                         argc == 4 ? NULL : &output[1]);
    assert(output[0] == 0x13579BDFUL && output[2] == 0x2468ACE0UL);
    if (result != PC98QR_FRAME_OFFSET_OK)
        assert(output[1] == SENTINEL);
    printf("{\"result\":%d,\"offset\":%lu}\n", result, output[1]);
    return 0;
}
