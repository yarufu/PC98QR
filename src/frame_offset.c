/*
 * version 01のフレームindexから、元ファイル内のバイト位置を計算する。
 * 総数とindexを検証し、32ビット以上の型へ広げてから2944を掛ける。
 * 読み取り自体はfile_chunk.cが担当する。
 * 先頭フレームの容量が異なるversion 02には、この位置計算を使用しない。
 */

#include "frame_offset.h"
#include "frame_count.h"

#include <limits.h>
#include <stddef.h>

#if CHAR_BIT != 8 || USHRT_MAX != 65535 || ULONG_MAX < 0xFFFFFFFFUL
#error "PC98QR frame offsets require 8-bit bytes, 16-bit unsigned short and at least 32-bit unsigned long"
#endif

int pc98qr_frame_offset_for_index(
    unsigned short frame_index,
    unsigned short frame_count,
    unsigned long *offset_out)
{
    if (frame_count == 0)
        return PC98QR_FRAME_OFFSET_ERROR_COUNT;
    if (frame_index >= frame_count)
        return PC98QR_FRAME_OFFSET_ERROR_INDEX;
    if (offset_out == NULL)
        return PC98QR_FRAME_OFFSET_ERROR_ARGUMENT;

    /*
     * ia16のintは16ビットなので乗算前に型を広げる。
     * 最大index 65534と容量2944の積192932096は32ビットに収まる。
     */
    /* Widen before multiplying: ia16 int is only 16 bits.
     * Valid index <= 65534; 65534 * 2944 = 192932096 fits in 32 bits.
     */
    *offset_out = (unsigned long)frame_index * PC98QR_FRAME_PAYLOAD_MAX;
    return PC98QR_FRAME_OFFSET_OK;
}
