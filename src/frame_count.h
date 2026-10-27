#ifndef PC98QR_FRAME_COUNT_H
#define PC98QR_FRAME_COUNT_H

/*
 * version 01 の容量はV40-Lの2953バイトから9バイトのヘッダーを除いた値。
 */
/* Version 1: V40-L decoded capacity (2953) minus the 9-byte header. */
#define PC98QR_FRAME_PAYLOAD_MAX 2944UL
#define PC98QR_FRAME_COUNT_MAX 65535U

enum pc98qr_frame_count_result {
    PC98QR_FRAME_COUNT_OK = 0,
    PC98QR_FRAME_COUNT_ERROR_ARGUMENT = 1,
    PC98QR_FRAME_COUNT_ERROR_SIZE = 2
};

/*
 * 既知の非空サイズから1～65535の切上げフレーム数を求める。
 * サイズを先に検証し、成功時だけ出力を更新する。
 * 192935040バイトは転送形式の数学的上限であり、実用上の保証ではない。
 * 除算と余りで加算オーバーフローを避ける。
 * I/OやQR処理は行わない。
 */
/* Count for an already known size; no file I/O or QR dependencies.
 * Check order: nonempty size fitting 1..65535 frames, then non-NULL output.
 * Errors leave output untouched; success writes the exact ceiling count.
 * 192935040 bytes is only the wire field's mathematical maximum, not a
 * production file size limit. Division/remainder avoid size addition overflow.
 * No heap, static workspace, recursion, offsets or payload access.
 */
int pc98qr_frame_count_for_size(
    unsigned long file_size,
    unsigned short *frame_count_out
);

#endif
