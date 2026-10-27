#ifndef PC98QR_STATUS_FONT_H
#define PC98QR_STATUS_FONT_H

enum pc98qr_status_result {
    PC98QR_STATUS_OK = 0,
    PC98QR_STATUS_ERROR_ARGUMENT,
    PC98QR_STATUS_ERROR_CHARACTER
};

/*
 * 右側領域だけに白背景・黒文字の8×16セルを描く。
 * graphics_init済みのページ0・アナログ16色・GRCG無効が前提。
 * xは520～632の8の倍数、yは0～384。
 * 文字は指定された数字・英字・記号・空白のみで、NUL終端は不要。
 * 要求全体を検証してから描き、失敗時はVRAMを変更しない。
 * 切詰めやクリッピングはしない。
 */
/* Right-side-only opaque black-on-white 8x16 cells. Requires graphics_init()
 * settings: page 0, analog 16 colors (0 black / 15 white), GRCG disabled.
 * x: 520..632, multiple of 8; y: 0..384; length: 1..(640-x)/8.
 * text points to at least length readable bytes; no NUL is required. Supports
 * 0123456789CELNRSTU/- and space only (embedded NUL is unsupported).
 * Validate the ENTIRE request before writing; all errors leave VRAM untouched.
 * No clipping, truncation, allocation, graphics mode changes or text VRAM use.
 */
int pc98qr_status_text(unsigned int x, unsigned int y,
                       const char *text, unsigned int length);

/*
 * CURRENT・1/24・SELECT・-を指定の固定座標へ描く診断用処理。
 * QR生成や画面の開始・終了は管理しない。
 */
/* Fixed diagnostic labels only: CURRENT, 1/24, SELECT, - at x=520 and
 * y=32,48,80,96. Does not encode QR data or manage graphics lifecycle.
 */
int pc98qr_status_fixed(void);

#endif
