/*
 * QR右側の状態表示に使う、白背景・黒文字の8×16フォントを描画する。
 * 必要な数字・英字・記号だけを独自の小さな字形表に持ち、CG ROMを使わない。
 * 描画範囲と全ての文字を検証してから4プレーンのVRAMへ書き込む。
 * 画面初期化・ページ選択・パレット設定は呼出し側の責務。
 */

#include "pc98_status_font.h"
#include "pc98_graphics.h"

#include <limits.h>
#include <stddef.h>
#include <stdint.h>

#if CHAR_BIT != 8 || USHRT_MAX != 65535
#error "Status font requires 8-bit bytes and 16-bit unsigned short"
#endif

/*
 * 診断用に独自作成した小型フォント。
 * 5×7の字形を縦2倍にし、上下1行・左1列・右2列を空ける。
 * MSBが左端で、1が黒。
 * CG ROMや外部フォント、一般ASCII表は使わない。
 */
/* Original small block alphabet, drawn for this diagnostic. Each cell has
 * one blank top/bottom row and a five-column, seven-row design doubled in Y.
 * One blank column on the left, two on the right. MSB is the leftmost pixel;
 * a set bit means BLACK. No CG ROM, external font or general ASCII table.
 */
static const char glyph_characters[] = "0123456789CELNRSTU/- ";
static const unsigned char glyphs[21][16] = {
    {0, 0x38,0x38, 0x44,0x44, 0x4C,0x4C, 0x54,0x54, 0x64,0x64, 0x44,0x44, 0x38,0x38, 0}, /* 0 */
    {0, 0x10,0x10, 0x30,0x30, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0x38,0x38, 0}, /* 1 */
    {0, 0x38,0x38, 0x44,0x44, 0x04,0x04, 0x08,0x08, 0x10,0x10, 0x20,0x20, 0x7C,0x7C, 0}, /* 2 */
    {0, 0x78,0x78, 0x04,0x04, 0x04,0x04, 0x38,0x38, 0x04,0x04, 0x04,0x04, 0x78,0x78, 0}, /* 3 */
    {0, 0x08,0x08, 0x18,0x18, 0x28,0x28, 0x48,0x48, 0x7C,0x7C, 0x08,0x08, 0x08,0x08, 0}, /* 4 */
    {0, 0x7C,0x7C, 0x40,0x40, 0x40,0x40, 0x78,0x78, 0x04,0x04, 0x04,0x04, 0x78,0x78, 0}, /* 5 */
    {0, 0x38,0x38, 0x40,0x40, 0x40,0x40, 0x78,0x78, 0x44,0x44, 0x44,0x44, 0x38,0x38, 0}, /* 6 */
    {0, 0x7C,0x7C, 0x04,0x04, 0x08,0x08, 0x10,0x10, 0x20,0x20, 0x20,0x20, 0x20,0x20, 0}, /* 7 */
    {0, 0x38,0x38, 0x44,0x44, 0x44,0x44, 0x38,0x38, 0x44,0x44, 0x44,0x44, 0x38,0x38, 0}, /* 8 */
    {0, 0x38,0x38, 0x44,0x44, 0x44,0x44, 0x3C,0x3C, 0x04,0x04, 0x04,0x04, 0x38,0x38, 0}, /* 9 */
    {0, 0x38,0x38, 0x44,0x44, 0x40,0x40, 0x40,0x40, 0x40,0x40, 0x44,0x44, 0x38,0x38, 0}, /* C */
    {0, 0x7C,0x7C, 0x40,0x40, 0x40,0x40, 0x78,0x78, 0x40,0x40, 0x40,0x40, 0x7C,0x7C, 0}, /* E */
    {0, 0x40,0x40, 0x40,0x40, 0x40,0x40, 0x40,0x40, 0x40,0x40, 0x40,0x40, 0x7C,0x7C, 0}, /* L */
    {0, 0x44,0x44, 0x64,0x64, 0x64,0x64, 0x54,0x54, 0x4C,0x4C, 0x4C,0x4C, 0x44,0x44, 0}, /* N */
    {0, 0x78,0x78, 0x44,0x44, 0x44,0x44, 0x78,0x78, 0x50,0x50, 0x48,0x48, 0x44,0x44, 0}, /* R */
    {0, 0x3C,0x3C, 0x40,0x40, 0x40,0x40, 0x38,0x38, 0x04,0x04, 0x04,0x04, 0x78,0x78, 0}, /* S */
    {0, 0x7C,0x7C, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0x10,0x10, 0}, /* T */
    {0, 0x44,0x44, 0x44,0x44, 0x44,0x44, 0x44,0x44, 0x44,0x44, 0x44,0x44, 0x38,0x38, 0}, /* U */
    {0, 0x04,0x04, 0x04,0x04, 0x08,0x08, 0x10,0x10, 0x20,0x20, 0x40,0x40, 0x40,0x40, 0}, /* / */
    {0, 0,0, 0,0, 0,0, 0x7C,0x7C, 0,0, 0,0, 0,0, 0}, /* - */
    {0, 0,0, 0,0, 0,0, 0,0, 0,0, 0,0, 0,0, 0} /* space */ /* 空白文字。 */
};

/*
 * ホストでは最終的なVRAM書き込みだけを差し替える。
 * ループ・セグメント値・位置計算・ビット反転は実機と共通。
 */
/* Only the final hardware store is substituted on the host. The real loops,
 * physical segment values, offset calculation and inversion are shared.
 */
#ifdef PC98QR_STATUS_FONT_HOST_TEST
void pc98qr_status_font_store(uint16_t segment, uint16_t offset, uint8_t value);
#else
static void pc98qr_status_font_store(uint16_t segment, uint16_t offset,
                                      uint8_t value)
{
    volatile uint8_t __far *plane =
        (volatile uint8_t __far *)((uint32_t)segment << 16);
    plane[offset] = value;
}
#endif

static int glyph_index(char character)
{
    unsigned int i;
    for (i = 0; i < sizeof(glyphs) / sizeof(glyphs[0]); ++i) {
        if (character == glyph_characters[i])
            return (int)i;
    }
    return -1;
}

int pc98qr_status_text(unsigned int x, unsigned int y,
                       const char *text, unsigned int length)
{
    unsigned int cell;
    if (x < 520u || x > SCREEN_WIDTH - 8u || (x & 7u) != 0 ||
        y > SCREEN_HEIGHT - 16u || text == NULL || length == 0 ||
        length > (SCREEN_WIDTH - x) / 8u)
        return PC98QR_STATUS_ERROR_ARGUMENT;
    for (cell = 0; cell < length; ++cell) {
        if (glyph_index(text[cell]) < 0)
            return PC98QR_STATUS_ERROR_CHARACTER;
    }
    for (cell = 0; cell < length; ++cell) {
        unsigned int row;
        const unsigned char *glyph = glyphs[glyph_index(text[cell])];
        for (row = 0; row < 16u; ++row) {
            uint16_t offset = (y + row) * (SCREEN_WIDTH / 8u) + x / 8u + cell;
            uint8_t value = (uint8_t)~glyph[row];
            /*
             * pc98_graphics.cと同じB/R/G/I配置で、0は黒、15は白。
             * ここではページ選択やパレット・GRCG変更をしない。
             */
            /* Same B/R/G/I mapping as pc98_graphics.c; 0=black, 15=white.
             * This module neither selects a page nor changes palette/GRCG.
             */
            pc98qr_status_font_store(0xA800u, offset, value);
            pc98qr_status_font_store(0xB000u, offset, value);
            pc98qr_status_font_store(0xB800u, offset, value);
            pc98qr_status_font_store(0xE000u, offset, value);
        }
    }
    return PC98QR_STATUS_OK;
}

int pc98qr_status_fixed(void)
{
    int result = pc98qr_status_text(520, 32, "CURRENT", 7);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 48, "1/24", 4);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 80, "SELECT", 6);
    if (result == PC98QR_STATUS_OK)
        result = pc98qr_status_text(520, 96, "-", 1);
    return result;
}
