/*
 * QRTEST の入口。コマンドライン引数に応じて入力方法と表示処理を選択する。
 * 文字列・/B HEX・/F PATH は単一 QR、/T は version 01 の転送フレームを表示する。
 * /M PATH は version 02 の手動転送を manual_transfer に委譲し、戻り値を
 * DOS の終了コードへ変換する。手動転送では呼出し先が画面の終了処理まで担当する。
 * QR の種類は入力容量に応じて選び、大きな出力領域は DOS スタックを避けて
 * 静的領域に置く。引数や入力の検証を終えてからグラフィック画面へ移行する。
 */

#include "pc98_graphics.h"
#include "qr_encode.h"
#include "frame_header.h"
#include "file_size.h"
#include "frame_count.h"
#include "frame_offset.h"
#include "file_chunk.h"
#include "manual_transfer.h"

#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#define QR_MODULE_SIZE 12
#define QR_QUIET_ZONE 4
#define QR_MAX_TOTAL_SIZE ((QR_V2_SIZE + 2 * QR_QUIET_ZONE) * QR_MODULE_SIZE)
#define QR_V25_MODULE_SIZE 3
#define QR_V25_TOTAL_SIZE ((QR_V25_SIZE + 2 * QR_QUIET_ZONE) * QR_V25_MODULE_SIZE)
#define QR_V40_MODULE_SIZE 2
#define QR_V40_TOTAL_SIZE ((QR_V40_SIZE + 2 * QR_QUIET_ZONE) * QR_V40_MODULE_SIZE)
#define TRANSPORT_PAYLOAD_MAX (QR_V40L_BYTE_MAX - PC98QR_FRAME_HEADER_SIZE)

#if QR_MAX_TOTAL_SIZE > SCREEN_WIDTH || QR_MAX_TOTAL_SIZE > SCREEN_HEIGHT
#error QR must fit on the screen, including its quiet zone
#endif

#if QR_V25_TOTAL_SIZE > SCREEN_WIDTH || QR_V25_TOTAL_SIZE > SCREEN_HEIGHT
#error V25 QR must fit on the screen, including its quiet zone
#endif

#if QR_V40_TOTAL_SIZE > SCREEN_WIDTH || QR_V40_TOTAL_SIZE > SCREEN_HEIGHT
#error V40 QR must fit on the screen, including its quiet zone
#endif

/*
 * 内部の保存・描画形式を選択する。
 * V1/V2のセル配列とV25/V40のビット列を混在させない。
 */
/* Private storage/render selection; V1/V2 cells and V25/V40 bits never mix. */
enum qr_profile { PROFILE_V1, PROFILE_V2, PROFILE_V25, PROFILE_V40 };

/*
 * 有効な出力は同時に一つだけ。
 * 最大領域をDOSスタックに置かない。
 */
/* Only one output is active. Keep the largest storage off the DOS stack. */
static union {
    unsigned char v1[QR_V1_SIZE][QR_V1_SIZE];
    unsigned char v2[QR_V2_SIZE][QR_V2_SIZE];
    unsigned char v25[QR_V25_PACKED_BYTES];
    unsigned char v40[QR_V40_PACKED_BYTES];
} matrix;

static int encode_binary(const unsigned char *data, unsigned int length,
                         enum qr_profile *profile)
{
    if (length <= QR_V1Q_BYTE_MAX) {
        *profile = PROFILE_V1;
        return qr_encode_v1q_bytes(data, length, matrix.v1);
    }
    if (length <= QR_V2M_BYTE_MAX) {
        *profile = PROFILE_V2;
        if (length <= QR_V2Q_BYTE_MAX)
            return qr_encode_v2q_bytes(data, length, matrix.v2);
        return qr_encode_v2m_bytes(data, length, matrix.v2);
    }
    if (length <= QR_V25L_BYTE_MAX) {
        *profile = PROFILE_V25;
        return qr_encode_v25l_bytes(data, length, matrix.v25);
    }
    *profile = PROFILE_V40;
    return qr_encode_v40l_bytes(data, length, matrix.v40);
}

static void print_error(const char *message)
{
    /*
     * stderrへ直接writeしてstdioのバッファとheap依存を避ける。
     * DOSコンソール用にCR/LFを明示する。
     */
    /* Raw stderr write avoids stdio buffers and their heap dependencies.
     * Explicit CR/LF is needed for the DOS text console.
     */
    size_t remaining = strlen(message);
    while (remaining != 0) {
        ssize_t written = write(STDERR_FILENO, message, remaining);
        if (written <= 0)
            return;
        message += written;
        remaining -= (size_t)written;
    }
}

static void draw_byte_qr(const unsigned char *cells, unsigned int size)
{
    unsigned int x;
    unsigned int y;
    unsigned int total = (size + 2u * QR_QUIET_ZONE) * QR_MODULE_SIZE;
    unsigned int body_x = (SCREEN_WIDTH - total) / 2u + QR_QUIET_ZONE * QR_MODULE_SIZE;
    unsigned int body_y = (SCREEN_HEIGHT - total) / 2u + QR_QUIET_ZONE * QR_MODULE_SIZE;

    for (y = 0; y < size; ++y) {
        for (x = 0; x < size; ++x) {
            if (cells[y * size + x]) {
                box_fill(body_x + x * QR_MODULE_SIZE,
                         body_y + y * QR_MODULE_SIZE,
                         QR_MODULE_SIZE, QR_MODULE_SIZE);
            }
        }
    }
}

static void draw_v25_qr(void)
{
    unsigned int x;
    unsigned int y;
    unsigned int body_x = (SCREEN_WIDTH - QR_V25_TOTAL_SIZE) / 2u
                         + QR_QUIET_ZONE * QR_V25_MODULE_SIZE;
    unsigned int body_y = (SCREEN_HEIGHT - QR_V25_TOTAL_SIZE) / 2u
                         + QR_QUIET_ZONE * QR_V25_MODULE_SIZE;

    for (y = 0; y < QR_V25_SIZE; ++y) {
        for (x = 0; x < QR_V25_SIZE; ++x) {
            unsigned int index = y * QR_V25_SIZE + x;
            if (matrix.v25[index >> 3] & (0x80u >> (index & 7u))) {
                box_fill(body_x + x * QR_V25_MODULE_SIZE,
                         body_y + y * QR_V25_MODULE_SIZE,
                         QR_V25_MODULE_SIZE, QR_V25_MODULE_SIZE);
            }
        }
    }
}

static void draw_v40_qr(void)
{
    unsigned int x;
    unsigned int y;
    unsigned int body_x = (SCREEN_WIDTH - QR_V40_TOTAL_SIZE) / 2u
                         + QR_QUIET_ZONE * QR_V40_MODULE_SIZE;
    unsigned int body_y = (SCREEN_HEIGHT - QR_V40_TOTAL_SIZE) / 2u
                         + QR_QUIET_ZONE * QR_V40_MODULE_SIZE;

    for (y = 0; y < QR_V40_SIZE; ++y) {
        for (x = 0; x < QR_V40_SIZE; ++x) {
            unsigned int index = y * QR_V40_SIZE + x;
            if (matrix.v40[index >> 3] & (0x80u >> (index & 7u))) {
                box_fill(body_x + x * QR_V40_MODULE_SIZE,
                         body_y + y * QR_V40_MODULE_SIZE,
                         QR_V40_MODULE_SIZE, QR_V40_MODULE_SIZE);
            }
        }
    }
}

static void draw_qr(enum qr_profile profile)
{
    switch (profile) {
    case PROFILE_V1:
        draw_byte_qr(&matrix.v1[0][0], QR_V1_SIZE);
        break;
    case PROFILE_V2:
        draw_byte_qr(&matrix.v2[0][0], QR_V2_SIZE);
        break;
    case PROFILE_V25:
        draw_v25_qr();
        break;
    case PROFILE_V40:
        draw_v40_qr();
        break;
    }
}

static int hex_value(unsigned char character)
{
    if (character >= '0' && character <= '9')
        return character - '0';
    if (character >= 'A' && character <= 'F')
        return character - 'A' + 10;
    if (character >= 'a' && character <= 'f')
        return character - 'a' + 10;
    return -1;
}

static int parse_binary_hex(const char *hex, unsigned char *data,
                            unsigned int *length)
{
    unsigned int digits;
    unsigned int i;

    /*
     * ASCII入力を上限付きで走査する。
     * バイナリ出力には終端文字を付けない。
     */
    /* Bounded scan of ASCII input only; binary output has no terminator. */
    for (digits = 0; digits <= 2u * QR_V2M_BYTE_MAX; ++digits) {
        if (hex[digits] == '\0')
            break;
    }
    if (digits == 0 || digits > 2u * QR_V2M_BYTE_MAX || digits % 2u != 0)
        return QR_ERROR_LENGTH;
    for (i = 0; i < digits; i += 2) {
        int high = hex_value((unsigned char)hex[i]);
        int low = hex_value((unsigned char)hex[i + 1]);
        if (high < 0 || low < 0)
            return QR_ERROR_CHARACTER;
        data[i / 2u] = (unsigned char)((unsigned int)high * 16u + (unsigned int)low);
    }
    *length = digits / 2u;
    return QR_OK;
}

/*
 * ASCII十進数字だけを受け付け、16ビットunsigned intでも乗算前に範囲を検査する。
 */
/* ASCII decimal only; check before multiplying even with 16-bit unsigned int. */
static int parse_frame_index(const char *text, unsigned short *index)
{
    unsigned int value = 0;
    if (*text == '\0')
        return 0;
    while (*text != '\0') {
        unsigned int digit;
        if (*text < '0' || *text > '9')
            return 0;
        digit = (unsigned int)(*text - '0');
        if (value > 6553u || (value == 6553u && digit > 4u))
            return 0;
        value = value * 10u + digit;
        ++text;
    }
    *index = (unsigned short)value;
    return 1;
}

enum file_read_result {
    FILE_READ_OK = 0,
    FILE_READ_OPEN_ERROR,
    FILE_READ_EMPTY,
    FILE_READ_TOO_LARGE,
    FILE_READ_IO_ERROR
};

/*
 * 呼出し側は最大長+1バイトを用意する。
 * 余分に1バイト読んで長すぎるファイルを切り捨てず拒否する。
 * ia16/newlibやPOSIXの低水準読み取りはO_BINARYがなくてもバイナリを保持し、正の短いreadをEOF扱いしない。
 * 長さの更新は成功時だけ。
 */
/* Caller supplies max_length + 1 bytes; length is set only on success.
 * Read one extra byte to reject larger files without truncating their payload.
 * This runtime's low-level disk reads are binary, including when O_BINARY is
 * absent (ia16/newlib and POSIX). Never treat a positive short read as EOF.
 */
static enum file_read_result read_binary_file(
    const char *path, unsigned char *buffer, unsigned int max_length,
    unsigned int *length)
{
    unsigned int total = 0;
    enum file_read_result result = FILE_READ_OK;
    int flags = O_RDONLY;
    int fd;

#ifdef O_BINARY
    flags |= O_BINARY;
#endif
    fd = open(path, flags);
    if (fd < 0)
        return FILE_READ_OPEN_ERROR;

    while (total < max_length + 1u) {
        ssize_t count = read(fd, buffer + total, max_length + 1u - total);
        if (count < 0) {
            result = FILE_READ_IO_ERROR;
            break;
        }
        if (count == 0)
            break;
        total += (unsigned int)count;
    }
    if (result == FILE_READ_OK) {
        if (total == 0)
            result = FILE_READ_EMPTY;
        else if (total > max_length)
            result = FILE_READ_TOO_LARGE;
    }
    /*
     * 先行エラーを優先するが、closeに失敗した場合もQR生成へ進まない。
     */
    /* Preserve an earlier failure, but do not encode after a close failure. */
    if (close(fd) < 0 && result == FILE_READ_OK)
        result = FILE_READ_IO_ERROR;
    if (result == FILE_READ_OK)
        *length = total;
    return result;
}

int main(int argc, char **argv)
{
    static unsigned char binary[QR_V2M_BYTE_MAX];
    static unsigned char file_buffer[QR_V40L_BYTE_MAX + 1];
    unsigned int length;
    enum qr_profile profile = PROFILE_V1;
    int result;

    if (argc == 3 && strcmp(argv[1], "/M") == 0) {
        enum pc98qr_transfer_result transfer_result = pc98qr_manual_transfer_v2(
            argv[2], file_buffer, sizeof(file_buffer),
            matrix.v40, sizeof(matrix.v40));
        /*
         * 画面とキー入力の終了処理は制御側が担当する。
         * 従来の描画・待機経路へ進まず、内部enumをそのままDOS終了コードにしない。
         */
        /* The controller owns graphics and keyboard cleanup. Do not enter
         * the legacy render/wait path, and never expose its enum as DOS status.
         */
        if (transfer_result == PC98QR_TRANSFER_FINISHED ||
            transfer_result == PC98QR_TRANSFER_CANCELLED)
            return 0;
        print_error("Error: manual transfer failed.\r\n");
        return 1;
    } else if (argc == 3 && strcmp(argv[1], "/B") == 0) {
        result = parse_binary_hex(argv[2], binary, &length);
        if (result != QR_OK) {
            if (result == QR_ERROR_LENGTH)
                print_error("Error: binary hex must contain 2-52 characters with even length.\r\n");
            else
                print_error("Error: binary hex must use 0-9, A-F, or a-f only.\r\n");
            return 1;
        }
        result = encode_binary(binary, length, &profile);
    } else if (argc == 3 && (strcmp(argv[1], "/F") == 0 ||
                            strcmp(argv[1], "/T") == 0)) {
        enum file_read_result file_result;
        int transport = strcmp(argv[1], "/T") == 0;
        if (argv[2][0] == '\0') {
            print_error("Error: file path must not be empty.\r\n");
            return 1;
        }
        /*
         * /Tは先頭9バイトをヘッダー用に確保する。
         * 位置2953は超過検出用で、両モードは2954バイトの領域を共有する。
         */
        /* /T reserves bytes 0..8 for the header; index 2953 remains the
         * extra-byte probe. Both modes share this one 2954-byte buffer.
         */
        file_result = read_binary_file(
            argv[2], file_buffer + (transport ? PC98QR_FRAME_HEADER_SIZE : 0),
            transport ? TRANSPORT_PAYLOAD_MAX : QR_V40L_BYTE_MAX, &length);
        if (file_result != FILE_READ_OK) {
            if (file_result == FILE_READ_OPEN_ERROR)
                print_error("Error: could not open file.\r\n");
            else if (file_result == FILE_READ_EMPTY)
                print_error("Error: file is empty.\r\n");
            else if (file_result == FILE_READ_TOO_LARGE)
                print_error(transport
                    ? "Error: transport file must contain 1-2944 bytes.\r\n"
                    : "Error: file must contain 1-2953 bytes.\r\n");
            else
                print_error("Error: file I/O failed.\r\n");
            return 1;
        }
        if (transport) {
            if (pc98qr_frame_header_serialize(0, 1, file_buffer) !=
                PC98QR_FRAME_HEADER_OK) {
                print_error("Error: transport header generation failed.\r\n");
                return 1;
            }
            /*
             * ペイロードが1バイトでも転送QRの形状はV40-Lに固定する。
             */
            /* Transport geometry stays V40-L even for a one-byte payload. */
            profile = PROFILE_V40;
            result = qr_encode_v40l_bytes(
                file_buffer, PC98QR_FRAME_HEADER_SIZE + length, matrix.v40);
        } else {
            result = encode_binary(file_buffer, length, &profile);
        }
    } else if (argc == 4 && strcmp(argv[1], "/T") == 0) {
        unsigned short frame_index;
        unsigned short frame_count;
        unsigned long file_size;
        unsigned long offset;
        unsigned long remaining;
        unsigned int expected_length;

        if (!parse_frame_index(argv[2], &frame_index)) {
            print_error("Error: transport frame index must be 0-65534.\r\n");
            return 1;
        }
        if (argv[3][0] == '\0') {
            print_error("Error: file path must not be empty.\r\n");
            return 1;
        }
        result = pc98qr_file_size(argv[3], &file_size);
        if (result != PC98QR_FILE_SIZE_OK) {
            print_error(result == PC98QR_FILE_SIZE_ERROR_OPEN
                ? "Error: could not open file.\r\n"
                : "Error: file I/O failed.\r\n");
            return 1;
        }
        if (pc98qr_frame_count_for_size(file_size, &frame_count) !=
            PC98QR_FRAME_COUNT_OK) {
            print_error("Error: transport file size is not supported.\r\n");
            return 1;
        }
        if (pc98qr_frame_offset_for_index(frame_index, frame_count, &offset) !=
            PC98QR_FRAME_OFFSET_OK) {
            print_error("Error: transport frame index is out of range.\r\n");
            return 1;
        }
        /*
         * 検証済みの総数とindexからoffsetはファイルサイズ未満となる。
         * ia16では減算を広い型で行い、長さを制限してから型を縮小する。
         */
        /* The validated count/index imply offset < file_size. Keep the
         * subtraction wide and bound the length before narrowing on ia16.
         */
        remaining = file_size - offset;
        if (remaining > PC98QR_FRAME_PAYLOAD_MAX)
            remaining = PC98QR_FRAME_PAYLOAD_MAX;
        if (remaining == 0) {
            print_error("Error: file I/O failed.\r\n");
            return 1;
        }
        expected_length = (unsigned int)remaining;
        /*
         * 既存バッファのヘッダー直後へ直接読み込む。
         * ファイルが変化しても、最終以外の短いフレームを送らない。
         */
        /* Reuse the legacy buffer, reading directly after the header slot.
         * A changed file must not produce a short nonfinal transport frame.
         */
        result = pc98qr_read_file_chunk(
            argv[3], offset, file_buffer + PC98QR_FRAME_HEADER_SIZE,
            (unsigned int)PC98QR_FRAME_PAYLOAD_MAX, &length);
        if (result != PC98QR_FILE_CHUNK_OK || length != expected_length) {
            print_error(result == PC98QR_FILE_CHUNK_ERROR_OPEN
                ? "Error: could not open file.\r\n"
                : "Error: file I/O failed.\r\n");
            return 1;
        }
        if (pc98qr_frame_header_serialize(frame_index, frame_count, file_buffer) !=
            PC98QR_FRAME_HEADER_OK) {
            print_error("Error: transport header generation failed.\r\n");
            return 1;
        }
        profile = PROFILE_V40;
        result = qr_encode_v40l_bytes(
            file_buffer, PC98QR_FRAME_HEADER_SIZE + length, matrix.v40);
    } else if (argc == 2) {
        result = qr_encode_v1q_alnum(argv[1], matrix.v1);
        if (result != QR_OK) {
            if (result == QR_ERROR_LENGTH)
                print_error("Error: text must contain 1-16 characters.\r\n");
            else
                print_error("Error: use 0-9, A-Z, space, $%*+-./: only.\r\n");
            return 1;
        }
    } else {
        print_error("Usage: QRTEST \"TEXT\" (1-16 QR Alphanumeric characters)\r\n"
                    "       QRTEST /B HEX (1-26 bytes, 2-52 hex digits)\r\n"
                    "       QRTEST /F PATH (whole binary file, 1-2953 bytes)\r\n"
                    "       QRTEST /T PATH (transport payload, 1-2944 bytes)\r\n"
                    "       QRTEST /T INDEX PATH (selected transport frame, index 0-65534)\r\n");
        return 1;
    }
    if (result != QR_OK) {
        print_error("Error: binary encoding failed.\r\n");
        return 1;
    }

    graphics_init();
    screen_clear();
    draw_qr(profile);
    graphics_show();
    wait_key();
    graphics_shutdown();
    return 0;
}
