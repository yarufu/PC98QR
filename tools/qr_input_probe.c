/*
 * QINPUTは固定QR上で実際の数字入力を試す診断。
 * QRTESTへリンクしない。
 */
/* QINPUT: real number input over one fixed QR; never linked into QRTEST. */
#include "pc98_graphics.h"
#include "pc98_status_font.h"
#include "manual_selection.h"
#include "pc98_key_input.h"
#include "frame_header.h"
#include "qr_encode.h"

/*
 * この診断専用の終了コードであり、既存probeは独自の契約を保つ。
 */
/* Diagnostic-only exit codes; existing probes retain their own contracts. */
enum qinput_exit {
    QINPUT_OK = 0,
    QINPUT_USAGE = 1,
    QINPUT_INIT_ERROR = 2,
    QINPUT_HEADER_ERROR = 3,
    QINPUT_ENCODER_ERROR = 4,
    QINPUT_INITIAL_STATUS_ERROR = 5,
    QINPUT_UPDATE_ERROR = 6,
    QINPUT_KEY_ERROR = 7,
    QINPUT_INPUT_ERROR = 8
};

static unsigned char fixed_frame[QR_V40L_BYTE_MAX];
static unsigned char fixed_matrix[QR_V40_PACKED_BYTES];

/*
 * QUPDと同じ走査と形状を使い、本番描画関数は内部のままにする。
 */
/* Same packed scan and geometry as QUPD. Keep production rendering private. */
static void draw_fixed_qr(void)
{
    unsigned int bit;
    unsigned int x = 143;
    unsigned int y = 23;
    for (bit = 0; bit < QR_V40_SIZE * QR_V40_SIZE; ++bit) {
        if (fixed_matrix[bit >> 3] & (0x80u >> (bit & 7u)))
            box_fill(x, y, 2, 2);
        x += 2;
        if (x == 497) {
            x = 143;
            y += 2;
        }
    }
}

int main(int argc, char **argv)
{
    struct pc98qr_manual_selection state;
    char display[PC98QR_MANUAL_DIGITS_MAX] = {'-', ' ', ' ', ' ', ' '};
    unsigned int i;
    int exit_code;
    (void)argv;

    if (argc != 1)
        return QINPUT_USAGE;
    if (pc98qr_manual_selection_init(&state, 24) != PC98QR_MANUAL_UNCHANGED)
        return QINPUT_INIT_ERROR;
    if (pc98qr_frame_header_serialize(0, 24, fixed_frame) != PC98QR_FRAME_HEADER_OK)
        return QINPUT_HEADER_ERROR;
    for (i = 0; i < QR_V40L_BYTE_MAX - PC98QR_FRAME_HEADER_SIZE; ++i) {
        /*
         * 下位バイトは同等で、すべての中間値がia16のunsigned intに収まる。
         */
        /* Equivalent low byte; every intermediate fits ia16 unsigned int. */
        fixed_frame[PC98QR_FRAME_HEADER_SIZE + i] =
            (unsigned char)(((i & 255u) * 37u + (i >> 8) * 11u + 3u) & 255u);
    }
    if (qr_encode_v40l_bytes(fixed_frame, sizeof(fixed_frame), fixed_matrix) != QR_OK)
        return QINPUT_ENCODER_ERROR;

    graphics_init();
    screen_clear();
    draw_fixed_qr();
    if (pc98qr_status_fixed() != PC98QR_STATUS_OK) {
        exit_code = QINPUT_INITIAL_STATUS_ERROR;
        goto shutdown;
    }
    graphics_show();
    for (;;) {
        unsigned short ax;
        int result = pc98qr_key_poll(&ax);
        if (result == PC98QR_KEY_EMPTY)
            continue;
        if (result != PC98QR_KEY_READY) {
            exit_code = QINPUT_KEY_ERROR;
            break;
        }
        result = pc98qr_manual_selection_input(&state, pc98qr_key_classify(ax));
        if (result == PC98QR_MANUAL_EXIT) {
            exit_code = QINPUT_OK;
            break;
        }
        if (result == PC98QR_MANUAL_EDITED) {
            /*
             * NULなしの正確な5バイトを渡す。
             * 空白で旧文字を消す。
             * 状態は参照可能だが、変更するのはinit/inputだけ。
             */
            /* Exactly five readable bytes, no NUL. Spaces erase old strokes.
             * State is inspectable, but only init/input may change its fields.
             */
            for (i = 0; i < sizeof(display); ++i)
                display[i] = i < state.length ? state.digits[i] : ' ';
            if (state.length == 0)
                display[0] = '-';
            if (pc98qr_status_text(520, 96, display, sizeof(display)) != PC98QR_STATUS_OK) {
                exit_code = QINPUT_UPDATE_ERROR;
                break;
            }
        } else if (result != PC98QR_MANUAL_UNCHANGED &&
                   result != PC98QR_MANUAL_REJECTED &&
                   result != PC98QR_MANUAL_SELECTED) {
            exit_code = QINPUT_INPUT_ERROR;
            break;
        }
        /*
         * SELECTEDで入力欄が消えても直前の表示表記を保持する。
         * REJECTEDも表示を保ち、エラーラベル・入力破棄・チャタリング除去は行わない。
         */
        /* SELECTED clears state.digits; retain the previous display spelling.
         * REJECTED also keeps the display. No error label, flush or debounce.
         */
    }

shutdown:
    graphics_shutdown();
    return exit_code;
}
