#ifndef PC98QR_MANUAL_SELECTION_H
#define PC98QR_MANUAL_SELECTION_H

#define PC98QR_MANUAL_DIGITS_MAX 5U

/*
 * 数字はASCII値だが、制御イベントはBIOSのAX値ではない。
 */
/* Digits are ASCII '0'..'9'. These control events are NOT BIOS AX values. */
enum pc98qr_manual_key {
    PC98QR_MANUAL_KEY_IGNORE = 0,
    PC98QR_MANUAL_KEY_ENTER = 256,
    PC98QR_MANUAL_KEY_BACKSPACE,
    PC98QR_MANUAL_KEY_ESCAPE
};

enum pc98qr_manual_error {
    PC98QR_MANUAL_ERROR_NONE = 0,
    PC98QR_MANUAL_ERROR_RANGE,
    PC98QR_MANUAL_ERROR_OVERFLOW,
    PC98QR_MANUAL_ERROR_TOO_LONG
};

enum pc98qr_manual_result {
    PC98QR_MANUAL_UNCHANGED = 0,
    PC98QR_MANUAL_EDITED,
    PC98QR_MANUAL_REJECTED,
    PC98QR_MANUAL_SELECTED,
    PC98QR_MANUAL_EXIT,
    PC98QR_MANUAL_ERROR_ARGUMENT
};

/*
 * 状態は呼出し側が所有するが、初期化関数を使い、フィールドを直接変更しない。
 * 確定番号は1始まり、0は未選択。
 * SELECTED時には入力欄が空になり、番号−1が転送indexとなる。
 * I/Oや静的作業領域は持たない。
 */
/* Caller-owned, inspectable state. Initialize through init; do not edit fields.
 * selected_number is 1-origin; 0 means no selection yet. It changes ONLY on
 * SELECTED (or reinitialization). On SELECTED, selected_number - 1U is the
 * 0-origin frame_index, and digits is already empty. error holds a value from
 * pc98qr_manual_error. No heap, static workspace, BIOS, graphics or file I/O.
 */
struct pc98qr_manual_selection {
    char digits[PC98QR_MANUAL_DIGITS_MAX + 1U];
    unsigned short length;
    unsigned short frame_count;
    unsigned short selected_number;
    unsigned char error;
    unsigned char exited;
};

/*
 * 総数は1～65535。
 * 成功はUNCHANGED、不正引数はERROR_ARGUMENTで状態を変更しない。
 */
/* Count must be 1..65535. Invalid arguments leave state untouched.
 * Success returns UNCHANGED; errors return ERROR_ARGUMENT.
 */
int pc98qr_manual_selection_init(struct pc98qr_manual_selection *state,
                                 unsigned short frame_count);

/*
 * 論理イベントを一つ処理する。
 * 空Enterは無変化、不正な確定は入力と前回選択を保持する。
 * 6桁目で桁数超過を固定し、Backspaceで最後の保存済み桁を削除するまで解除しない。
 * Escは一度だけEXITを返し、その後は無変化。
 * 初期化済み状態が必要で、キーの物理的なチャタリング除去はしない。
 */
/* Accept one logical event. Empty Enter and unrelated keys are UNCHANGED.
 * Invalid Enter returns REJECTED, preserving digits and previous selection.
 * The sixth digit latches TOO_LONG; further digits cannot extend or repair it.
 * Enter remains rejected until Backspace deletes the LAST SAVED digit AND
 * clears the error (123456 -> saved 12345/error -> Backspace -> 1234).
 * Other errors clear on a digit edit or Backspace. Escape returns EXIT once;
 * all subsequent events are UNCHANGED, including Escape. NULL is an argument
 * error. Non-NULL state must have been successfully initialized.
 * Repeated digits are ordinary input; this is not a physical-key debouncer.
 */
int pc98qr_manual_selection_input(struct pc98qr_manual_selection *state,
                                  unsigned int key);

#endif
