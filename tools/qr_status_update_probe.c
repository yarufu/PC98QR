/*
 * QUPDは表示済みの固定QR上でSELECTを更新する診断。
 * QRTEST/QSTATUSから独立している。
 */
/* QUPD: update SELECT over a displayed fixed QR; independent of QRTEST/QSTATUS. */
#include "pc98_graphics.h"
#include "pc98_status_font.h"
#include "frame_header.h"
#include "qr_encode.h"

static unsigned char fixed_frame[QR_V40L_BYTE_MAX];
static unsigned char fixed_matrix[QR_V40_PACKED_BYTES];
/*
 * 長さ明示のフォント入力は1行正確に5バイトで、NULを含めない。
 */
/* Explicit-length font input: exactly five bytes per row, without a NUL. */
static const char select_updates[4][5] = {"23   ", "2    ", "00023", "-    "};

/*
 * QSTATUSと同じ形状の診断用ビット走査。
 * 本番描画は内部に保ち、ホストでQRTESTと全画素を比較する。
 */
/* Diagnostic-only packed scan, matching QSTATUS's geometry. Keep the existing
 * production renderer private; host tests compare every pixel with QRTEST.
 */
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
    unsigned int i;
    (void)argv;
    /*
     * probe専用終了コードは成功0、usage1、ヘッダー2、符号化3、状態表示4。
     * グラフィック開始後の失敗は必ず一度shutdownする。
     */
    /* Probe-only exits: success=0, usage=1, header=2, encoder=3, any status=4.
     * Every failure after graphics_init shuts graphics down exactly once.
     */
    if (argc != 1)
        return 1;
    if (pc98qr_frame_header_serialize(0, 24, fixed_frame) != PC98QR_FRAME_HEADER_OK)
        return 2;
    for (i = 0; i < QR_V40L_BYTE_MAX - PC98QR_FRAME_HEADER_SIZE; ++i) {
        /*
         * 下位バイトは同等で、全中間値がia16のunsigned intに収まる。
         */
        /* Equivalent low byte; all intermediates fit in ia16 unsigned int. */
        fixed_frame[PC98QR_FRAME_HEADER_SIZE + i] =
            (unsigned char)(((i & 255u) * 37u + (i >> 8) * 11u + 3u) & 255u);
    }
    if (qr_encode_v40l_bytes(fixed_frame, sizeof(fixed_frame), fixed_matrix) != QR_OK)
        return 3;

    graphics_init();
    screen_clear();
    draw_fixed_qr();
    if (pc98qr_status_fixed() != PC98QR_STATUS_OK) {
        graphics_shutdown();
        return 4;
    }
    graphics_show();
    for (i = 0; i < sizeof(select_updates) / sizeof(select_updates[0]); ++i) {
        /*
         * 消費したBIOSイベント一つで1段階進む。
         * 解放・反復フィルターはなく、背景込みの空白で旧数字だけを消しQRは描き直さない。
         */
        /* One consumed BIOS event advances one stage; no release/repeat filter.
         * Opaque spaces erase old digits without clearing or redrawing the QR.
         */
        wait_key();
        if (pc98qr_status_text(520, 96, select_updates[i], 5) != PC98QR_STATUS_OK) {
            graphics_shutdown();
            return 4;
        }
    }
    wait_key();
    graphics_shutdown();
    return 0;
}
