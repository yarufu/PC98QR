/*
 * 通常のバイナリファイルを読み取り専用で開き、末尾への seek でサイズを得る。
 * 内容を読み込まず、空ファイルはサイズ 0 として成功する。転送可能なサイズか
 * どうかの判定は、この処理ではなく呼出し側のフレーム数計算等で行う。
 * open 成功後は seek の成否にかかわらず close を一度試みる。seek が失敗した
 * 場合はそのエラーを優先し、seek と close の両方が成功した場合だけ出力を更新する。
 * ia16 の符号付き 32 ビット off_t を扱い、負の結果を検査してからサイズへ変換する。
 * この結果はその時点の長さであり、ファイル内容の同一性や後続の読み取りまでの
 * 不変性を保証するものではない。
 */

#include <fcntl.h>
#include <limits.h>
#include <stddef.h>
#include <sys/types.h>
#include <unistd.h>
#include "file_size.h"

/*
 * 異なるABIで値を暗黙に縮小せずコンパイル時に拒否する。
 * ia16では負値を検査してから32ビット符号なし値へ変換し、ホストでは正の範囲を保持できる幅を許す。
 */
/* Fail compilation rather than silently narrowing an offset on another ABI.
 * ia16: signed 32-bit off_t -> unsigned 32-bit long after the negative check.
 * Host: allow wider off_t when unsigned long can still hold its positive range.
 */
typedef char file_size_type_check[
    (CHAR_BIT == 8 && sizeof(off_t) >= 4 && (off_t)-1 < (off_t)0 &&
     sizeof(off_t) <= sizeof(unsigned long) && ULONG_MAX >= 4294967295UL)
    ? 1 : -1];

int pc98qr_file_size(const char *path, unsigned long *size_out)
{
    int flags = O_RDONLY;
    int fd;
    off_t end;
    int result = PC98QR_FILE_SIZE_OK;

    if (path == NULL || path[0] == '\0' || size_out == NULL)
        return PC98QR_FILE_SIZE_ERROR_ARGUMENT;
#ifdef O_BINARY
    flags |= O_BINARY;
#endif
    fd = open(path, flags);
    if (fd < 0)
        return PC98QR_FILE_SIZE_ERROR_OPEN;

    end = lseek(fd, (off_t)0, SEEK_END);
    if (end < (off_t)0)
        result = PC98QR_FILE_SIZE_ERROR_SEEK;
    if (close(fd) < 0 && result == PC98QR_FILE_SIZE_OK)
        result = PC98QR_FILE_SIZE_ERROR_CLOSE;
    if (result == PC98QR_FILE_SIZE_OK)
        *size_out = (unsigned long)end;
    return result;
}
