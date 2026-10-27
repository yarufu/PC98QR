#ifndef PC98QR_FRAME_HEADER_H
#define PC98QR_FRAME_HEADER_H

#define PC98QR_FRAME_HEADER_SIZE 9
#define PC98QR_FRAME_PROTOCOL_VERSION 1

enum pc98qr_frame_header_result {
    PC98QR_FRAME_HEADER_OK = 0,
    PC98QR_FRAME_HEADER_ERROR_ARGUMENT,
    PC98QR_FRAME_HEADER_ERROR_LENGTH,
    PC98QR_FRAME_HEADER_ERROR_MAGIC,
    PC98QR_FRAME_HEADER_ERROR_VERSION,
    PC98QR_FRAME_HEADER_ERROR_COUNT,
    PC98QR_FRAME_HEADER_ERROR_INDEX
};

/*
 * version 01 の9バイトヘッダーはP98Q、01、index BE16、count BE16。
 * 総数1～65535、indexは0始まりで総数未満。
 * 総数・index・出力ポインタの順に検証し、成功時だけ9バイトを書き込む。
 */
/* Wire: 50 39 38 51 (P98Q), protocol version 01, index BE16, count BE16.
 * Count is 1..65535; index is 0-origin and strictly less than count.
 * Check order: count, index, then non-NULL out (at least 9 writable bytes).
 * Errors leave output untouched. Writes exactly 9 bytes on success.
 * No heap, I/O, static workspace, recursion or QR dependencies.
 */
int pc98qr_frame_header_serialize(
    unsigned short frame_index,
    unsigned short frame_count,
    unsigned char out[PC98QR_FRAME_HEADER_SIZE]
);

/*
 * ヘッダーなしQRを自動判別する関数ではない。
 * 長さ、ポインタ、識別子、version、総数、indexの順に検証し、先頭9バイトだけを読む。
 * ペイロードは検証しない。
 * プロトコルversionとQRのVersionは別概念。
 * 全検証後にだけ両出力を更新する。
 */
/* Explicit transport helper, not automatic detection of headerless raw QR.
 * Check order: length >= 9, all pointers non-NULL, magic, version, count, index.
 * Read only offsets 0..8, even when length > 9; no payload validation.
 * Magic has no NUL terminator. Protocol version is unrelated to QR version.
 * Write both outputs only after all validation succeeds; errors change neither.
 * No input writes; C wire accesses use bytes with explicit big-endian conversion.
 */
int pc98qr_frame_header_parse(
    const unsigned char *bytes,
    unsigned int length,
    unsigned short *frame_index,
    unsigned short *frame_count
);

#endif
