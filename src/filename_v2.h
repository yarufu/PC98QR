#ifndef PC98QR_FILENAME_V2_H
#define PC98QR_FILENAME_V2_H

#define PC98QR_FILENAME_V2_MAX 12U

enum pc98qr_filename_v2_result {
    PC98QR_FILENAME_V2_OK = 0,
    PC98QR_FILENAME_V2_ERROR_ARGUMENT,
    PC98QR_FILENAME_V2_ERROR_PATH,
    PC98QR_FILENAME_V2_ERROR_NAME,
    PC98QR_FILENAME_V2_ERROR_RESERVED,
    PC98QR_FILENAME_V2_ERROR_CAPACITY
};

/*
 * パスやNUL終端文字列ではなく、長さ付きの正規化済みメタデータを検証する。
 * 名前1～8文字、任意の拡張子1～3文字、全長1～12バイト。
 * 大文字ASCIIと許可記号だけを認め、小文字・NUL・予約名は拒否する。
 * ポインタ、書式、予約名の順に検査し、書き込みはしない。
 */
/* Validate canonical metadata, NOT a path or a NUL-terminated string.
 * Read exactly length bytes (1..12): base 1..8, optional '.' + extension 1..3.
 * Letters must already be uppercase ASCII. Digits and !#$%&'()-@^_`{}~ are
 * allowed; every other byte is rejected, including embedded NUL and lowercase.
 * Reject base CON/CONIN$/CONOUT$/PRN/AUX/NUL/CLOCK$/COM/LPT/LST/COM0..9/LPT0..9,
 * with or without extension. This includes conservative PC98QR rules.
 * Check pointer, length/grammar/characters, then reserved base. No writes.
 */
int pc98qr_filename_v2_validate(const unsigned char *filename,
                              unsigned int length);

/*
 * CLIで指定したASCIIパスから最後の名前を抽出し、大文字化して長さ付きで返す。
 * 実ディレクトリの名前取得やパス解決はしない。
 * 各要素を8.3形式と予約名規則で検証し、ディレクトリ位置の . と .. だけを例外とする。
 * 元パス・出力・長さ出力は有効かつ非重複であること。
 * 切詰めや代替名はなく、失敗時は出力を変更しない。
 * 実際の存在確認とDOSのパス解釈は呼出し側の責務。
 */
/* Extract from CLI spelling, not a DOS directory entry. No file I/O.
 * path is a valid NUL-terminated near string, entirely ASCII. Grammar:
 *   optional [A-Za-z]:, optional single leading '\\', then nonempty components
 *   separated by one '\\'. No '/' or UNC paths, repeated/trailing separators,
 *   misplaced colons, wildcard, whitespace, non-ASCII or other illegal bytes.
 * Ordinary directory components obey the same 8.3/reserved-name rules as the
 * final filename. '.' and '..' are allowed ONLY as directory components; they
 * are not resolved. Relative, rooted, drive-relative and drive-rooted paths
 * remain distinct; existence, current directories and DOS resolution are the
 * caller's responsibility. No arbitrary total path limit is imposed here.
 *
 * Success copies only the final filename, with a..z converted to A..Z, and
 * writes its byte length. No NUL terminator, padding, truncation or fallback.
 * path is never modified. Caller supplies out_capacity writable bytes; path,
 * out and length_out must be valid, disjoint objects throughout the call.
 * Check NULL arguments first, then path/components and final filename, then
 * output capacity. Invalid directories give ERROR_PATH; invalid final name
 * gives ERROR_NAME/RESERVED. Empty final component gives ERROR_PATH.
 * Every error leaves out AND length_out untouched. No heap, static workspace,
 * recursion, floating point, locale functions or retained pointers.
 */
int pc98qr_filename_v2_from_path(const char *path,
                               unsigned char *out, unsigned int out_capacity,
                               unsigned int *length_out);

#endif
