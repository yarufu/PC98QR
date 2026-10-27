/*
 * QSTATUSは固定転送QRと状態ラベルを表示する診断。
 * QRTESTへリンクしない。
 */
/* QSTATUS: one fixed transport QR and status labels; not linked into QRTEST. */
#include "pc98_graphics.h"
#include "pc98_status_font.h"
#include "frame_header.h"
#include "qr_encode.h"

static unsigned char fixed_frame[QR_V40L_BYTE_MAX];
static unsigned char fixed_matrix[QR_V40_PACKED_BYTES];

/*
 * ビット詰めV40を診断専用に走査し、既存の黒矩形APIへ渡す。
 * 本番main/draw_qrは変えず、ホストで画面全体を本番出力と比較する。
 */
/* Diagnostic-only scan of a packed V40 symbol. A single advancing bit index
 * feeds the existing black rectangle API. Production main/draw_qr is untouched;
 * host tests compare the result with production main's complete framebuffer.
 */
static void draw_fixed_qr(void)
{
    unsigned int bit;
    unsigned int x = 143;
    unsigned int y = 23;
    for (bit = 0; bit < QR_V40_SIZE * QR_V40_SIZE; ++bit) {
        if ((fixed_matrix[bit >> 3] & (0x80u >> (bit & 7u))) != 0)
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
    unsigned int i;
    int result;
    (void)argv;
    /*
     * probe専用終了コードはusage=1、ヘッダー=2、符号化=3、状態表示=4。
     */
    /* Probe-only exit codes: usage=1, header=2, encoder=3, status=4. */
    if (argc != 1)
        return 1;
    if (pc98qr_frame_header_serialize(0, 24, fixed_frame) != PC98QR_FRAME_HEADER_OK)
        return 2;
    for (i = 0; i < QR_V40L_BYTE_MAX - PC98QR_FRAME_HEADER_SIZE; ++i) {
        /*
         * 元の式と下位バイトを一致させる。
         * 先にiへマスクを掛け、16ビットunsigned intでも中間値を65536未満に保つ。
         */
        /* Same low byte as i*37 + (i>>8)*11 + 3. Masking i first keeps
         * every intermediate below 65536 even with 16-bit unsigned int.
         */
        fixed_frame[PC98QR_FRAME_HEADER_SIZE + i] =
            (unsigned char)(((i & 255u) * 37u + (i >> 8) * 11u + 3u) & 255u);
    }
    if (qr_encode_v40l_bytes(fixed_frame, sizeof(fixed_frame), fixed_matrix) != QR_OK)
        return 3;
    graphics_init();
    screen_clear();
    draw_fixed_qr();
    result = pc98qr_status_fixed();
    if (result != PC98QR_STATUS_OK) {
        graphics_shutdown();
        return 4;
    }
    graphics_show();
    wait_key();
    graphics_shutdown();
    return 0;
}
