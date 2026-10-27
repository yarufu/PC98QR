/*
 * 転送プロトコルversion 01の9バイトヘッダーを生成・解析する。
 * P98Q、version、0始まりのindex、総数を扱い、数値2項目はBE16で格納する。
 * ペイロードの内容や全フレームの整合性は検証しない。
 * version 02の処理はframe_v2.cに分離されている。
 */

#include "frame_header.h"

#include <limits.h>
#include <stddef.h>

#if UCHAR_MAX != 255 || USHRT_MAX != 65535
#error "PC98QR frame headers require 8-bit unsigned char and 16-bit unsigned short"
#endif

int pc98qr_frame_header_serialize(
    unsigned short frame_index,
    unsigned short frame_count,
    unsigned char out[PC98QR_FRAME_HEADER_SIZE])
{
    if (frame_count == 0)
        return PC98QR_FRAME_HEADER_ERROR_COUNT;
    if (frame_index >= frame_count)
        return PC98QR_FRAME_HEADER_ERROR_INDEX;
    if (out == NULL)
        return PC98QR_FRAME_HEADER_ERROR_ARGUMENT;

    out[0] = 0x50;
    out[1] = 0x39;
    out[2] = 0x38;
    out[3] = 0x51;
    out[4] = PC98QR_FRAME_PROTOCOL_VERSION;
    out[5] = (unsigned char)(frame_index >> 8);
    out[6] = (unsigned char)(frame_index & 0xFFu);
    out[7] = (unsigned char)(frame_count >> 8);
    out[8] = (unsigned char)(frame_count & 0xFFu);
    return PC98QR_FRAME_HEADER_OK;
}

int pc98qr_frame_header_parse(
    const unsigned char *bytes,
    unsigned int length,
    unsigned short *frame_index,
    unsigned short *frame_count)
{
    unsigned short parsed_index;
    unsigned short parsed_count;

    if (length < PC98QR_FRAME_HEADER_SIZE)
        return PC98QR_FRAME_HEADER_ERROR_LENGTH;
    if (bytes == NULL || frame_index == NULL || frame_count == NULL)
        return PC98QR_FRAME_HEADER_ERROR_ARGUMENT;
    if (bytes[0] != 0x50 || bytes[1] != 0x39 ||
        bytes[2] != 0x38 || bytes[3] != 0x51)
        return PC98QR_FRAME_HEADER_ERROR_MAGIC;
    if (bytes[4] != PC98QR_FRAME_PROTOCOL_VERSION)
        return PC98QR_FRAME_HEADER_ERROR_VERSION;

    /*
     * ia16のintは16ビットなので、シフト前に符号なし型へ変換する。
     */
    /* Cast before shifting: int is only 16 bits on ia16. */
    parsed_index = (unsigned short)(((unsigned int)bytes[5] << 8) | bytes[6]);
    parsed_count = (unsigned short)(((unsigned int)bytes[7] << 8) | bytes[8]);
    if (parsed_count == 0)
        return PC98QR_FRAME_HEADER_ERROR_COUNT;
    if (parsed_index >= parsed_count)
        return PC98QR_FRAME_HEADER_ERROR_INDEX;

    *frame_index = parsed_index;
    *frame_count = parsed_count;
    return PC98QR_FRAME_HEADER_OK;
}
