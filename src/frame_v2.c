/*
 * ファイル名を含む転送プロトコル version 02 の計算と直列化を担当する。
 * 共通ヘッダーは P98Q・version・index・総数の 9 バイトで、index と総数は BE16。
 * 先頭フレームだけに名前長 N と名前 N バイトが続くため、生データ容量は
 * 2944-(1+N) バイト、後続は 2944 バイトとなる。後続 index のファイル位置は
 * index*2944-(1+N)。最終フレームは実データ長だけを送り、埋め草は加えない。
 * ファイル読み取りや QR 生成は呼出し側の責務。ここでは引数と容量を検証し、
 * 成功が確定してから出力を更新する。入力と出力の領域は重複させない。
 */

#include "frame_v2.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

#if CHAR_BIT != 8 || USHRT_MAX != 65535 || ULONG_MAX < 0xFFFFFFFFUL || UINT_MAX < 65535U
#error "PC98QR v2 requires 8-bit bytes, 16-bit ushort, uint >=16 and ulong >=32 bits"
#endif

int pc98qr_frame_v2_count_for_size(unsigned long file_size,
                                 unsigned int filename_length,
                                 unsigned short *frame_count_out)
{
    unsigned long first_capacity, count = 1;
    if (filename_length == 0 || filename_length > PC98QR_FILENAME_V2_MAX)
        return PC98QR_FRAME_V2_ERROR_FILENAME;
    if (file_size == 0)
        return PC98QR_FRAME_V2_ERROR_SIZE;
    first_capacity = PC98QR_FRAME_V2_PAYLOAD_MAX - (1UL + filename_length);
    if (file_size > first_capacity) {
        unsigned long remaining = file_size - first_capacity;
        unsigned long whole = remaining / PC98QR_FRAME_V2_PAYLOAD_MAX;
        unsigned long remainder = remaining % PC98QR_FRAME_V2_PAYLOAD_MAX;
        /*
         * 先頭以外のフレームは最大65534個。
         * 加算や型の縮小より先に検証する。
         */
        /* At most 65534 frames after frame 0. Check before adding/narrowing. */
        if (whole > PC98QR_FRAME_V2_COUNT_MAX - 1UL ||
            (whole == PC98QR_FRAME_V2_COUNT_MAX - 1UL && remainder != 0))
            return PC98QR_FRAME_V2_ERROR_SIZE;
        count += whole + (remainder != 0);
    }
    if (frame_count_out == NULL)
        return PC98QR_FRAME_V2_ERROR_ARGUMENT;
    *frame_count_out = (unsigned short)count;
    return PC98QR_FRAME_V2_OK;
}

int pc98qr_frame_v2_layout_for_index(unsigned long file_size,
                                   unsigned int filename_length,
                                   unsigned short frame_index,
                                   struct pc98qr_frame_v2_layout *layout_out)
{
    struct pc98qr_frame_v2_layout value;
    unsigned int metadata;
    unsigned long remaining;
    int result = pc98qr_frame_v2_count_for_size(file_size, filename_length,
                                             &value.frame_count);
    if (result != PC98QR_FRAME_V2_OK)
        return result;
    if (frame_index >= value.frame_count)
        return PC98QR_FRAME_V2_ERROR_INDEX;
    if (layout_out == NULL)
        return PC98QR_FRAME_V2_ERROR_ARGUMENT;
    metadata = 1U + filename_length;
    value.file_offset = frame_index == 0 ? 0UL :
        (unsigned long)frame_index * PC98QR_FRAME_V2_PAYLOAD_MAX - metadata;
    if (value.file_offset >= file_size)
        return PC98QR_FRAME_V2_ERROR_INDEX;
    value.file_capacity = (unsigned int)PC98QR_FRAME_V2_PAYLOAD_MAX -
                         (frame_index == 0 ? metadata : 0U);
    value.payload_offset = PC98QR_FRAME_V2_HEADER_SIZE +
                           (frame_index == 0 ? metadata : 0U);
    remaining = file_size - value.file_offset;
    value.file_length = remaining > value.file_capacity ? value.file_capacity :
                        (unsigned int)remaining;
    value.frame_length = value.payload_offset + value.file_length;
    *layout_out = value;
    return PC98QR_FRAME_V2_OK;
}

int pc98qr_frame_v2_serialize(const unsigned char *filename,
                            unsigned int filename_length,
                            unsigned long file_size, unsigned short frame_index,
                            const unsigned char *file_chunk,
                            unsigned int file_chunk_length,
                            unsigned char *out, unsigned int out_capacity,
                            unsigned int *length_out)
{
    struct pc98qr_frame_v2_layout layout;
    int result;
    if (filename == NULL || file_chunk == NULL || out == NULL || length_out == NULL)
        return PC98QR_FRAME_V2_ERROR_ARGUMENT;
    if (pc98qr_filename_v2_validate(filename, filename_length) != PC98QR_FILENAME_V2_OK)
        return PC98QR_FRAME_V2_ERROR_FILENAME;
    result = pc98qr_frame_v2_layout_for_index(file_size, filename_length,
                                           frame_index, &layout);
    if (result != PC98QR_FRAME_V2_OK)
        return result;
    if (file_chunk_length != layout.file_length)
        return PC98QR_FRAME_V2_ERROR_LENGTH;
    if (out_capacity < layout.frame_length)
        return PC98QR_FRAME_V2_ERROR_CAPACITY;
    out[0] = 0x50;
    out[1] = 0x39;
    out[2] = 0x38;
    out[3] = 0x51;
    out[4] = PC98QR_FRAME_V2_VERSION;
    out[5] = (unsigned char)(frame_index >> 8);
    out[6] = (unsigned char)(frame_index & 0xFFU);
    out[7] = (unsigned char)(layout.frame_count >> 8);
    out[8] = (unsigned char)(layout.frame_count & 0xFFU);
    if (frame_index == 0) {
        out[9] = (unsigned char)filename_length;
        memcpy(out + 10, filename, filename_length);
    }
    memcpy(out + layout.payload_offset, file_chunk, file_chunk_length);
    *length_out = layout.frame_length;
    return PC98QR_FRAME_V2_OK;
}
