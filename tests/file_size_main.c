/*
 * render_mainと同じ方式のホスト専用I/Oラッパー。
 * 本番にテスト用フックはない。
 */
/* Host-only I/O wrappers, following render_main.c; no production test hooks. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>

static int observed_open(const char *path, int flags, ...);
static off_t observed_lseek(int fd, off_t offset, int whence);
static int observed_close(int fd);
#define open observed_open
#define lseek observed_lseek
#define close observed_close
#include "../src/file_size.c"
#undef open
#undef lseek
#undef close

#define SENTINEL 0xA55A5AA5UL
static const char *expected_path;
static const unsigned long *observed_output;
static unsigned int faults;
static unsigned int open_calls, seek_calls, close_calls;
static int file_fd = -1;

static int observed_open(const char *path, int flags, ...)
{
    int expected_flags = O_RDONLY;
#ifdef O_BINARY
    expected_flags |= O_BINARY;
#endif
    assert(open_calls == 0 && seek_calls == 0 && close_calls == 0);
    assert(path == expected_path && flags == expected_flags);
    assert(*observed_output == SENTINEL);
    ++open_calls;
    if (faults & 1u) {
        errno = EACCES;
        return -1;
    }
    file_fd = open(path, flags);
    return file_fd;
}

static off_t observed_lseek(int fd, off_t offset, int whence)
{
    assert(open_calls == 1 && seek_calls == 0 && close_calls == 0);
    assert(fd >= 0 && fd == file_fd);
    assert(offset == (off_t)0 && whence == SEEK_END);
    assert(*observed_output == SENTINEL);
    ++seek_calls;
    if (faults & 2u) {
        errno = ESPIPE;
        return (off_t)-1;
    }
    return lseek(fd, offset, whence);
}

static int observed_close(int fd)
{
    int result;
    assert(open_calls == 1 && seek_calls == 1 && close_calls == 0);
    assert(fd >= 0 && fd == file_fd);
    /*
     * close前にサイズを出力すると、close失敗時のAPI契約に違反する。
     */
    /* Publishing the size before close would violate the API on close error. */
    assert(*observed_output == SENTINEL);
    ++close_calls;
    result = close(fd);
    assert(result == 0);
    file_fd = -1;
    if (faults & 4u) {
        errno = EIO;
        return -1;
    }
    return result;
}

int main(int argc, char **argv)
{
    unsigned long output[3] = {0x13572468UL, SENTINEL, 0x24681357UL};
    unsigned int nulls;
    int result;

    assert(argc == 4);
    expected_path = argv[1];
    faults = (unsigned int)strtoul(argv[2], NULL, 10);
    nulls = (unsigned int)strtoul(argv[3], NULL, 10);
    assert(faults <= 7u && nulls <= 3u);
    observed_output = &output[1];
    result = pc98qr_file_size(nulls & 1u ? NULL : expected_path,
                              nulls & 2u ? NULL : &output[1]);
    assert(file_fd == -1);
    assert(output[0] == 0x13572468UL && output[2] == 0x24681357UL);
    if (result != PC98QR_FILE_SIZE_OK)
        assert(output[1] == SENTINEL);
    printf("{\"result\":%d,\"size\":%lu,\"open\":%u,\"seek\":%u,\"close\":%u}\n",
           result, output[1], open_calls, seek_calls, close_calls);
    return 0;
}
