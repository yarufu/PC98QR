/*
 * MSTARTはDOSテキストで開始操作を試す診断で、QRTESTへリンクしない。
 */
/* MSTART: DOS text startup diagnostic, never linked into QRTEST. */
#include <string.h>
#include <unistd.h>
#include "file_size.h"
#include "frame_count.h"
#include "pc98_key_input.h"

/*
 * 当時のprobe専用終了コードであり、QRTEST /Mの契約を定めるものではない。
 */
/* Probe-only exit codes, not a contract for the future QRTEST /M mode. */
enum mstart_exit {
    MSTART_START = 0,
    MSTART_CANCEL = 1,
    MSTART_USAGE = 2,
    MSTART_FILE_ERROR = 3,
    MSTART_COUNT_ERROR = 4,
    MSTART_KEY_ERROR = 5,
    MSTART_OUTPUT_ERROR = 6
};

static int emit_bytes(int fd, const char *bytes, size_t length)
{
    size_t offset = 0;
    while (offset < length) {
        ssize_t n = write(fd, bytes + offset, length - offset);
        if (n <= 0)
            return 0;
        offset += (size_t)n;
    }
    return 1;
}

static int emit_text(int fd, const char *text)
{
    return emit_bytes(fd, text, strlen(text));
}

static int emit_decimal(unsigned long value)
{
    unsigned long place = 1;
    /*
     * ULONG_MAXでも桁あふれしないよう最上位の位を求める。
     * 数値バッファや16ビット縮小を使わず1文字ずつ出力する。
     */
    /* Find the leading place without overflowing even at ULONG_MAX.
     * Emit one character at a time: no number buffer or 16-bit narrowing.
     */
    while (value / place >= 10UL)
        place *= 10UL;
    do {
        char digit = (char)('0' + value / place);
        if (!emit_bytes(STDOUT_FILENO, &digit, 1))
            return 0;
        value %= place;
        place /= 10UL;
    } while (place != 0);
    return 1;
}

static int fail(int code, const char *message)
{
    return emit_text(STDERR_FILENO, message) ? code : MSTART_OUTPUT_ERROR;
}

int main(int argc, char **argv)
{
    unsigned long file_size;
    unsigned short frame_count;
    int result;

    if (argc != 2 || argv[1][0] == '\0')
        return fail(MSTART_USAGE, "Usage: MSTART PATH\r\n");

    result = pc98qr_file_size(argv[1], &file_size);
    if (result != PC98QR_FILE_SIZE_OK)
        return fail(MSTART_FILE_ERROR, result == PC98QR_FILE_SIZE_ERROR_OPEN
                    ? "Error: could not open file.\r\n"
                    : "Error: file size acquisition failed.\r\n");
    if (pc98qr_frame_count_for_size(file_size, &frame_count) !=
        PC98QR_FRAME_COUNT_OK)
        return fail(MSTART_COUNT_ERROR, file_size == 0
                    ? "Error: file is empty.\r\n"
                    : "Error: transport frame count is not supported.\r\n");

    if (!emit_text(STDOUT_FILENO, "PC98QR - Manual Transfer\r\n\r\nFile       : ") ||
        !emit_text(STDOUT_FILENO, argv[1]) ||
        !emit_text(STDOUT_FILENO, "\r\nFile size  : ") ||
        !emit_decimal(file_size) ||
        !emit_text(STDOUT_FILENO, " bytes\r\nQR frames  : ") ||
        !emit_decimal((unsigned long)frame_count) ||
        !emit_text(STDOUT_FILENO, "\r\nFrame range: 1 - ") ||
        !emit_decimal((unsigned long)frame_count) ||
        !emit_text(STDOUT_FILENO, "\r\n\r\nS   : Start\r\nEsc : Cancel\r\n"))
        return MSTART_OUTPUT_ERROR;

    for (;;) {
        unsigned short ax;
        unsigned int character;
        result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY)
            return fail(MSTART_KEY_ERROR,
                        "Error: unexpected BIOS keyboard status.\r\n");
        /*
         * INT 18h AH=00hではALがASCII、AHがキーコード。
         * 消費したAXだけを使い、入力破棄・キー解放確認・チャタリング除去はしない。
         */
        /* INT 18h AH=00h returns key data (ASCII) in AL, key code in AH.
         * Use only the consumed AX. No flush, release check or debounce.
         */
        character = ax & 0xFFU;
        if (character == 'S' || character == 's')
            return emit_text(STDOUT_FILENO,
                       "\r\nStart requested.\r\n"
                       "QR display is not implemented in this probe.\r\n")
                   ? MSTART_START : MSTART_OUTPUT_ERROR;
        if (pc98qr_key_classify(ax) == PC98QR_MANUAL_KEY_ESCAPE)
            return emit_text(STDOUT_FILENO, "\r\nCancelled.\r\n")
                   ? MSTART_CANCEL : MSTART_OUTPUT_ERROR;
        /*
         * Enterの反復、数字、Backspace、その他のキーでは何もしない。
         */
        /* Enter (including repeats), digits, Backspace and others do nothing. */
    }
}
