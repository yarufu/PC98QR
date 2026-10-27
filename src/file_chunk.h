#ifndef PC98QR_FILE_CHUNK_H
#define PC98QR_FILE_CHUNK_H

#include "frame_count.h" /* Reuse the Version 1 payload limit; no count calls. */ /* version 01 のペイロード上限だけを再利用し、フレーム数計算は呼ばない。 */

enum pc98qr_file_chunk_result {
    PC98QR_FILE_CHUNK_OK = 0,
    PC98QR_FILE_CHUNK_ERROR_ARGUMENT = 1,
    PC98QR_FILE_CHUNK_ERROR_OFFSET = 2,
    PC98QR_FILE_CHUNK_ERROR_OPEN = 3,
    PC98QR_FILE_CHUNK_ERROR_SEEK = 4,
    PC98QR_FILE_CHUNK_ERROR_READ = 5,
    PC98QR_FILE_CHUNK_ERROR_CLOSE = 6,
    PC98QR_FILE_CHUNK_ERROR_NO_DATA = 7
};

/*
 * 指定位置から生バイトを読む。
 * 引数、容量1～2944、off_tの範囲を先に検証し、正確な位置へseekする。
 * 正の短いreadは継続し、容量を超えて読まない。
 * 1バイト以上でEOFなら成功、0バイトならNO_DATA。
 * open後はcloseを一度試み、先行エラーを優先する。
 * 取得長は成功時だけ更新し、失敗時のバッファは使用不可。
 * 容量分の領域と独立した長さ出力を呼出し側が用意する。
 * 位置計算・分割・QR処理は行わない。
 */
/* Read raw bytes from an ordinary binary file at the supplied byte offset.
 * Validate ARGUMENT (nonempty path, non-NULL buffer/output, capacity 1..2944),
 * then OFFSET (representable by signed off_t), before opening read-only.
 * Seek once with SEEK_SET and require the exact returned position. Continue
 * positive short reads until capacity or EOF; never probe beyond capacity.
 * EOF after 1..capacity bytes succeeds; zero bytes is ERROR_NO_DATA.
 * After open succeeds, attempt close exactly once. Preserve an earlier error;
 * otherwise a close failure is ERROR_CLOSE. Publish length only on success.
 * All errors leave length_out untouched; buffer contents are unspecified on
 * error and must not be used. Caller supplies at least buffer_capacity bytes
 * and a separate length_out object. No padding, terminator, heap or workspace.
 * Supported ABI: signed off_t with long's range, unsigned long >= 32 bits.
 * Result codes, not errno after cleanup, identify the failing operation.
 * No file size/count lookup, offset arithmetic, splitting or QR integration.
 */
int pc98qr_read_file_chunk(
    const char *path,
    unsigned long offset,
    unsigned char *buffer,
    unsigned int buffer_capacity,
    unsigned int *length_out
);

#endif
