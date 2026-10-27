/*
 * 公開APIのホスト専用ドライバー。
 * 実際の選択モジュールは別途コンパイルする。
 */
/* Host-only public API driver. Real selection module is compiled separately. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/manual_selection.h"

#define PC98QR_KEY_INPUT_HOST_TEST
#include "../src/pc98_key_input.c"

static unsigned char mock_bh;
static uint16_t mock_peek, mock_read;
static unsigned int peeks, reads;

static uint16_t bios_peek_key(unsigned char *bh)
{
    ++peeks;
    *bh = mock_bh;
    return mock_peek;
}

static uint16_t bios_read_key(void)
{
    assert(mock_bh == 1);
    ++reads;
    return mock_read;
}

static unsigned int number(const char *text, unsigned long limit)
{
    char *end;
    unsigned long value;
    errno = 0;
    value = strtoul(text, &end, 10);
    assert(*text >= '0' && *text <= '9' && *end == '\0');
    assert(errno == 0 && value <= limit);
    return (unsigned int)value;
}

static void report(const struct pc98qr_manual_selection *s, int result)
{
    assert(s->length <= 5 && s->digits[s->length] == '\0');
    assert(strlen(s->digits) == s->length);
    assert(s->selected_number <= s->frame_count);
    printf("{\"result\":%d,\"digits\":\"%s\",\"length\":%u,\"count\":%u,"
           "\"selected\":%u,\"index\":%ld,\"error\":%u,\"exited\":%u}",
           result, s->digits, (unsigned int)s->length, (unsigned int)s->frame_count,
           (unsigned int)s->selected_number, (long)s->selected_number - 1L,
           (unsigned int)s->error, (unsigned int)s->exited);
}

int main(int argc, char **argv)
{
    struct {
        unsigned short before[4];
        struct pc98qr_manual_selection state;
        unsigned short after[4];
    } guarded;
    unsigned int i;
    int result;

    assert(argc >= 2);
    if (strcmp(argv[1], "classify") == 0) {
        printf("[");
        for (i = 2; i < (unsigned int)argc; ++i)
            printf("%s%u", i == 2 ? "" : ",",
                   pc98qr_key_classify((unsigned short)number(argv[i], 65535)));
        puts("]");
        return 0;
    }
    if (strcmp(argv[1], "poll") == 0) {
        unsigned short output = 0xA55AU;
        assert(argc == 5 || argc == 6);
        mock_bh = (unsigned char)number(argv[2], 255);
        mock_peek = (uint16_t)number(argv[3], 65535);
        mock_read = (uint16_t)number(argv[4], 65535);
        result = pc98qr_key_poll(argc == 6 ? NULL : &output);
        printf("{\"result\":%d,\"ax\":%u,\"peeks\":%u,\"reads\":%u}\n",
               result, (unsigned int)output, peeks, reads);
        return 0;
    }
    memset(&guarded, 0xA5, sizeof(guarded));
    if (strcmp(argv[1], "arguments") == 0) {
        unsigned char original[sizeof(guarded)];
        memcpy(original, &guarded, sizeof(guarded));
        assert(pc98qr_manual_selection_init(NULL, 24) == PC98QR_MANUAL_ERROR_ARGUMENT);
        assert(pc98qr_manual_selection_init(&guarded.state, 0) == PC98QR_MANUAL_ERROR_ARGUMENT);
        assert(memcmp(original, &guarded, sizeof(guarded)) == 0);
        assert(pc98qr_manual_selection_input(NULL, '1') == PC98QR_MANUAL_ERROR_ARGUMENT);
        puts("{\"arguments_checked\":true}");
        return 0;
    }
    assert((strcmp(argv[1], "state") == 0 || strcmp(argv[1], "reinit") == 0) && argc >= 3);
    result = pc98qr_manual_selection_init(&guarded.state,
                                         (unsigned short)number(argv[2], 65535));
    assert(result == PC98QR_MANUAL_UNCHANGED);
    printf("[");
    report(&guarded.state, result);
    for (i = 3; i < (unsigned int)argc; ++i) {
        result = pc98qr_manual_selection_input(&guarded.state, number(argv[i], UINT_MAX));
        printf(",");
        report(&guarded.state, result);
    }
    if (strcmp(argv[1], "reinit") == 0) {
        result = pc98qr_manual_selection_init(&guarded.state, 3);
        printf(",");
        report(&guarded.state, result);
    }
    puts("]");
    for (i = 0; i < 4; ++i)
        assert(guarded.before[i] == 0xA5A5U && guarded.after[i] == 0xA5A5U);
    return 0;
}
