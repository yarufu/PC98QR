/*
 * 手動転送で表示するフレーム番号の入力状態を管理する。BIOS 入力や描画は行わず、
 * 数字・Enter・Backspace・Esc の論理イベントを受け取り、状態と処理結果を返す。
 * 数字は最大 5 桁まで保持し、Enter で総フレーム数の範囲内か検証して確定する。
 * 確定番号は 1 始まりで、呼出し側が 1 を引いて転送用の 0 始まり index に変換する。
 * 6 桁目は保存せず桁数超過状態にする。この状態は追加の数字では解除されず、
 * Backspace で最後に保存した 1 桁を削除すると解除される。
 * 不正な確定操作では入力と前回の確定番号を保持し、正常確定時は入力欄を空にする。
 * Esc 後は終了状態を保持し、それ以降の入力では状態を変更しない。
 */

#include "manual_selection.h"

#include <limits.h>
#include <stddef.h>

#if CHAR_BIT != 8 || USHRT_MAX != 65535 || UINT_MAX < 65535U
#error "Manual selection requires 8-bit bytes and 16-bit unsigned short"
#endif

int pc98qr_manual_selection_init(struct pc98qr_manual_selection *state,
                                 unsigned short frame_count)
{
    unsigned int i;
    if (state == NULL || frame_count == 0)
        return PC98QR_MANUAL_ERROR_ARGUMENT;
    for (i = 0; i <= PC98QR_MANUAL_DIGITS_MAX; ++i)
        state->digits[i] = '\0';
    state->length = 0;
    state->frame_count = frame_count;
    state->selected_number = 0;
    state->error = PC98QR_MANUAL_ERROR_NONE;
    state->exited = 0;
    return PC98QR_MANUAL_UNCHANGED;
}

int pc98qr_manual_selection_input(struct pc98qr_manual_selection *state,
                                  unsigned int key)
{
    unsigned int value;
    unsigned int i;

    if (state == NULL)
        return PC98QR_MANUAL_ERROR_ARGUMENT;
    if (state->exited)
        return PC98QR_MANUAL_UNCHANGED;
    if (key == PC98QR_MANUAL_KEY_ESCAPE) {
        state->exited = 1;
        return PC98QR_MANUAL_EXIT;
    }
    if (key == PC98QR_MANUAL_KEY_BACKSPACE) {
        if (state->length == 0)
            return PC98QR_MANUAL_UNCHANGED;
        state->digits[--state->length] = '\0';
        state->error = PC98QR_MANUAL_ERROR_NONE;
        return PC98QR_MANUAL_EDITED;
    }
    if (key >= '0' && key <= '9') {
        if (state->error == PC98QR_MANUAL_ERROR_TOO_LONG)
            return PC98QR_MANUAL_UNCHANGED;
        if (state->length == PC98QR_MANUAL_DIGITS_MAX) {
            state->error = PC98QR_MANUAL_ERROR_TOO_LONG;
            return PC98QR_MANUAL_REJECTED;
        }
        state->digits[state->length++] = (char)key;
        state->digits[state->length] = '\0';
        state->error = PC98QR_MANUAL_ERROR_NONE;
        return PC98QR_MANUAL_EDITED;
    }
    if (key != PC98QR_MANUAL_KEY_ENTER || state->length == 0)
        return PC98QR_MANUAL_UNCHANGED;
    if (state->error == PC98QR_MANUAL_ERROR_TOO_LONG)
        return PC98QR_MANUAL_REJECTED;

    value = 0;
    for (i = 0; i < state->length; ++i) {
        unsigned int digit = (unsigned int)(state->digits[i] - '0');
        /*
         * 16ビットia16でも桁あふれを防ぐため、乗算前に検証する。
         */
        /* Check BEFORE multiplying, including on the 16-bit ia16 ABI. */
        if (value > 6553U || (value == 6553U && digit > 5U)) {
            state->error = PC98QR_MANUAL_ERROR_OVERFLOW;
            return PC98QR_MANUAL_REJECTED;
        }
        value = value * 10U + digit;
    }
    if (value == 0 || value > state->frame_count) {
        state->error = PC98QR_MANUAL_ERROR_RANGE;
        return PC98QR_MANUAL_REJECTED;
    }
    state->selected_number = (unsigned short)value;
    state->length = 0;
    state->digits[0] = '\0';
    state->error = PC98QR_MANUAL_ERROR_NONE;
    return PC98QR_MANUAL_SELECTED;
}
