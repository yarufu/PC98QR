/*
 * QFRAMEは合成フレームを手動選択する診断で、QRTEST/QINPUTから独立している。
 */
/* QFRAME: manually selected synthetic frames; independent of QRTEST/QINPUT. */
#include "pc98_graphics.h"
#include "pc98_status_font.h"
#include "manual_selection.h"
#include "pc98_key_input.h"
#include "frame_header.h"
#include "qr_encode.h"

/*
 * 診断専用の終了コード。
 * 実行中のヘッダーや符号化失敗でも画面終了処理が必要。
 */
/* Diagnostic-only exits. Runtime header/encoder failures also need shutdown. */
enum qframe_exit {
    QFRAME_OK = 0,
    QFRAME_USAGE = 1,
    QFRAME_INIT_ERROR = 2,
    QFRAME_HEADER_ERROR = 3,
    QFRAME_ENCODER_ERROR = 4,
    QFRAME_INITIAL_STATUS_ERROR = 5,
    QFRAME_UPDATE_ERROR = 6,
    QFRAME_KEY_ERROR = 7,
    QFRAME_INPUT_ERROR = 8
};

#define FRAME_COUNT 24u
static unsigned char frame[QR_V40L_BYTE_MAX];
static unsigned char matrix[QR_V40_PACKED_BYTES];

static int prepare_frame(unsigned short index)
{
    unsigned int i;
    if (pc98qr_frame_header_serialize(index, FRAME_COUNT, frame) != PC98QR_FRAME_HEADER_OK)
        return QFRAME_HEADER_ERROR;
    for (i = 0; i < QR_V40L_BYTE_MAX - PC98QR_FRAME_HEADER_SIZE; ++i) {
        /*
         * index 0～23ではia16の全中間値が9582以下。
         * index 0はヘッダー込みでQINPUTの試験データを保持する。
         */
        /* For index 0..23 every intermediate is <=9582 on the ia16 ABI.
         * Index 0 preserves the complete QINPUT fixture, including its header.
         */
        frame[PC98QR_FRAME_HEADER_SIZE + i] =
            (unsigned char)(((i & 255u) * 37u + (i >> 8) * 11u + 3u + index) & 255u);
    }
    if (qr_encode_v40l_bytes(frame, sizeof(frame), matrix) != QR_OK)
        return QFRAME_ENCODER_ERROR;
    return QFRAME_OK;
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
 * indexはヘッダーAPIで検証済み。
 * 正確に5バイトで、NUL終端は付けない。
 */
/* index is already validated by the header API. Exactly five bytes, no NUL. */
static void current_text(unsigned short index, char text[5])
{
    unsigned int number = index + 1u;
    unsigned int position = 0;
    unsigned int i;
    for (i = 0; i < 5u; ++i)
        text[i] = ' ';
    if (number >= 10u)
        text[position++] = (char)('0' + number / 10u);
    text[position++] = (char)('0' + number % 10u);
    text[position++] = '/';
    text[position++] = '2';
    text[position] = '4';
}

/*
 * 再描画中もページ0が見える。
 * 状態表示失敗で打ち切り、先行描画は戻さない。
 */
/* Not atomic: page 0 is visible during runtime redraw. Each failed status
 * call stops this sequence; earlier calls are not rolled back.
 */
static int redraw(unsigned short index, const char selection[5])
{
    char current[5];
    int result;
    current_text(index, current);
    screen_clear();
    draw_qr();
    result = pc98qr_status_text(520, 32, "CURRENT", 7);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 48, current, sizeof(current));
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
    unsigned short current_index = 0;
    unsigned int i;
    int exit_code;
    (void)argv;

    if (argc != 1)
        return QFRAME_USAGE;
    if (pc98qr_manual_selection_init(&state, FRAME_COUNT) != PC98QR_MANUAL_UNCHANGED)
        return QFRAME_INIT_ERROR;
    exit_code = prepare_frame(current_index);
    if (exit_code != QFRAME_OK)
        return exit_code;

    graphics_init();
    if (redraw(current_index, selection) != PC98QR_STATUS_OK) {
        exit_code = QFRAME_INITIAL_STATUS_ERROR;
        goto shutdown;
    }
    graphics_show();
    for (;;) {
        unsigned short ax;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY) {
            exit_code = QFRAME_KEY_ERROR;
            break;
        }
        result = pc98qr_manual_selection_input(&state, pc98qr_key_classify(ax));
        if (result == PC98QR_MANUAL_EXIT) {
            exit_code = QFRAME_OK;
            break;
        }
        if (result == PC98QR_MANUAL_EDITED) {
            for (i = 0; i < sizeof(selection); ++i)
                selection[i] = i < state.length ? state.digits[i] : ' ';
            if (state.length == 0)
                selection[0] = '-';
            if (pc98qr_status_text(520, 96, selection, sizeof(selection)) != PC98QR_STATUS_OK) {
                exit_code = QFRAME_UPDATE_ERROR;
                break;
            }
        } else if (result == PC98QR_MANUAL_SELECTED) {
            unsigned short candidate = (unsigned short)(state.selected_number - 1u);
            /*
             * 準備中は旧画面を保つがバッファは変更され得る。
             * 失敗時の復元には使わない。
             */
            /* Keep the old screen throughout preparation. The caller buffers
             * may change; neither is used for rollback after a failure.
             */
            exit_code = prepare_frame(candidate);
            if (exit_code != QFRAME_OK)
                break;
            /*
             * SELECTEDで入力欄が空になるため表示は元の表記を保持する。
             * 同じ番号でも全経路を再実行する。
             */
            /* SELECTED has emptied digits. Retain selection's original spelling.
             * Reselecting the same number deliberately takes this entire path.
             */
            if (redraw(candidate, selection) != PC98QR_STATUS_OK) {
                exit_code = QFRAME_UPDATE_ERROR;
                break;
            }
            current_index = candidate;
        } else if (result != PC98QR_MANUAL_UNCHANGED &&
                   result != PC98QR_MANUAL_REJECTED) {
            exit_code = QFRAME_INPUT_ERROR;
            break;
        }
    }

shutdown:
    graphics_shutdown();
    return exit_code;
}
