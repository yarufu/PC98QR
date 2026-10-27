/*
 * PC-98 の 640×400 グラフィック画面を扱うハードウェア依存部分。
 * BIOS と I/O ポートで表示モード・パレットを設定し、B/R/G/I の 4 プレーンの
 * VRAM を CPU で直接操作する。各バイトの上位ビットが左側の画素に対応する。
 * 4 プレーンのビットを立てて白で消去し、すべて落として黒い QR セルを描く。
 * 初期描画中はグラフィックを隠し、完成後に表示する。終了時はグラフィックを
 * 隠してテキスト表示を再開するが、以前のグラフィック内容・モード・パレットは
 * 保存・復元しない。
 */

#include "pc98_graphics.h"

#include <stdint.h>

#define BYTES_PER_LINE (SCREEN_WIDTH / 8u)
#define PLANE_BYTES (BYTES_PER_LINE * SCREEN_HEIGHT)
#define VRAM_B ((volatile uint8_t __far *)0xA8000000UL)
#define VRAM_R ((volatile uint8_t __far *)0xB0000000UL)
#define VRAM_G ((volatile uint8_t __far *)0xB8000000UL)
#define VRAM_I ((volatile uint8_t __far *)0xE0000000UL)

/*
 * I/OとBIOS呼出しだけを最小限の接着処理とし、描画は通常のCで行う。
 */
/* Minimal I/O and BIOS glue; all drawing is ordinary C. */
static void out8(uint16_t port, uint8_t value)
{
    /*
     * ia16ではバイトのa制約がAHへ割り当てられる場合があるため、ALを使うOUT命令に対してAX全体を束縛する。
     */
    /* Bind the whole AX: ia16 can place a byte-sized "a" operand in AH,
     * whereas OUT below explicitly consumes AL. */
    __asm__ __volatile__("outb %%al, %%dx"
                         : : "a"((uint16_t)value), "d"(port) : "memory");
}

static void bios_display(uint16_t ax)
{
    __asm__ __volatile__("int $0x18"
                         : "+a"(ax) : : "bx", "cx", "dx", "cc", "memory");
}

static void set_gray_palette(uint8_t index, uint8_t level)
{
    out8(0x00A8, index);
    out8(0x00AA, level); /* Green: 4 bits. */ /* 緑成分は4ビット。 */
    out8(0x00AC, level); /* Red. */ /* 赤成分。 */
    out8(0x00AE, level); /* Blue. */ /* 青成分。 */
}

void graphics_init(void)
{
    uint16_t ax = 0x4200;
    uint16_t cx = 0xC000;

    bios_display(0x0D00); /* Hide text, preserving text VRAM and cursor state. */ /* テキストVRAMとカーソル状態を保持して、文字表示だけを隠す。 */
    bios_display(0x4100); /* Keep graphics hidden until the QR is complete. */ /* QRの描画完了まではグラフィックを隠す。 */
    __asm__ __volatile__("int $0x18"
                         : "+a"(ax), "+c"(cx)
                         : : "bx", "dx", "cc", "memory");
    /*
     * AH=42h、CH=C0hでカラー・ページ0・640×400全域を指定する。
     */
    /* AH=42h, CH=C0h: color, page 0, full 640x400 display area. */
    out8(0x00A6, 0);    /* CPU access page 0. */ /* CPUからのアクセス先をページ0にする。 */
    out8(0x00A4, 0);    /* Display page 0. */ /* 表示ページを0にする。 */
    out8(0x006A, 0x01); /* Enable analog 16-color palette. */ /* アナログ16色パレットを有効にする。 */
    out8(0x007C, 0);    /* Disable GRCG; use CPU reads/writes only. */ /* GRCGを無効化し、CPUの読み書きだけを使う。 */
    set_gray_palette(0, 0x00);
    set_gray_palette(15, 0x0F);
}

void screen_clear(void)
{
    uint16_t offset;

    /*
     * 4ビットすべてを立てるとパレット15の白になる。
     * Quiet Zoneも白で埋める。
     */
    /* All four bits set => palette 15 (white), including the quiet zone. */
    for (offset = 0; offset < PLANE_BYTES; ++offset) {
        VRAM_B[offset] = 0xFF;
        VRAM_R[offset] = 0xFF;
        VRAM_G[offset] = 0xFF;
        VRAM_I[offset] = 0xFF;
    }
}

void box_fill(unsigned int x, unsigned int y,
              unsigned int width, unsigned int height)
{
    unsigned int row;
    unsigned int column;

    for (row = y; row < y + height; ++row) {
        for (column = x; column < x + width; ++column) {
            uint16_t offset = row * BYTES_PER_LINE + column / 8u;
            uint8_t keep = (uint8_t)~(0x80u >> (column % 8u));

            /*
             * 最も左の画素はbit 7。
             * 4色プレーンのビットをすべて落とすと黒になる。
             */
            /* Leftmost pixel is bit 7. Clear all four color bits => black. */
            VRAM_B[offset] &= keep;
            VRAM_R[offset] &= keep;
            VRAM_G[offset] &= keep;
            VRAM_I[offset] &= keep;
        }
    }
}

void graphics_show(void)
{
    bios_display(0x4000);
}

void wait_key(void)
{
    /*
     * AH=00hはキーが来るまで待ち、その1キーを消費する。
     */
    /* AH=00h blocks until one BIOS key is available and consumes it. */
    bios_display(0x0000);
}

void graphics_shutdown(void)
{
    bios_display(0x4100); /* Remove the white graphics background from DOS. */ /* DOSへ戻る際に白いグラフィック背景を隠す。 */
    out8(0x00A6, 0);
    out8(0x00A4, 0);
    bios_display(0x0C00); /* Re-enable the original text console. */ /* 元のテキストコンソール表示を再開する。 */
    /*
     * 以前のグラフィック画素・モード・パレットは保存も復元もしない。
     */
    /* Previous graphics pixels/mode/palette are not saved or restored. */
}
