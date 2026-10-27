/*
 * PC-98 BIOSのキー確認・取得を、手動選択で使う論理イベントへ橋渡しする。
 * 未入力なら待たずに戻り、入力がある場合だけ1キーを消費する。
 * Enter・Backspace・EscはAXの完全一致、数字はALで分類する。
 * 画面描画や選択状態の更新は呼出し側が担当する。
 */

#include "pc98_key_input.h"

#include <limits.h>
#include <stddef.h>
#include <stdint.h>

#if CHAR_BIT != 8 || USHRT_MAX != 65535
#error "PC-98 key events require 8-bit bytes and 16-bit unsigned short"
#endif

/*
 * 出典はPC-9800 Technical Data Book BIOS（ASCII、1992年）の指定ページ。
 * 独立したKEYPROBEと同様にAX/BX全体を束縛してバイトレジスタ割当ての曖昧さを避ける。
 * ホスト試験ではBIOS呼出しだけを差し替える。
 */
/* PC-9800 Technical Data Book BIOS (ASCII, 1992), pp.20-24,30.
 * Same whole-AX/BX constraints as the independent KEYPROBE; no byte-register
 * allocation ambiguity. The test seam substitutes only the BIOS calls.
 */
#ifdef PC98QR_KEY_INPUT_HOST_TEST
static uint16_t bios_peek_key(unsigned char *bh);
static uint16_t bios_read_key(void);
#else
static uint16_t bios_peek_key(unsigned char *bh)
{
    uint16_t ax = 0x0100;
    uint16_t bx = 0;
    __asm__ __volatile__("int $0x18"
                         : "+a"(ax), "+b"(bx)
                         : : "cx", "dx", "cc", "memory");
    *bh = (unsigned char)(bx >> 8);
    return ax;
}

static uint16_t bios_read_key(void)
{
    uint16_t ax = 0;
    __asm__ __volatile__("int $0x18"
                         : "+a"(ax) : : "bx", "cx", "dx", "cc", "memory");
    return ax;
}
#endif

int pc98qr_key_poll(unsigned short *ax_out)
{
    unsigned char bh;
    if (ax_out == NULL)
        return PC98QR_KEY_ERROR_ARGUMENT;
    (void)bios_peek_key(&bh);
    if (bh == 0)
        return PC98QR_KEY_EMPTY;
    if (bh != 1)
        return PC98QR_KEY_ERROR_STATUS;
    *ax_out = bios_read_key();
    return PC98QR_KEY_READY;
}

unsigned int pc98qr_key_classify(unsigned short ax)
{
    unsigned int character = ax & 0xFFU;
    if (ax == 0x1C0DU)
        return PC98QR_MANUAL_KEY_ENTER;
    if (ax == 0x0E08U)
        return PC98QR_MANUAL_KEY_BACKSPACE;
    if (ax == 0x001BU)
        return PC98QR_MANUAL_KEY_ESCAPE;
    if (character >= '0' && character <= '9')
        return character;
    return PC98QR_MANUAL_KEY_IGNORE;
}
