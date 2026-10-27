/*
 * 実NUMPROBEと実アダプターを使い、BIOSとwriteだけを差し替えるホスト試験。
 */
/* Host-only real NUMPROBE main + real adapter, replacing BIOS and write only. */
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"

static const char *script;
static char *pending_end;
static uint16_t pending_ax;
static unsigned int read_count;
static int have_key;
static long remaining_write = -1;
static size_t write_limit;
static int zero_write;

static uint16_t bios_peek_key(unsigned char *bh)
{
    unsigned long value;
    assert(!have_key);
    while (*script == ' ')
        ++script;
    /*
     * -はAXに古いEnterを残した未入力、!はBH=2を与える。
     */
    /* '-' supplies an empty poll with a stale Enter in AX; '!' supplies BH=2. */
    if (*script == '-' || *script == '!') {
        *bh = *script++ == '-' ? 0 : 2;
        return 0x1C0D;
    }
    assert(*script != '\0'); /* Exhaustion catches unexpected extra polling. */ /* イベントを使い切った後の呼出しで、予期しない追加ポーリングを検出する。 */
    errno = 0;
    value = strtoul(script, &pending_end, 16);
    assert(errno == 0 && pending_end != script && value <= 65535);
    assert(*pending_end == '\0' || *pending_end == ' ');
    pending_ax = (uint16_t)value;
    have_key = 1;
    *bh = 1;
    return pending_ax;
}

static uint16_t bios_read_key(void)
{
    assert(have_key);
    have_key = 0;
    script = pending_end;
    ++read_count;
    return pending_ax;
}

static ssize_t observed_write(int fd, const void *buffer, size_t length)
{
    ssize_t result;
    if (remaining_write == 0) {
        errno = EIO;
        return zero_write ? 0 : -1;
    }
    if (write_limit != 0 && length > write_limit)
        length = write_limit;
    if (remaining_write > 0 && length > (size_t)remaining_write)
        length = (size_t)remaining_write;
    result = write(fd, buffer, length);
    if (result > 0 && remaining_write > 0)
        remaining_write -= result;
    return result;
}

#define write observed_write
#define main numprobe_main
#include "../tools/manual_selection_probe.c"
#undef main
#undef write

int main(int argc, char **argv)
{
    const char *setting;
    int result;
    script = getenv("PC98QR_NUMPROBE_KEYS");
    if (script == NULL)
        script = "001b";
    setting = getenv("PC98QR_NUMPROBE_WRITE_LIMIT");
    if (setting != NULL)
        write_limit = (size_t)strtoul(setting, NULL, 10);
    setting = getenv("PC98QR_NUMPROBE_WRITE_BYTES");
    if (setting != NULL)
        remaining_write = strtol(setting, NULL, 10);
    zero_write = getenv("PC98QR_NUMPROBE_ZERO_WRITE") != NULL;
    result = numprobe_main(argc, argv);
    setting = getenv("PC98QR_NUMPROBE_READS");
    if (setting != NULL)
        assert(read_count == strtoul(setting, NULL, 10));
    return result;
}
