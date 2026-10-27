/*
 * 手動ファイル転送の制御。情報表示 → S で開始 → QR 表示と番号選択 → Esc で終了
 * という流れを管理する。画面の番号は 1 始まり、転送フレームの index は 0 始まり。
 * 正式 QRTEST は version 02、比較・回帰診断用の入口は version 01 を使い、
 * キー操作と画面の初期化・終了処理は共通化している。
 * version 02 では matrix を生データの一時領域として使い、frame へ直列化した後、
 * QR 符号化で matrix を上書きする。準備に失敗した領域は再描画せず終了する。
 * 開始時と有効な番号選択時にサイズを再確認するが、同サイズの内容変更は検出しない。
 */

/*
 * 情報表示からSで実ファイルの手動転送を始める。
 * mainや大きな保存領域は持たず、既存MSTART/QFILEの手法を利用する。
 */
/* Information -> S -> real-file manual QR transfer. No main or large storage.
 * Uses this project's MSTART/QFILE algorithms; those probes remain unchanged.
 */
#include "manual_transfer.h"
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
#include "frame_v2.h"

#if PC98QR_FRAME_V2_MAX != QR_V40L_BYTE_MAX || PC98QR_FRAME_V2_PAYLOAD_MAX > QR_V40_PACKED_BYTES
#error "V2 frame and scratch capacities must fit the existing caller buffers"
#endif

#define CURRENT_WIDTH 11u

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
     * ULONG_MAXでも桁あふれしない方法で最上位の位を求める。
     * 数値用バッファや16ビットへの縮小を使わず1文字ずつ出力する。
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

/*
 * 先に情報を表示し、Sが押されるまでは選択状態の初期化やデータ読み取りをしない。
 */
/* Display first; do not initialize selection or read payload until S. */
static int startup(const char *path, unsigned long file_size,
                   unsigned short frame_count)
{
    if (!emit_text(STDOUT_FILENO, "PC98QR - Manual Transfer\r\n\r\nFile       : ") ||
        !emit_text(STDOUT_FILENO, path) ||
        !emit_text(STDOUT_FILENO, "\r\nFile size  : ") ||
        !emit_decimal(file_size) ||
        !emit_text(STDOUT_FILENO, " bytes\r\nQR frames  : ") ||
        !emit_decimal((unsigned long)frame_count) ||
        !emit_text(STDOUT_FILENO, "\r\nFrame range: 1 - ") ||
        !emit_decimal((unsigned long)frame_count) ||
        !emit_text(STDOUT_FILENO, "\r\n\r\nS   : Start\r\nEsc : Cancel\r\n"))
        return PC98QR_TRANSFER_ERROR_OUTPUT;

    for (;;) {
        unsigned short ax;
        unsigned int character;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY)
            return PC98QR_TRANSFER_ERROR_KEY;
        character = ax & 0xFFU;
        /*
         * Sはここで消費する。
         * 先読み・入力破棄・キー解放待ちは行わない。
         */
        /* Consume S here; do not peek ahead, flush, or wait for key release. */
        if (character == 'S' || character == 's')
            return PC98QR_TRANSFER_FINISHED; /* Internal: permission to start. */ /* 内部処理では開始許可を表す。 */
        if (pc98qr_key_classify(ax) == PC98QR_MANUAL_KEY_ESCAPE)
            return emit_text(STDOUT_FILENO, "\r\nCancelled.\r\n")
                   ? PC98QR_TRANSFER_CANCELLED : PC98QR_TRANSFER_ERROR_OUTPUT;
        /*
         * Enter・数字・Backspace・その他のキーは消費して無視する。
         */
        /* Enter, digits, Backspace and unrelated events are consumed/ignored. */
    }
}

/*
 * 符号化成功までmatrixは一時領域であり、frameとは別領域。
 * 直列化はポインタを保持せず、符号化は以前の出力を読まず全体を上書きする。
 * 失敗した領域を再描画しない。
 */
/* matrix is scratch until encoding succeeds. It is distinct from frame, and
 * the serializer retains no input pointers. The encoder replaces every matrix
 * byte without reading the old output. Never redraw either buffer on failure.
 */
static int prepare_frame_v2(const char *path, unsigned long file_size,
                            unsigned short count, unsigned short index,
                            const unsigned char *filename, unsigned int filename_length,
                            unsigned char *frame, unsigned char *matrix)
{
    struct pc98qr_frame_v2_layout layout;
    unsigned int actual_length, complete_length;
    if (pc98qr_frame_v2_layout_for_index(file_size, filename_length, index, &layout) !=
        PC98QR_FRAME_V2_OK || layout.frame_count != count)
        return PC98QR_TRANSFER_ERROR_OFFSET;
    if (pc98qr_read_file_chunk(path, layout.file_offset, matrix,
                              layout.file_capacity, &actual_length) != PC98QR_FILE_CHUNK_OK ||
        actual_length != layout.file_length)
        return PC98QR_TRANSFER_ERROR_READ;
    if (pc98qr_frame_v2_serialize(filename, filename_length, file_size, index,
                                matrix, actual_length, frame, QR_V40L_BYTE_MAX,
                                &complete_length) != PC98QR_FRAME_V2_OK ||
        complete_length != layout.frame_length)
        return PC98QR_TRANSFER_ERROR_HEADER;
    if (qr_encode_v40l_bytes(frame, complete_length, matrix) != QR_OK)
        return PC98QR_TRANSFER_ERROR_ENCODER;
    return PC98QR_TRANSFER_FINISHED;
}

static int prepare_frame(const char *path, unsigned long file_size,
                         unsigned short count, unsigned short index,
                         unsigned char *frame, unsigned char *matrix,
                         const unsigned char *filename, unsigned int filename_length)
{
    unsigned long offset, remaining;
    unsigned int actual_length, expected_length;
    if (filename != NULL)
        return prepare_frame_v2(path, file_size, count, index, filename,
                                filename_length, frame, matrix);
    if (pc98qr_frame_offset_for_index(index, count, &offset) != PC98QR_FRAME_OFFSET_OK ||
        offset >= file_size)
        return PC98QR_TRANSFER_ERROR_OFFSET;
    remaining = file_size - offset;
    if (remaining > PC98QR_FRAME_PAYLOAD_MAX)
        remaining = PC98QR_FRAME_PAYLOAD_MAX;
    expected_length = (unsigned int)remaining; /* Narrow only after bounding. */ /* 範囲を制限した後で型を縮小する。 */
    if (pc98qr_read_file_chunk(path, offset, frame + PC98QR_FRAME_HEADER_SIZE,
                              (unsigned int)PC98QR_FRAME_PAYLOAD_MAX, &actual_length) !=
        PC98QR_FILE_CHUNK_OK || actual_length != expected_length)
        return PC98QR_TRANSFER_ERROR_READ;
    if (pc98qr_frame_header_serialize(index, count, frame) != PC98QR_FRAME_HEADER_OK)
        return PC98QR_TRANSFER_ERROR_HEADER;
    /*
     * 元ファイルへ埋め草を加えず、前の長いフレームの残りバイトを含めない。
     */
    /* No file padding: bytes left over from an earlier long frame are excluded. */
    if (qr_encode_v40l_bytes(frame, PC98QR_FRAME_HEADER_SIZE + actual_length, matrix) != QR_OK)
        return PC98QR_TRANSFER_ERROR_ENCODER;
    return PC98QR_TRANSFER_FINISHED;
}

/*
 * QINPUTと同じビット列走査と8画素の余白を使用する。
 * 各描画前に画面を消去する。
 */
/* Same packed scan and 8-pixel quiet zone as QINPUT. Clear before each pass. */
static void draw_qr(const unsigned char *matrix)
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
 * 値は1～65535で、終端文字や32ビット除算は不要。
 * 追加ごとに11バイトの出力境界を確認する。
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
 * 再描画中もページ0が見えるため、更新は不可分ではない。
 * 状態表示の失敗で中断し、既に描いた部分は元へ戻さない。
 */
/* Not atomic: page 0 is visible during runtime redraw. Each failed status
 * call stops this sequence; earlier calls are not rolled back.
 */
static int redraw(const unsigned char *matrix,
                  const char current[CURRENT_WIDTH], const char selection[5])
{
    int result;
    screen_clear();
    draw_qr(matrix);
    result = pc98qr_status_text(520, 32, "CURRENT", 7);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 48, current, CURRENT_WIDTH);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 80, "SELECT", 6);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 96, selection, 5);
    return result;
}

/*
 * 両入口で画面操作と寿命管理を共有する。
 * v1は暫定の診断互換用で、不正なv2パスをv1へ切り替えない。
 */
/* One UI/lifecycle for both explicit entry points; v1 is temporary diagnostic
 * compatibility. Invalid v2 paths never fall back to the v1 branch.
 */
static enum pc98qr_transfer_result manual_transfer(
    const char *path,
    unsigned char *frame, unsigned int frame_capacity,
    unsigned char *matrix, unsigned int matrix_capacity, int version2)
{
    struct pc98qr_manual_selection state;
    char selection[PC98QR_MANUAL_DIGITS_MAX] = {'-', ' ', ' ', ' ', ' '};
    unsigned short current_index = 0, frame_count;
    unsigned long initial_file_size;
    char current[CURRENT_WIDTH];
    unsigned int i;
    unsigned char filename[PC98QR_FILENAME_V2_MAX];
    unsigned int filename_length = 0;
    int exit_code;
    if (path == NULL || path[0] == '\0' || frame == NULL || matrix == NULL ||
        frame_capacity < QR_V40L_BYTE_MAX || matrix_capacity < QR_V40_PACKED_BYTES)
        return PC98QR_TRANSFER_ERROR_ARGUMENT;
    if (version2 && pc98qr_filename_v2_from_path(path, filename, sizeof(filename),
                                               &filename_length) != PC98QR_FILENAME_V2_OK)
        return PC98QR_TRANSFER_ERROR_ARGUMENT;
    if (pc98qr_file_size(path, &initial_file_size) != PC98QR_FILE_SIZE_OK)
        return PC98QR_TRANSFER_ERROR_SIZE;
    if (version2 ? pc98qr_frame_v2_count_for_size(initial_file_size, filename_length,
                                                &frame_count) != PC98QR_FRAME_V2_OK :
                   pc98qr_frame_count_for_size(initial_file_size, &frame_count) != PC98QR_FRAME_COUNT_OK)
        return PC98QR_TRANSFER_ERROR_COUNT;
    exit_code = startup(path, initial_file_size, frame_count);
    if (exit_code != PC98QR_TRANSFER_FINISHED)
        return (enum pc98qr_transfer_result)exit_code;
    {
        unsigned long current_file_size;
        if (pc98qr_file_size(path, &current_file_size) != PC98QR_FILE_SIZE_OK)
            return PC98QR_TRANSFER_ERROR_SIZE;
        if (current_file_size != initial_file_size)
            return PC98QR_TRANSFER_ERROR_SIZE_CHANGED;
    }
    if (pc98qr_manual_selection_init(&state, frame_count) != PC98QR_MANUAL_UNCHANGED)
        return PC98QR_TRANSFER_ERROR_INIT;
    exit_code = prepare_frame(path, initial_file_size, frame_count, current_index, frame, matrix,
                             version2 ? filename : NULL, filename_length);
    if (exit_code != PC98QR_TRANSFER_FINISHED)
        return (enum pc98qr_transfer_result)exit_code;
    if (!current_text(current_index, frame_count, current))
        return PC98QR_TRANSFER_ERROR_STATUS;

    graphics_init();
    if (redraw(matrix, current, selection) != PC98QR_STATUS_OK) {
        exit_code = PC98QR_TRANSFER_ERROR_STATUS;
        goto shutdown;
    }
    graphics_show();
    for (;;) {
        unsigned short ax;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY) {
            exit_code = PC98QR_TRANSFER_ERROR_KEY;
            break;
        }
        result = pc98qr_manual_selection_input(&state, pc98qr_key_classify(ax));
        if (result == PC98QR_MANUAL_EXIT) {
            exit_code = PC98QR_TRANSFER_FINISHED;
            break;
        }
        if (result == PC98QR_MANUAL_EDITED) {
            for (i = 0; i < sizeof(selection); ++i)
                selection[i] = i < state.length ? state.digits[i] : ' ';
            if (state.length == 0)
                selection[0] = '-';
            if (pc98qr_status_text(520, 96, selection, sizeof(selection)) != PC98QR_STATUS_OK) {
                exit_code = PC98QR_TRANSFER_ERROR_STATUS;
                break;
            }
        } else if (result == PC98QR_MANUAL_SELECTED) {
            unsigned short candidate = (unsigned short)(state.selected_number - 1u);
            unsigned long current_file_size;
            /*
             * サイズ取得とchunk読み取りは別々にopen/closeする。
             * サイズ変化は検出するが、同一ファイルや不可分なスナップショットを保証しない。
             */
            /* B method: size and chunk each open/close independently. This
             * detects size changes, not identity or an atomic file snapshot.
             */
            if (pc98qr_file_size(path, &current_file_size) != PC98QR_FILE_SIZE_OK) {
                exit_code = PC98QR_TRANSFER_ERROR_SIZE;
                break;
            }
            if (current_file_size != initial_file_size) {
                exit_code = PC98QR_TRANSFER_ERROR_SIZE_CHANGED;
                break;
            }
            /*
             * 準備中は以前の画面を保つが、バッファは変更され得る。
             * 失敗時の復元用には使わない。
             */
            /* Keep the old screen throughout preparation. The caller buffers
             * may change; neither is used for rollback after a failure.
             */
            exit_code = prepare_frame(path, initial_file_size, frame_count, candidate, frame, matrix,
                                     version2 ? filename : NULL, filename_length);
            if (exit_code != PC98QR_TRANSFER_FINISHED)
                break;
            if (!current_text(candidate, frame_count, current)) {
                exit_code = PC98QR_TRANSFER_ERROR_STATUS;
                break;
            }
            /*
             * SELECTED後は入力欄が空なので、表示用には選択時の表記を保持する。
             * 同じ番号でも意図的に再読み取り・再描画を行う。
             */
            /* SELECTED has emptied digits. Retain selection's original spelling.
             * Reselecting the same number deliberately takes this entire path.
             */
            if (redraw(matrix, current, selection) != PC98QR_STATUS_OK) {
                exit_code = PC98QR_TRANSFER_ERROR_STATUS;
                break;
            }
            current_index = candidate;
        } else if (result != PC98QR_MANUAL_UNCHANGED &&
                   result != PC98QR_MANUAL_REJECTED) {
            exit_code = PC98QR_TRANSFER_ERROR_INPUT;
            break;
        }
    }

shutdown:
    graphics_shutdown();
    return (enum pc98qr_transfer_result)exit_code;
}

enum pc98qr_transfer_result pc98qr_manual_transfer(
    const char *path,
    unsigned char *frame, unsigned int frame_capacity,
    unsigned char *matrix, unsigned int matrix_capacity)
{
    return manual_transfer(path, frame, frame_capacity, matrix, matrix_capacity, 0);
}

enum pc98qr_transfer_result pc98qr_manual_transfer_v2(
    const char *path,
    unsigned char *frame, unsigned int frame_capacity,
    unsigned char *matrix, unsigned int matrix_capacity)
{
    return manual_transfer(path, frame, frame_capacity, matrix, matrix_capacity, 1);
}
