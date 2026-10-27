/*
 * 通常のバイナリファイルを読み取り専用で開き、指定位置から部分読み取りする。
 * 引数とオフセットの表現範囲を検証してから seek し、指定容量または EOF まで
 * read を繰り返す。正の短い read は EOF と見なさず、残りの読み取りを続ける。
 * 00・1A・80・FF 等も元データとして扱い、変換・終端・埋め草は加えない。
 * open 成功後はエラー時も close を一度試み、先に発生したエラーを優先する。
 * 取得長は close を含めて成功した場合だけ返す。失敗時のバッファ内容は使わない。
 * フレーム番号からの位置計算やヘッダー付加は呼出し側の責務であり、ここでは
 * 指定されたバイト位置と容量だけを扱う。
 */

#include <fcntl.h>
#include <limits.h>
#include <stddef.h>
#include <sys/types.h>
#include <unistd.h>
#include "file_chunk.h"

/*
 * ia16 の off_t は符号付き32ビット、ホストでは64ビット。
 * 未対応のABIは拒否し、オフセットを暗黙に縮小しない。
 * 読み取り量は16ビットの符号付き int に収まる。
 */
/* ia16 off_t is signed long (32 bits); the host uses signed long (64 bits).
 * Reject unsupported ABIs rather than narrowing a caller's offset silently.
 * All read requests and positive results fit even ia16's signed 16-bit int.
 */
typedef char file_chunk_type_check[
    (CHAR_BIT == 8 && sizeof(off_t) == sizeof(long) &&
     (off_t)-1 < (off_t)0 && (off_t)LONG_MAX == LONG_MAX &&
     ULONG_MAX >= 4294967295UL && INT_MAX >= PC98QR_FRAME_PAYLOAD_MAX &&
     UINT_MAX >= PC98QR_FRAME_PAYLOAD_MAX &&
     (ssize_t)-1 < (ssize_t)0 &&
     (ssize_t)PC98QR_FRAME_PAYLOAD_MAX == PC98QR_FRAME_PAYLOAD_MAX &&
     (size_t)PC98QR_FRAME_PAYLOAD_MAX == PC98QR_FRAME_PAYLOAD_MAX)
    ? 1 : -1];

int pc98qr_read_file_chunk(
    const char *path, unsigned long offset, unsigned char *buffer,
    unsigned int buffer_capacity, unsigned int *length_out)
{
    int flags = O_RDONLY;
    int fd;
    off_t requested;
    off_t position;
    unsigned int total = 0;
    int result = PC98QR_FILE_CHUNK_OK;

    if (path == NULL || path[0] == '\0' || buffer == NULL ||
        length_out == NULL || buffer_capacity == 0 ||
        buffer_capacity > PC98QR_FRAME_PAYLOAD_MAX)
        return PC98QR_FILE_CHUNK_ERROR_ARGUMENT;
    if (offset > (unsigned long)LONG_MAX)
        return PC98QR_FILE_CHUNK_ERROR_OFFSET;
    requested = (off_t)offset;
#ifdef O_BINARY
    flags |= O_BINARY;
#endif
    fd = open(path, flags);
    if (fd < 0)
        return PC98QR_FILE_CHUNK_ERROR_OPEN;

    position = lseek(fd, requested, SEEK_SET);
    if (position < (off_t)0 || position != requested)
        result = PC98QR_FILE_CHUNK_ERROR_SEEK;
    while (result == PC98QR_FILE_CHUNK_OK && total < buffer_capacity) {
        ssize_t count = read(fd, buffer + total, buffer_capacity - total);
        if (count < 0) {
            result = PC98QR_FILE_CHUNK_ERROR_READ;
            break;
        }
        if (count == 0) {
            if (total == 0)
                result = PC98QR_FILE_CHUNK_ERROR_NO_DATA;
            break;
        }
        total += (unsigned int)count;
    }
    if (close(fd) < 0 && result == PC98QR_FILE_CHUNK_OK)
        result = PC98QR_FILE_CHUNK_ERROR_CLOSE;
    if (result == PC98QR_FILE_CHUNK_OK)
        *length_out = total;
    return result;
}
