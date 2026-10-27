/*
 * version 01で非空ファイルを2944バイト単位へ分割した際の枚数を計算する。
 * 除算と余りを使い、切上げ用の加算による桁あふれを避ける。
 * 転送形式の総数上限を検証するが、実機での転送時間や実用サイズは保証しない。
 * ファイルI/Oや、先頭にファイル名が入るversion 02の計算は担当しない。
 */

#include "frame_count.h"

#include <limits.h>
#include <stddef.h>

#if USHRT_MAX != 65535 || ULONG_MAX < 0xFFFFFFFFUL
#error "PC98QR frame counts require 16-bit unsigned short and at least 32-bit unsigned long"
#endif

int pc98qr_frame_count_for_size(
    unsigned long file_size,
    unsigned short *frame_count_out)
{
    unsigned long whole;
    unsigned long remainder;

    if (file_size == 0)
        return PC98QR_FRAME_COUNT_ERROR_SIZE;

    whole = file_size / PC98QR_FRAME_PAYLOAD_MAX;
    remainder = file_size % PC98QR_FRAME_PAYLOAD_MAX;
    /*
     * 商の加算や型の縮小より先に、転送形式のフレーム数上限を検査する。
     */
    /* Check the wire limit before incrementing or narrowing the quotient. */
    if (whole > (unsigned long)PC98QR_FRAME_COUNT_MAX ||
        (whole == (unsigned long)PC98QR_FRAME_COUNT_MAX && remainder != 0))
        return PC98QR_FRAME_COUNT_ERROR_SIZE;
    if (frame_count_out == NULL)
        return PC98QR_FRAME_COUNT_ERROR_ARGUMENT;

    if (remainder != 0)
        ++whole;
    *frame_count_out = (unsigned short)whole;
    return PC98QR_FRAME_COUNT_OK;
}
