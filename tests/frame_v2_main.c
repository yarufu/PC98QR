/*
 * 本番ヘルパーを別Cモジュールとしてコンパイルするホスト専用ドライバー。
 * heapとstdioは試験基盤だけで、QRTESTへリンクしない。
 */
/* Host-only driver. Production helpers are compiled as separate C modules.
 * Heap/stdio here are test infrastructure, never linked into QRTEST.
 */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/frame_v2.h"
#include "../src/frame_header.h"
#include "../src/frame_count.h"
#include "../src/frame_offset.h"
#include "../src/qr_encode.h"

#define SENTINEL 0xA55AU
static unsigned char output[65537], saved_output[65537];

static unsigned long number(const char *text)
{
    char *end;
    unsigned long value;
    errno = 0;
    value = strtoul(text, &end, 10);
    assert(*text >= '0' && *text <= '9' && *end == '\0' && errno == 0);
    return value;
}

static unsigned int uint_arg(const char *text)
{
    unsigned long value = number(text);
    assert(value <= UINT_MAX);
    return (unsigned int)value;
}

static unsigned short index_arg(const char *text)
{
    unsigned long value = number(text);
    assert(value <= USHRT_MAX);
    return (unsigned short)value;
}

static unsigned char *unhex(const char *text, unsigned int *length)
{
    size_t i, n = strcmp(text, "-") == 0 ? 0 : strlen(text);
    unsigned char *bytes;
    assert(n % 2 == 0 && n / 2 < 65535);
    *length = (unsigned int)(n / 2);
    bytes = malloc(*length + 1U);
    assert(bytes != NULL);
    for (i = 0; i < n; i += 2) {
        unsigned int byte;
        assert(sscanf(text + i, "%2x", &byte) == 1);
        bytes[i / 2] = (unsigned char)byte;
    }
    bytes[*length] = 0;
    return bytes;
}

static void hex(const unsigned char *bytes, unsigned int length)
{
    unsigned int i;
    for (i = 0; i < length; ++i)
        printf("%02x", bytes[i]);
}

static void check_output(int result, unsigned int length, unsigned int capacity)
{
    assert(output[0] == 0xA5 && output[capacity + 1U] == 0xA5);
    if (result == 0) {
        assert(length <= capacity);
        assert(memcmp(output + length + 1U, saved_output + length + 1U,
                      sizeof(output) - length - 1U) == 0);
    } else {
        assert(length == SENTINEL);
        assert(memcmp(output, saved_output, sizeof(output)) == 0);
    }
}

static void print_frame(int result, unsigned int length)
{
    printf("{\"result\":%d,\"length\":%u,\"hex\":\"", result, length);
    if (result == 0)
        hex(output + 1, length);
    puts("\"}");
}

static void reuse(void)
{
    static unsigned char source[6000];
    const unsigned short indices[] = {0, 1, 2, 0};
    const unsigned int offsets[] = {0, 2935, 5879, 0};
    const unsigned int lengths[] = {2935, 2944, 121, 2935};
    unsigned int i;
    for (i = 0; i < sizeof(source); ++i)
        source[i] = (unsigned char)((37UL*i + 11UL*(i >> 8) + 19UL*(i >> 16) + 3) & 255);
    for (i = 0; i < 4; ++i) {
        unsigned int actual = SENTINEL;
        int result;
        memcpy(saved_output, output, sizeof(output));
        result = pc98qr_frame_v2_serialize((const unsigned char *)"TEST.MAG", 8, 6000,
            indices[i], source + offsets[i], lengths[i], output + 1, 2953, &actual);
        assert(result == 0);
        check_output(result, actual, 2953);
        print_frame(result, actual);
    }
}

static void exact_bounds(void)
{
    unsigned char name[1] = {'A'}, data[1] = {0x1A}, out[12];
    unsigned int length = SENTINEL;
    assert(pc98qr_filename_v2_validate(name, 1) == 0);
    assert(pc98qr_filename_v2_validate(name, 13) == PC98QR_FILENAME_V2_ERROR_NAME);
    assert(pc98qr_frame_v2_serialize(name, 1, 1, 0, data, 1, out, sizeof(out), &length) == 0);
    assert(length == 12 && out[9] == 1 && out[10] == 'A' && out[11] == 0x1A);
    assert(pc98qr_frame_v2_serialize(name, 1, 1, 0, data, 0, out, sizeof(out), &length)
           == PC98QR_FRAME_V2_ERROR_LENGTH);
    puts("true");
}

int main(int argc, char **argv)
{
    unsigned int length = SENTINEL, capacity, mask, n;
    unsigned char *input, *before;
    int result;
    assert(argc >= 2);
    memset(output, 0xA5, sizeof(output));
    memcpy(saved_output, output, sizeof(output));
    if (strcmp(argv[1], "constants") == 0) {
        printf("{\"version\":%u,\"header\":%u,\"max\":%u,\"payload\":%lu,"
               "\"name\":%u,\"count\":%u,\"ulong_max\":%lu,\"uint_max\":%u,"
               "\"qr_max\":%d,\"v1_version\":%d,\"v1_header\":%d}\n",
               PC98QR_FRAME_V2_VERSION, PC98QR_FRAME_V2_HEADER_SIZE, PC98QR_FRAME_V2_MAX,
               PC98QR_FRAME_V2_PAYLOAD_MAX, PC98QR_FILENAME_V2_MAX, PC98QR_FRAME_V2_COUNT_MAX,
               ULONG_MAX, UINT_MAX, QR_V40L_BYTE_MAX, PC98QR_FRAME_PROTOCOL_VERSION,
               PC98QR_FRAME_HEADER_SIZE);
        return 0;
    }
    if (strcmp(argv[1], "reuse") == 0) {
        reuse();
        return 0;
    }
    if (strcmp(argv[1], "bounds") == 0) {
        exact_bounds();
        return 0;
    }
    if (strcmp(argv[1], "v1") == 0) {
        unsigned short count = 0, parsed_index = 0, parsed_count = 0;
        unsigned long offset = 0;
        unsigned char header[9];
        assert(pc98qr_frame_count_for_size(6000, &count) == 0 && count == 3);
        assert(pc98qr_frame_offset_for_index(2, count, &offset) == 0 && offset == 5888);
        assert(pc98qr_frame_header_serialize(2, count, header) == 0);
        assert(memcmp(header, "P98Q\x01\x00\x02\x00\x03", 9) == 0);
        assert(pc98qr_frame_header_parse(header, 9, &parsed_index, &parsed_count) == 0);
        assert(parsed_index == 2 && parsed_count == 3);
        header[4] = 2;
        assert(pc98qr_frame_header_parse(header, 9, &parsed_index, &parsed_count)
               == PC98QR_FRAME_HEADER_ERROR_VERSION);
        puts("true");
        return 0;
    }
    if (strcmp(argv[1], "count") == 0) {
        unsigned short out[3] = {0x1357, SENTINEL, 0x2468};
        assert(argc == 5);
        result = pc98qr_frame_v2_count_for_size(number(argv[2]), uint_arg(argv[3]),
                                               uint_arg(argv[4]) ? NULL : &out[1]);
        assert(out[0] == 0x1357 && out[2] == 0x2468);
        if (result != 0) assert(out[1] == SENTINEL);
        printf("{\"result\":%d,\"count\":%u}\n", result, (unsigned int)out[1]);
        return 0;
    }
    if (strcmp(argv[1], "layout") == 0) {
        struct guarded {
            unsigned long first;
            struct pc98qr_frame_v2_layout value;
            unsigned long last;
        } out, saved;
        assert(argc == 6);
        memset(&out, 0xA5, sizeof(out));
        memcpy(&saved, &out, sizeof(out));
        result = pc98qr_frame_v2_layout_for_index(number(argv[2]), uint_arg(argv[3]),
            index_arg(argv[4]), uint_arg(argv[5]) ? NULL : &out.value);
        assert(out.first == saved.first && out.last == saved.last);
        printf("{\"result\":%d", result);
        if (result != 0) assert(memcmp(&out, &saved, sizeof(out)) == 0);
        else printf(",\"count\":%u,\"offset\":%lu,\"capacity\":%u,\"raw\":%u,"
                    "\"start\":%u,\"complete\":%u", (unsigned int)out.value.frame_count,
                    out.value.file_offset, out.value.file_capacity, out.value.file_length,
                    out.value.payload_offset, out.value.frame_length);
        puts("}");
        return 0;
    }
    assert(argc >= 5);
    input = unhex(argv[2], &n);
    before = malloc(n + 1U);
    assert(before != NULL);
    memcpy(before, input, n + 1U);
    if (strcmp(argv[1], "path") == 0) {
        assert(argc == 5);
        capacity = uint_arg(argv[3]);
        assert(capacity <= 65535);
        mask = uint_arg(argv[4]);
        result = pc98qr_filename_v2_from_path(mask & 1 ? NULL : (char *)input,
            mask & 2 ? NULL : output + 1, capacity, mask & 4 ? NULL : &length);
        check_output(result, length, capacity);
        print_frame(result, length);
    } else if (strcmp(argv[1], "validate") == 0) {
        assert(argc == 5);
        result = pc98qr_filename_v2_validate(uint_arg(argv[4]) ? NULL : input, uint_arg(argv[3]));
        printf("{\"result\":%d}\n", result);
    } else {
        unsigned int chunk_size;
        unsigned char *chunk, *chunk_before;
        assert(strcmp(argv[1], "frame") == 0 && argc == 10);
        chunk = unhex(argv[5], &chunk_size);
        chunk_before = malloc(chunk_size + 1U);
        assert(chunk_before != NULL);
        memcpy(chunk_before, chunk, chunk_size + 1U);
        capacity = uint_arg(argv[7]);
        assert(capacity <= 65535);
        mask = uint_arg(argv[8]);
        result = pc98qr_frame_v2_serialize(mask & 1 ? NULL : input, uint_arg(argv[9]),
            number(argv[3]), index_arg(argv[4]), mask & 2 ? NULL : chunk, uint_arg(argv[6]),
            mask & 4 ? NULL : output + 1, capacity, mask & 8 ? NULL : &length);
        check_output(result, length, capacity);
        assert(memcmp(chunk, chunk_before, chunk_size + 1U) == 0);
        print_frame(result, length);
        free(chunk_before);
        free(chunk);
    }
    assert(memcmp(input, before, n + 1U) == 0);
    free(before);
    free(input);
    return 0;
}
