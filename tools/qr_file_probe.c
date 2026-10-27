/*
 * QFILEは実ファイルのフレームを手動選択する診断で、QRTEST/QFRAMEから独立している。
 */
/* QFILE: manually selected real-file frames; independent of QRTEST/QFRAME. */
#include <string.h>
#include <unistd.h>
#include "file_size.h"
#include "frame_count.h"
#include "frame_offset.h"
#include "file_chunk.h"
#include "pc98_graphics.h"
#include "pc98_status_font.h"
#include "manual_selection.h"
#include "pc98_key_input.h"
#include "frame_header.h"
#include "qr_encode.h"

/*
 * QFILE専用の終了コード。
 * 開いたハンドルの所有とcloseは各ヘルパーが担当する。
 */
/* QFILE-only exit codes. Helpers own and close every opened file handle. */
enum qfile_exit {
    QFILE_OK = 0,
    QFILE_USAGE = 1,
    QFILE_SIZE_ERROR = 2,
    QFILE_COUNT_ERROR = 3,
    QFILE_INIT_ERROR = 4,
    QFILE_SIZE_CHANGED = 5,
    QFILE_OFFSET_ERROR = 6,
    QFILE_READ_ERROR = 7,
    QFILE_HEADER_ERROR = 8,
    QFILE_ENCODER_ERROR = 9,
    QFILE_STATUS_ERROR = 10,
    QFILE_KEY_ERROR = 11,
    QFILE_INPUT_ERROR = 12
};

#define CURRENT_WIDTH 11u
static unsigned char frame[QR_V40L_BYTE_MAX];
static unsigned char matrix[QR_V40_PACKED_BYTES];

static int prepare_frame(const char *path, unsigned long file_size,
                         unsigned short count, unsigned short index)
{
    unsigned long offset, remaining;
    unsigned int actual_length, expected_length;
    if (pc98qr_frame_offset_for_index(index, count, &offset) != PC98QR_FRAME_OFFSET_OK ||
        offset >= file_size)
        return QFILE_OFFSET_ERROR;
    remaining = file_size - offset;
    if (remaining > PC98QR_FRAME_PAYLOAD_MAX)
        remaining = PC98QR_FRAME_PAYLOAD_MAX;
    expected_length = (unsigned int)remaining; /* Narrow only after bounding. */ /* 範囲を制限してから型を縮小する。 */
    if (pc98qr_read_file_chunk(path, offset, frame + PC98QR_FRAME_HEADER_SIZE,
                              (unsigned int)PC98QR_FRAME_PAYLOAD_MAX, &actual_length) !=
        PC98QR_FILE_CHUNK_OK || actual_length != expected_length)
        return QFILE_READ_ERROR;
    if (pc98qr_frame_header_serialize(index, count, frame) != PC98QR_FRAME_HEADER_OK)
        return QFILE_HEADER_ERROR;
    /*
     * 埋め草は加えず、前の長いフレームの残りバイトを含めない。
     */
    /* No file padding: bytes left over from an earlier long frame are excluded. */
    if (qr_encode_v40l_bytes(frame, PC98QR_FRAME_HEADER_SIZE + actual_length, matrix) != QR_OK)
        return QFILE_ENCODER_ERROR;
    return QFILE_OK;
}

/*
 * QRTEST同様に低水準stderr出力を使う。
 * 出力失敗でも診断終了コードを保ち、グラフィック開始前か終了後にだけ呼ぶ。
 */
/* Low-level stderr, as in QRTEST. Preserve the diagnostic exit code even if
 * output fails. Only call before graphics_init or after graphics_shutdown.
 */
static int fail(int code)
{
    const char *message;
    size_t remaining;
    switch (code) {
    case QFILE_USAGE: message = "Usage: QFILE PATH\r\n"; break;
    case QFILE_SIZE_ERROR: message = "Error: could not get file size.\r\n"; break;
    case QFILE_COUNT_ERROR: message = "Error: unsupported file size.\r\n"; break;
    case QFILE_INIT_ERROR: message = "Error: could not initialize selection.\r\n"; break;
    case QFILE_SIZE_CHANGED: message = "Error: file size changed.\r\n"; break;
    case QFILE_OFFSET_ERROR: message = "Error: invalid frame offset.\r\n"; break;
    case QFILE_READ_ERROR: message = "Error: could not read frame.\r\n"; break;
    case QFILE_HEADER_ERROR: message = "Error: could not build header.\r\n"; break;
    case QFILE_ENCODER_ERROR: message = "Error: could not encode QR.\r\n"; break;
    case QFILE_STATUS_ERROR: message = "Error: could not draw status.\r\n"; break;
    case QFILE_KEY_ERROR: message = "Error: BIOS keyboard poll failed.\r\n"; break;
    default: message = "Error: selection input failed.\r\n"; break;
    }
    remaining = strlen(message);
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
 * QINPUTと同じビット走査と8画素のQuiet Zoneを使う。
 * 毎回描画前に消去する。
 */
/* Same packed scan and 8-pixel quiet zone as QINPUT. Clear before each pass. */
static void draw_qr(void)
{
    unsigned int bit;
    unsigned int x = 143;
    unsigned int y = 23;
    for (bit = 0; bit < QR_V40_SIZE * QR_V40_SIZE; ++bit) {
        if (matrix[bit >> 3] & (0x80u >> (bit & 7u)))
            box_fill(x, y, 2, 2);
        x += 2;
        if (x == 497) {
            x = 143;
            y += 2;
        }
    }
}

/*
 * 値は1～65535で、終端や32ビット除算は不要。
 * 追加ごとに11バイトの境界を検査する。
 */
/* No terminator and no 32-bit division: all values are 1..65535.
 * Each append checks the explicit 11-byte destination boundary.
 */
static int append_decimal(unsigned short value, char text[CURRENT_WIDTH],
                          unsigned int *position)
{
    unsigned int place = 10000u;
    while (place > value && place > 1u)
        place /= 10u;
    do {
        if (*position >= CURRENT_WIDTH)
            return 0;
        text[(*position)++] = (char)('0' + value / place);
        value = (unsigned short)(value % place);
        place /= 10u;
    } while (place != 0);
    return 1;
}

static int current_text(unsigned short index, unsigned short count,
                        char text[CURRENT_WIDTH])
{
    unsigned int i, position = 0;
    if (count == 0 || index >= count)
        return 0;
    for (i = 0; i < CURRENT_WIDTH; ++i)
        text[i] = ' ';
    if (!append_decimal((unsigned short)(index + 1u), text, &position) ||
        position >= CURRENT_WIDTH)
        return 0;
    text[position++] = '/';
    return append_decimal(count, text, &position);
}

/*
 * 実行中の再描画ではページ0が見えるため不可分ではない。
 * 状態表示の失敗で打ち切り、先行描画は戻さない。
 */
/* Not atomic: page 0 is visible during runtime redraw. Each failed status
 * call stops this sequence; earlier calls are not rolled back.
 */
static int redraw(const char current[CURRENT_WIDTH], const char selection[5])
{
    int result;
    screen_clear();
    draw_qr();
    result = pc98qr_status_text(520, 32, "CURRENT", 7);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 48, current, CURRENT_WIDTH);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 80, "SELECT", 6);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 96, selection, 5);
    return result;
}

int main(int argc, char **argv)
{
    struct pc98qr_manual_selection state;
    char selection[PC98QR_MANUAL_DIGITS_MAX] = {'-', ' ', ' ', ' ', ' '};
    unsigned short current_index = 0, frame_count;
    unsigned long initial_file_size;
    char current[CURRENT_WIDTH];
    unsigned int i;
    int exit_code;
    if (argc != 2 || argv[1][0] == '\0')
        return fail(QFILE_USAGE);
    if (pc98qr_file_size(argv[1], &initial_file_size) != PC98QR_FILE_SIZE_OK)
        return fail(QFILE_SIZE_ERROR);
    if (pc98qr_frame_count_for_size(initial_file_size, &frame_count) != PC98QR_FRAME_COUNT_OK)
        return fail(QFILE_COUNT_ERROR);
    if (pc98qr_manual_selection_init(&state, frame_count) != PC98QR_MANUAL_UNCHANGED)
        return fail(QFILE_INIT_ERROR);
    exit_code = prepare_frame(argv[1], initial_file_size, frame_count, current_index);
    if (exit_code != QFILE_OK)
        return fail(exit_code);
    if (!current_text(current_index, frame_count, current))
        return fail(QFILE_STATUS_ERROR);

    graphics_init();
    if (redraw(current, selection) != PC98QR_STATUS_OK) {
        exit_code = QFILE_STATUS_ERROR;
        goto shutdown;
    }
    graphics_show();
    for (;;) {
        unsigned short ax;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY) {
            exit_code = QFILE_KEY_ERROR;
            break;
        }
        result = pc98qr_manual_selection_input(&state, pc98qr_key_classify(ax));
        if (result == PC98QR_MANUAL_EXIT) {
            exit_code = QFILE_OK;
            break;
        }
        if (result == PC98QR_MANUAL_EDITED) {
            for (i = 0; i < sizeof(selection); ++i)
                selection[i] = i < state.length ? state.digits[i] : ' ';
            if (state.length == 0)
                selection[0] = '-';
            if (pc98qr_status_text(520, 96, selection, sizeof(selection)) != PC98QR_STATUS_OK) {
                exit_code = QFILE_STATUS_ERROR;
                break;
            }
        } else if (result == PC98QR_MANUAL_SELECTED) {
            unsigned short candidate = (unsigned short)(state.selected_number - 1u);
            unsigned long current_file_size;
            /*
             * サイズとchunkは別々にopen/closeする。
             * サイズ変化を検出するが、同一性や不可分なスナップショットは保証しない。
             */
            /* B method: size and chunk each open/close independently. This
             * detects size changes, not identity or an atomic file snapshot.
             */
            if (pc98qr_file_size(argv[1], &current_file_size) != PC98QR_FILE_SIZE_OK) {
                exit_code = QFILE_SIZE_ERROR;
                break;
            }
            if (current_file_size != initial_file_size) {
                exit_code = QFILE_SIZE_CHANGED;
                break;
            }
            /*
             * 準備中は旧画面を保つがバッファは変更され得る。
             * 失敗時の復元には使わない。
             */
            /* Keep the old screen throughout preparation. The caller buffers
             * may change; neither is used for rollback after a failure.
             */
            exit_code = prepare_frame(argv[1], initial_file_size, frame_count, candidate);
            if (exit_code != QFILE_OK)
                break;
            if (!current_text(candidate, frame_count, current)) {
                exit_code = QFILE_STATUS_ERROR;
                break;
            }
            /*
             * SELECTEDで入力欄が空になるため、表示は元の表記を保持する。
             * 同じ番号の再選択でも全処理を実行する。
             */
            /* SELECTED has emptied digits. Retain selection's original spelling.
             * Reselecting the same number deliberately takes this entire path.
             */
            if (redraw(current, selection) != PC98QR_STATUS_OK) {
                exit_code = QFILE_STATUS_ERROR;
                break;
            }
            current_index = candidate;
        } else if (result != PC98QR_MANUAL_UNCHANGED &&
                   result != PC98QR_MANUAL_REJECTED) {
            exit_code = QFILE_INPUT_ERROR;
            break;
        }
    }

shutdown:
    graphics_shutdown();
    return exit_code == QFILE_OK ? QFILE_OK : fail(exit_code);
}
