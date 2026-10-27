/*
 * 実MSTART・キーアダプター・サイズ/総数ヘルパーを使うホスト試験。
 * BIOS・write・明示的なヘルパー故障だけを差し替える。
 */
/* Host harness: real MSTART main, key adapter, file-size and count helpers.
 * Only BIOS, write and explicit helper-failure injections are substituted.
 */
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "file_size.h"
#include "frame_count.h"

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"

static const char *events;
static uint16_t queued_ax;
static int queued;
static unsigned long polls, reads, file_calls, count_calls;
static long write_budget = -1;
static size_t write_limit;
static int write_zero, write_failed;

static uint16_t bios_peek_key(unsigned char *bh)
{
    char *end;
    unsigned long ax;
    assert(!queued);
    assert(file_calls == 1 && count_calls == 1);
    ++polls;
    while (*events == ' ')
        ++events;
    assert(*events != '\0'); /* Unexpected polling must fail, not hang. */ /* 予期しないポーリングはハングではなく失敗にする。 */
    if (*events == '-' || *events == '!') {
        *bh = *events++ == '-' ? 0 : 2;
        return 0x1E53; /* Stale S must never start on EMPTY or error. */ /* 未入力やエラー時に残っている古いSで開始してはならない。 */
    }
    errno = 0;
    ax = strtoul(events, &end, 16);
    assert(errno == 0 && end != events && ax <= 65535UL);
    assert(*end == '\0' || *end == ' ');
    events = end;
    queued_ax = (uint16_t)ax;
    queued = 1;
    *bh = 1;
    return 0x1E53; /* Deliberately differs from the consumed key. */ /* 実際に消費したキーと意図的に異なる値を使う。 */
}

static uint16_t bios_read_key(void)
{
    assert(queued);
    queued = 0;
    ++reads;
    return queued_ax;
}

static int observed_file_size(const char *path, unsigned long *size)
{
    const char *setting = getenv("PC98QR_MSTART_FILE_ERROR");
    assert(polls == 0 && count_calls == 0);
    ++file_calls;
    if (setting != NULL)
        return (int)strtol(setting, NULL, 10);
    setting = getenv("PC98QR_MSTART_SIZE");
    if (setting != NULL) {
        *size = strtoul(setting, NULL, 10);
        return PC98QR_FILE_SIZE_OK;
    }
    return pc98qr_file_size(path, size);
}

static int observed_frame_count(unsigned long size, unsigned short *count)
{
    const char *setting = getenv("PC98QR_MSTART_COUNT_ERROR");
    assert(polls == 0 && file_calls == 1);
    ++count_calls;
    if (setting != NULL)
        return (int)strtol(setting, NULL, 10);
    return pc98qr_frame_count_for_size(size, count);
}

static ssize_t observed_write(int fd, const void *bytes, size_t length)
{
    ssize_t n;
    assert(!write_failed); /* Failure terminates output immediately. */ /* 出力失敗時はただちに出力を打ち切る。 */
    assert(fd == STDOUT_FILENO || fd == STDERR_FILENO);
    if (write_budget == 0) {
        write_failed = 1;
        errno = EIO;
        return write_zero ? 0 : -1;
    }
    if (write_limit && length > write_limit)
        length = write_limit;
    if (write_budget > 0 && length > (size_t)write_budget)
        length = (size_t)write_budget;
    n = write(fd, bytes, length);
    if (n > 0 && write_budget > 0)
        write_budget -= n;
    return n;
}

#define pc98qr_file_size observed_file_size
#define pc98qr_frame_count_for_size observed_frame_count
#define write observed_write
#define main mstart_main
#include "../tools/manual_start_probe.c"
#undef main
#undef write
#undef pc98qr_frame_count_for_size
#undef pc98qr_file_size

static void check_calls(const char *name, unsigned long actual)
{
    const char *expected = getenv(name);
    if (expected != NULL)
        assert(actual == strtoul(expected, NULL, 10));
}

int main(int argc, char **argv)
{
    const char *setting;
    int result;
    events = getenv("PC98QR_MSTART_KEYS");
    if (events == NULL)
        events = "001b";
    setting = getenv("PC98QR_MSTART_WRITE_BUDGET");
    if (setting != NULL)
        write_budget = strtol(setting, NULL, 10);
    setting = getenv("PC98QR_MSTART_WRITE_LIMIT");
    if (setting != NULL)
        write_limit = (size_t)strtoul(setting, NULL, 10);
    write_zero = getenv("PC98QR_MSTART_WRITE_ZERO") != NULL;

    setting = getenv("PC98QR_MSTART_DECIMAL");
    if (setting != NULL) {
        /*
         * ホストABIのULONG_MAXでも本番整形関数を試す。
         */
        /* Exercise the production formatter at the host ABI's ULONG_MAX too. */
        unsigned long value = strcmp(setting, "max") == 0
                              ? ULONG_MAX : strtoul(setting, NULL, 10);
        result = emit_decimal(value) ? 0 : MSTART_OUTPUT_ERROR;
    } else {
        result = mstart_main(argc, argv);
    }
    check_calls("PC98QR_MSTART_POLLS", polls);
    check_calls("PC98QR_MSTART_READS", reads);
    check_calls("PC98QR_MSTART_FILE_CALLS", file_calls);
    check_calls("PC98QR_MSTART_COUNT_CALLS", count_calls);
    assert(!queued);
    return result;
}
