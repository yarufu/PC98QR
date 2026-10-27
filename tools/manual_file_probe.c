/*
 * QMANは再利用可能な手動転送制御部を試す独立診断プログラム。
 */
/* QMAN: independent diagnostic for the reusable manual-transfer controller. */
#include <string.h>
#include <unistd.h>
#include "manual_transfer.h"

static unsigned char frame[QR_V40L_BYTE_MAX];
static unsigned char matrix[QR_V40_PACKED_BYTES];

/*
 * 制御部は既にテキストへ復帰済みか、グラフィックに入っていない。
 * 診断出力の失敗で元のエラーを置き換えない。
 */
/* The controller has already restored text mode, or never entered graphics.
 * Failure of this diagnostic output must not replace the original error.
 */
static int fail(int code, const char *message)
{
    size_t remaining = strlen(message);
    while (remaining != 0) {
        ssize_t written = write(STDERR_FILENO, message, remaining);
        if (written <= 0)
            break;
        message += written;
        remaining -= (size_t)written;
    }
    return code;
}

/*
 * QMAN独自の終了コード変換は共有APIの外に置く。
 * 記載の将来案は当時の説明で、QRTEST側では正常結果を0、エラーを1にまとめる別の変換が可能。
 */
/* QMAN's mapping is deliberately outside the shared API. A future QRTEST
 * caller can map both normal results to 0 and every error to 1 instead.
 */
static int finish(enum pc98qr_transfer_result result)
{
    switch (result) {
    case PC98QR_TRANSFER_FINISHED:
    case PC98QR_TRANSFER_CANCELLED:
        return 0;
    case PC98QR_TRANSFER_ERROR_ARGUMENT:
        return fail(1, "Error: invalid manual transfer arguments.\r\n");
    case PC98QR_TRANSFER_ERROR_SIZE:
        return fail(2, "Error: could not get file size.\r\n");
    case PC98QR_TRANSFER_ERROR_COUNT:
        return fail(3, "Error: unsupported file size.\r\n");
    case PC98QR_TRANSFER_ERROR_INIT:
        return fail(4, "Error: could not initialize selection.\r\n");
    case PC98QR_TRANSFER_ERROR_SIZE_CHANGED:
        return fail(5, "Error: file size changed.\r\n");
    case PC98QR_TRANSFER_ERROR_OFFSET:
        return fail(6, "Error: invalid frame offset.\r\n");
    case PC98QR_TRANSFER_ERROR_READ:
        return fail(7, "Error: could not read frame.\r\n");
    case PC98QR_TRANSFER_ERROR_HEADER:
        return fail(8, "Error: could not build header.\r\n");
    case PC98QR_TRANSFER_ERROR_ENCODER:
        return fail(9, "Error: could not encode QR.\r\n");
    case PC98QR_TRANSFER_ERROR_STATUS:
        return fail(10, "Error: could not draw status.\r\n");
    case PC98QR_TRANSFER_ERROR_KEY:
        return fail(11, "Error: BIOS keyboard poll failed.\r\n");
    case PC98QR_TRANSFER_ERROR_INPUT:
        return fail(12, "Error: selection input failed.\r\n");
    case PC98QR_TRANSFER_ERROR_OUTPUT:
        return fail(13, "Error: could not write transfer information.\r\n");
    default:
        return fail(12, "Error: unexpected manual transfer result.\r\n");
    }
}

int main(int argc, char **argv)
{
    if (argc != 2 || argv[1][0] == '\0')
        return fail(1, "Usage: QMAN PATH\r\n");
    return finish(pc98qr_manual_transfer(argv[1], frame, sizeof(frame),
                                         matrix, sizeof(matrix)));
}
