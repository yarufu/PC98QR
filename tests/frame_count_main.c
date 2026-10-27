/*
 * 公開APIのホスト専用ドライバー。
 * frame_count.cは別モジュールとしてコンパイルする。
 */
/* Host-only public API driver; compile frame_count.c as a separate module. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/frame_count.h"
#include "../src/frame_header.h"
#include "../src/qr_encode.h"

#define SENTINEL 0xA55AU

int main(int argc, char **argv)
{
    unsigned short output[3] = {0x1357U, SENTINEL, 0x2468U};
    unsigned long size;
    char *end;
    int result;

    assert(argc >= 2);
    if (strcmp(argv[1], "constants") == 0) {
        assert(argc == 2);
        printf("{\"payload_max\":%lu,\"count_max\":%u,\"ulong_max\":%lu,"
               "\"ushort_max\":%u,\"decoded_max\":%d,\"header_size\":%d}\n",
               PC98QR_FRAME_PAYLOAD_MAX, PC98QR_FRAME_COUNT_MAX, ULONG_MAX,
               (unsigned int)USHRT_MAX, QR_V40L_BYTE_MAX, PC98QR_FRAME_HEADER_SIZE);
        return 0;
    }

    assert(argc == 2 || (argc == 3 && strcmp(argv[2], "null") == 0));
    errno = 0;
    size = strtoul(argv[1], &end, 10);
    assert(argv[1][0] >= '0' && argv[1][0] <= '9' && *end == '\0');
    assert(errno == 0);
    result = pc98qr_frame_count_for_size(size, argc == 3 ? NULL : &output[1]);
    assert(output[0] == 0x1357U && output[2] == 0x2468U);
    if (result != PC98QR_FRAME_COUNT_OK)
        assert(output[1] == SENTINEL);
    printf("{\"result\":%d,\"count\":%u}\n", result, (unsigned int)output[1]);
    return 0;
}
