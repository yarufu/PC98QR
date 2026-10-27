/*
 * 実ファイルを扱うホスト専用ラッパー。
 * 本番にはテスト用フックを加えない。
 */
/* Host-only wrappers over real files; production has no test hooks. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

static int observed_open(const char *path, int flags, ...);
static off_t observed_lseek(int fd, off_t offset, int whence);
static ssize_t observed_read(int fd, void *buffer, size_t length);
static int observed_close(int fd);
#define open observed_open
#define lseek observed_lseek
#define read observed_read
#define close observed_close
#include "../src/file_chunk.c"
#undef open
#undef lseek
#undef read
#undef close

#define SENTINEL 0xA55Au
enum {
    FAIL_OPEN = 1, FAIL_SEEK = 2, FAIL_READ = 4, FAIL_CLOSE = 8,
    WRONG_POSITION = 16, FAIL_AFTER_PARTIAL = 32
};
static const char *expected_path;
static unsigned long expected_offset;
static unsigned char *expected_buffer;
static const unsigned int *observed_output;
static unsigned int capacity, faults, plan;
static unsigned int open_calls, seek_calls, read_calls, close_calls, total;
static size_t requests[16];
static ssize_t counts[16];
static int file_fd = -1;

static int observed_open(const char *path, int flags, ...)
{
    int expected_flags = O_RDONLY;
#ifdef O_BINARY
    expected_flags |= O_BINARY;
#endif
    assert(open_calls == 0 && seek_calls == 0 && read_calls == 0 && close_calls == 0);
    assert(path == expected_path && flags == expected_flags);
    assert(*observed_output == SENTINEL);
    ++open_calls;
    if (faults & FAIL_OPEN) {
        errno = EACCES;
        return -1;
    }
    file_fd = open(path, flags);
    return file_fd;
}

static off_t observed_lseek(int fd, off_t offset, int whence)
{
    assert(open_calls == 1 && seek_calls == 0 && read_calls == 0 && close_calls == 0);
    assert(fd >= 0 && fd == file_fd && whence == SEEK_SET);
    assert(offset >= 0 && (unsigned long)offset == expected_offset);
    assert(*observed_output == SENTINEL);
    ++seek_calls;
    if (faults & FAIL_SEEK) {
        errno = ESPIPE;
        return (off_t)-1;
    }
    if (faults & WRONG_POSITION)
        return offset == 0 ? (off_t)1 : (off_t)0;
    /*
     * ホストファイルシステムの制限と独立に表現可能範囲を試す。
     * plan 2は正確なseekと直後のEOFだけを模擬し、ファイル内容は作らない。
     */
    /* Exercise representability limits independently of host filesystem limits.
     * Plan 2 synthesizes an exact seek followed by EOF, never file data. */
    if (plan == 2)
        return offset;
    return lseek(fd, offset, whence);
}

static ssize_t observed_read(int fd, void *buffer, size_t length)
{
    static const size_t pieces[] = {5, 6, 1000, 1000, 933};
    size_t request = length;
    ssize_t count;

    assert(open_calls == 1 && seek_calls == 1 && close_calls == 0);
    assert(!(faults & (FAIL_SEEK | WRONG_POSITION)));
    assert(fd >= 0 && fd == file_fd && total < capacity);
    assert(capacity <= PC98QR_FRAME_PAYLOAD_MAX);
    assert(buffer == expected_buffer + total && length == capacity - total);
    assert(*observed_output == SENTINEL && read_calls < 16);
    requests[read_calls] = length;
    if ((faults & FAIL_READ) || ((faults & FAIL_AFTER_PARTIAL) && read_calls > 0)) {
        errno = EIO;
        count = -1;
    } else if (plan == 2) {
        count = 0;
    } else {
        if (plan == 1) {
            assert(read_calls < sizeof(pieces) / sizeof(pieces[0]));
            if (request > pieces[read_calls])
                request = pieces[read_calls];
        }
        if ((faults & FAIL_AFTER_PARTIAL) && request > 5)
            request = 5;
        count = read(fd, buffer, request);
    }
    counts[read_calls++] = count;
    if (count > 0) {
        assert((size_t)count <= request);
        total += (unsigned int)count;
    }
    return count;
}

static int observed_close(int fd)
{
    int result;
    assert(open_calls == 1 && seek_calls == 1 && close_calls == 0);
    assert(fd >= 0 && fd == file_fd && *observed_output == SENTINEL);
    ++close_calls;
    result = close(fd);
    assert(result == 0);
    file_fd = -1;
    if (faults & FAIL_CLOSE) {
        errno = EIO;
        return -1;
    }
    return result;
}

int main(int argc, char **argv)
{
    unsigned char storage[PC98QR_FRAME_PAYLOAD_MAX + 2];
    unsigned int output[3] = {0x1357u, SENTINEL, 0x2468u};
    unsigned int nulls, i;
    int result;

    if (argc == 2 && strcmp(argv[1], "constants") == 0) {
        printf("{\"payload_max\":%lu,\"long_max\":%lu,\"ulong_max\":%lu,"
               "\"off_bits\":%u,\"off_signed\":%d,\"uint_max\":%u}\n",
               PC98QR_FRAME_PAYLOAD_MAX, (unsigned long)LONG_MAX, ULONG_MAX,
               (unsigned int)(sizeof(off_t) * CHAR_BIT), (off_t)-1 < 0, UINT_MAX);
        return 0;
    }
    assert(argc == 7);
    expected_path = argv[1];
    expected_offset = strtoul(argv[2], NULL, 10);
    capacity = (unsigned int)strtoul(argv[3], NULL, 10);
    faults = (unsigned int)strtoul(argv[4], NULL, 10);
    nulls = (unsigned int)strtoul(argv[5], NULL, 10);
    plan = (unsigned int)strtoul(argv[6], NULL, 10);
    assert(faults <= 63 && nulls <= 7 && plan <= 2);
    memset(storage, 0xCD, sizeof(storage));
    expected_buffer = storage + 1;
    observed_output = &output[1];
    result = pc98qr_read_file_chunk(nulls & 1u ? NULL : expected_path,
                                    expected_offset,
                                    nulls & 2u ? NULL : expected_buffer,
                                    capacity, nulls & 4u ? NULL : &output[1]);
    assert(file_fd == -1 && output[0] == 0x1357u && output[2] == 0x2468u);
    assert(storage[0] == 0xCD && storage[sizeof(storage) - 1] == 0xCD);
    /*
     * 失敗時のバッファ内容は出力ではないので、境界だけを確認する。
     */
    /* Check only bounds on failure: the buffer contents are not an output. */
    if (capacity <= PC98QR_FRAME_PAYLOAD_MAX) {
        for (i = capacity; i < PC98QR_FRAME_PAYLOAD_MAX; ++i)
            assert(expected_buffer[i] == 0xCD);
    }
    if (result != PC98QR_FILE_CHUNK_OK) {
        assert(output[1] == SENTINEL);
    } else {
        assert(output[1] == total && total > 0 && total <= capacity);
        for (i = total; i < PC98QR_FRAME_PAYLOAD_MAX; ++i)
            assert(expected_buffer[i] == 0xCD); /* No padding or terminator. */ /* 埋め草や終端文字を付けない。 */
    }
    printf("{\"result\":%d,\"length\":%u,\"open\":%u,\"seek\":%u,"
           "\"read\":%u,\"close\":%u,\"total\":%u,\"requests\":[",
           result, output[1], open_calls, seek_calls, read_calls, close_calls, total);
    for (i = 0; i < read_calls; ++i)
        printf("%s%lu", i ? "," : "", (unsigned long)requests[i]);
    printf("],\"counts\":[");
    for (i = 0; i < read_calls; ++i)
        printf("%s%ld", i ? "," : "", (long)counts[i]);
    printf("],\"hex\":\"");
    if (result == PC98QR_FILE_CHUNK_OK) {
        for (i = 0; i < total; ++i)
            printf("%02x", (unsigned int)expected_buffer[i]);
    }
    printf("\"}\n");
    return 0;
}
