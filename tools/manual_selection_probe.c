/*
 * NUMPROBEはDOSテキスト用の単独診断で、QRTESTへはリンクしない。
 */
/* NUMPROBE: standalone DOS text diagnostic. Never linked into QRTEST. */
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include "manual_selection.h"
#include "pc98_key_input.h"

static int write_all(int fd, const char *text, size_t length)
{
    while (length != 0) {
        ssize_t written = write(fd, text, length);
        if (written <= 0)
            return 0;
        text += written;
        length -= (size_t)written;
    }
    return 1;
}

static int put_text(const char *text)
{
    return write_all(STDOUT_FILENO, text, strlen(text));
}

static int put_number(unsigned short number)
{
    char digits[PC98QR_MANUAL_DIGITS_MAX];
    char *end = digits + sizeof(digits);
    char *first = end;
    do {
        *--first = (char)('0' + number % 10U);
        number /= 10U;
    } while (number != 0);
    return write_all(STDOUT_FILENO, first, (size_t)(end - first));
}

static int show_input(const struct pc98qr_manual_selection *state)
{
    /*
     * CRと空白で消した数字を上書きし、ANSIやグラフィックに依存しない。
     * 編集・新エラー・選択成功時だけ再描画する。
     */
    /* CR + spaces erase deleted digits without ANSI or graphics dependencies.
     * Redraw only after an edit, a new error, or a successful selection.
     */
    return put_text("\rInput: ") &&
           write_all(STDOUT_FILENO, state->digits, state->length) &&
           write_all(STDOUT_FILENO, "     ",
                     PC98QR_MANUAL_DIGITS_MAX - state->length);
}

static int show_error(unsigned char error)
{
    if (error == PC98QR_MANUAL_ERROR_TOO_LONG)
        return put_text("\r\nError: too many digits; Backspace deletes last saved digit.\r\n");
    if (error == PC98QR_MANUAL_ERROR_OVERFLOW)
        return put_text("\r\nError: number exceeds 65535.\r\n");
    return put_text("\r\nError: out of range.\r\n");
}

static int parse_count(const char *text, unsigned short *count)
{
    struct pc98qr_manual_selection state;
    unsigned int i;
    pc98qr_manual_selection_init(&state, 65535U);
    /*
     * 無関係な対話キーは無視する一方、CLI構文は厳格に検証する。
     * 同じ実状態機械を使って長さ・範囲・桁あふれ規則をそろえる。
     */
    /* Strict CLI syntax, unlike interactive unrelated keys which are ignored.
     * Use the actual state machine for the same length/range/overflow rules.
     */
    for (i = 0; text[i] != '\0'; ++i) {
        if (i == PC98QR_MANUAL_DIGITS_MAX || text[i] < '0' || text[i] > '9')
            return 0;
        pc98qr_manual_selection_input(&state, (unsigned int)text[i]);
    }
    if (pc98qr_manual_selection_input(&state, PC98QR_MANUAL_KEY_ENTER) !=
        PC98QR_MANUAL_SELECTED)
        return 0;
    *count = state.selected_number;
    return 1;
}

int main(int argc, char **argv)
{
    struct pc98qr_manual_selection state;
    unsigned short count;

    if (argc != 2 || !parse_count(argv[1], &count)) {
        const char *usage = "Usage: NUMPROBE COUNT (1-65535, 1-5 decimal digits)\r\n";
        return write_all(STDERR_FILENO, usage, strlen(usage)) ? 2 : 1;
    }
    pc98qr_manual_selection_init(&state, count);
    if (!put_text("PC98QR - Number Input Test\r\n\r\nFrame range: 1 - ") ||
        !put_number(count) ||
        !put_text("\r\nDigits: 0-9  Enter: select  Backspace: edit  Esc: exit\r\n\r\n") ||
        !show_input(&state))
        return 1;

    for (;;) {
        unsigned short ax;
        unsigned char previous_error = state.error;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY) {
            const char *message = "\r\nError: unexpected BIOS keyboard status.\r\n";
            write_all(STDERR_FILENO, message, strlen(message));
            return 1;
        }
        result = pc98qr_manual_selection_input(&state, pc98qr_key_classify(ax));
        if (result == PC98QR_MANUAL_EXIT)
            return put_text("\r\nDone.\r\n") ? 0 : 1;
        if (result == PC98QR_MANUAL_SELECTED) {
            if (!put_text("\r\nSelected: ") || !put_number(state.selected_number) ||
                !put_text("\r\nInternal frame index: ") ||
                !put_number((unsigned short)(state.selected_number - 1U)) ||
                !put_text("\r\n") || !show_input(&state))
                return 1;
        } else if (result == PC98QR_MANUAL_EDITED) {
            if (!show_input(&state))
                return 1;
        } else if (result == PC98QR_MANUAL_REJECTED && state.error != previous_error) {
            if (!show_error(state.error) || !show_input(&state))
                return 1;
        }
    }
}
