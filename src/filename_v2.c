/*
 * 転送プロトコル version 02 で使うファイル名の検証と抽出を担当する。
 * コマンドラインのパスを構成要素ごとに検証し、最後の名前を大文字 ASCII の
 * 8.3 形式で取り出す。ディレクトリ位置の . と .. は許可するが、解決はしない。
 * デバイス名等との衝突を避けるため、予約名は拡張子の有無にかかわらず拒否する。
 * 送信用の名前だけを大文字化し、元のパスは変更しない。実際のファイルを開く際は
 * 呼出し側が元のパスを使う。ディレクトリエントリから実名を取得する処理ではない。
 * 文字コード変換・切詰め・代替名への置換は行わず、不正な入力はエラーにする。
 * 出力は長さ付きのバイト列で NUL 終端を付けず、検証成功後にだけ更新する。
 */

#include "filename_v2.h"

#include <limits.h>
#include <stddef.h>

#if CHAR_BIT != 8 || UCHAR_MAX != 255
#error "PC98QR filenames require 8-bit bytes"
#endif

static unsigned char ascii_upper(unsigned char c)
{
    return c >= 'a' && c <= 'z' ? (unsigned char)(c - 'a' + 'A') : c;
}

static int name_character(unsigned char c)
{
    const char *special = "!#$%&'()-@^_`{}~";
    if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))
        return 1;
    while (*special != '\0') {
        if (c == (unsigned char)*special++)
            return 1;
    }
    return 0;
}

static int base_equals(const unsigned char *name, unsigned int length,
                       const char *reserved)
{
    unsigned int i;
    for (i = 0; i < length; ++i) {
        if (reserved[i] == '\0' || ascii_upper(name[i]) != (unsigned char)reserved[i])
            return 0;
    }
    return reserved[length] == '\0';
}

static int reserved_base(const unsigned char *name, unsigned int length)
{
    if (base_equals(name, length, "CON") || base_equals(name, length, "PRN") ||
        base_equals(name, length, "CONIN$") || base_equals(name, length, "CONOUT$") ||
        base_equals(name, length, "AUX") || base_equals(name, length, "NUL") ||
        base_equals(name, length, "CLOCK$") || base_equals(name, length, "COM") ||
        base_equals(name, length, "LPT") || base_equals(name, length, "LST"))
        return 1;
    return length == 4 && name[3] >= '0' && name[3] <= '9' &&
           (base_equals(name, 3, "COM") || base_equals(name, 3, "LPT"));
}

static int validate_name(const unsigned char *name, unsigned int length,
                         int allow_lowercase)
{
    unsigned int i, base_length = length, dot = length;
    if (length == 0 || length > PC98QR_FILENAME_V2_MAX)
        return PC98QR_FILENAME_V2_ERROR_NAME;
    for (i = 0; i < length; ++i) {
        unsigned char c = name[i];
        if (c == '.') {
            if (dot != length)
                return PC98QR_FILENAME_V2_ERROR_NAME;
            dot = i;
            base_length = i;
        } else if (!name_character(allow_lowercase ? ascii_upper(c) : c)) {
            return PC98QR_FILENAME_V2_ERROR_NAME;
        }
    }
    if (base_length == 0 || base_length > 8 ||
        (dot != length && (length - dot - 1 == 0 || length - dot - 1 > 3)))
        return PC98QR_FILENAME_V2_ERROR_NAME;
    if (reserved_base(name, base_length))
        return PC98QR_FILENAME_V2_ERROR_RESERVED;
    return PC98QR_FILENAME_V2_OK;
}

int pc98qr_filename_v2_validate(const unsigned char *filename,
                              unsigned int length)
{
    if (filename == NULL)
        return PC98QR_FILENAME_V2_ERROR_ARGUMENT;
    return validate_name(filename, length, 0);
}

int pc98qr_filename_v2_from_path(const char *path,
                               unsigned char *out, unsigned int out_capacity,
                               unsigned int *length_out)
{
    const unsigned char *cursor = (const unsigned char *)path;
    if (path == NULL || out == NULL || length_out == NULL)
        return PC98QR_FILENAME_V2_ERROR_ARGUMENT;
    if (ascii_upper(cursor[0]) >= 'A' && ascii_upper(cursor[0]) <= 'Z' &&
        cursor[1] == ':')
        cursor += 2;
    if (*cursor == '\\')
        ++cursor;
    for (;;) {
        const unsigned char *component = cursor;
        unsigned int length = 0, i;
        int result;
        while (*cursor != '\0' && *cursor != '\\') {
            /*
             * 長いnear文字列でも16ビットの長さカウンタが周回しないよう、上限で飽和させる。
             */
            /* Saturate: even a long near string cannot wrap a 16-bit counter. */
            if (length <= PC98QR_FILENAME_V2_MAX)
                ++length;
            ++cursor;
        }
        if (length == 0)
            return PC98QR_FILENAME_V2_ERROR_PATH;
        if (*cursor == '\\') {
            int navigation = component[0] == '.' &&
                             (length == 1 || (length == 2 && component[1] == '.'));
            if (!navigation && validate_name(component, length, 1) != PC98QR_FILENAME_V2_OK)
                return PC98QR_FILENAME_V2_ERROR_PATH;
            ++cursor;
            continue;
        }
        result = validate_name(component, length, 1);
        if (result != PC98QR_FILENAME_V2_OK)
            return result;
        if (out_capacity < length)
            return PC98QR_FILENAME_V2_ERROR_CAPACITY;
        for (i = 0; i < length; ++i)
            out[i] = ascii_upper(component[i]);
        *length_out = length;
        return PC98QR_FILENAME_V2_OK;
    }
}
