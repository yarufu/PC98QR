#ifndef PC98QR_FRAME_OFFSET_H
#define PC98QR_FRAME_OFFSET_H

enum pc98qr_frame_offset_result {
    PC98QR_FRAME_OFFSET_OK = 0,
    PC98QR_FRAME_OFFSET_ERROR_COUNT = 1,
    PC98QR_FRAME_OFFSET_ERROR_INDEX = 2,
    PC98QR_FRAME_OFFSET_ERROR_ARGUMENT = 3
};

/*
 * version 01 の0始まりindexをファイル位置へ変換する。
 * 総数、index、出力ポインタを検証し、32ビット以上のunsigned longで容量との積を計算する。
 * 失敗時は出力を保持する。
 * 最大位置は転送形式由来で、実用サイズ保証ではない。
 * I/Oやデータ取得は行わない。
 */
/* Version 1 file offset for a 0-origin index in a count of 1..65535 frames.
 * Check order: nonzero count, index < count, then non-NULL offset_out.
 * Success writes index * payload_max in unsigned long (at least 32 bits).
 * Errors leave output untouched. The maximum valid offset is 192932096;
 * this is a wire-derived value, not a production file size limit.
 * No file I/O, size/count lookup, chunk read, header or QR dependencies.
 * No heap, static workspace, recursion or payload access.
 */
int pc98qr_frame_offset_for_index(
    unsigned short frame_index,
    unsigned short frame_count,
    unsigned long *offset_out
);

#endif
