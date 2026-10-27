/*
 * PC98QR の QR 符号化処理。V1-Q、V2-Q、V2-M、V25-L、V40-L に対応する。
 * 入力からデータ符号語を作り、Reed–Solomon 誤り訂正を付加し、機能パターンと
 * データを配置した後、8 種類のマスクを評価して最も低いペナルティのものを選ぶ。
 * V1/V2 はセルごとのバイト配列、V25/V40 はビット詰め配列を出力する。
 * 大きな QR ではブロック分割とインターリーブを行い、整数演算は ia16 の
 * 16 ビット int での桁あふれを避ける。静的作業領域を使うため再入可能ではない。
 * ファイル入出力や画面描画は担当せず、出力には Quiet Zone を含めない。
 */

/*
 * V1-Q・V2-Q・V2-M・V25-L・V40-Lに限定した独立実装。
 * 演算と配置は定義から実装し、外部符号化器はホストの比較試験だけで使う。
 */
/* Original PC98QR implementation, restricted to V1-Q, V2-Q, V2-M, V25-L and V40-L.
 * QR arithmetic and layout are implemented from their definitions.
 * External encoders are used only by host comparison tests.
 */
#include "qr_encode.h"

#include <string.h>

#define V1_DATA_BYTES 13
#define V1_ECC_BYTES 13
#define V2Q_DATA_BYTES 22
#define V2Q_ECC_BYTES 22
#define V2M_DATA_BYTES 28
#define V2M_ECC_BYTES 16
#define MAX_TOTAL_BYTES (V2Q_DATA_BYTES + V2Q_ECC_BYTES) /* Both V2 profiles: 44. */ /* V2の両プロファイルは合計44符号語。 */
#define ECC_FORMAT_Q 3u /* 11 */
#define ECC_FORMAT_M 0u /* 00 */

static int alnum_value(unsigned char character)
{
    static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";
    unsigned int i;

    for (i = 0; i < sizeof(alphabet) - 1; ++i) {
        if (character == (unsigned char)alphabet[i])
            return (int)i;
    }
    return -1;
}

static int validate_text(const char *text, unsigned int *length)
{
    unsigned int count;

    /*
     * 入力が長くても走査は最大17回で打ち切る。
     */
    /* Bounded scan: even a very long argument takes at most 17 reads here. */
    for (count = 0; count <= QR_V1Q_ALNUM_MAX; ++count) {
        if (text[count] == '\0')
            break;
    }
    if (count == 0 || count > QR_V1Q_ALNUM_MAX)
        return QR_ERROR_LENGTH;
    *length = count;
    for (count = 0; count < *length; ++count) {
        if (alnum_value((unsigned char)text[count]) < 0)
            return QR_ERROR_CHARACTER;
    }
    return QR_OK;
}

/*
 * 最大フィールドはV25/V40の16ビットByte数なので、V30のunsigned intで保持できる。
 */
/* Fields are at most 16 bits (V25/V40 Byte count); unsigned int suffices on V30. */
static void append_bits(unsigned char *data, unsigned int *used,
                        unsigned int value, unsigned int width)
{
    while (width != 0) {
        --width;
        data[*used / 8u] |= (unsigned char)(((value >> width) & 1u)
                                          << (7u - *used % 8u));
        ++*used;
    }
}

static void finish_data(unsigned char *data, unsigned int used,
                         unsigned int data_bytes)
{
    unsigned int remaining = data_bytes * 8u - used;
    unsigned int pad = 0;
    unsigned int i;

    append_bits(data, &used, 0, remaining < 4u ? remaining : 4u);
    append_bits(data, &used, 0, (8u - used % 8u) % 8u);
    for (i = used / 8u; i < data_bytes; ++i)
        data[i] = (unsigned char)((pad++ % 2u == 0) ? 0xEC : 0x11);
}

/*
 * 入力検証後だけ呼び出す。
 * 最大ペイロード時は104ビット中101ビットを使う。
 */
/* Called only after validation. The largest payload occupies 101 of 104 bits. */
static void build_data(const char *text, unsigned int length,
                       unsigned char *data)
{
    unsigned int used = 0;
    unsigned int i;

    memset(data, 0, V1_DATA_BYTES);
    append_bits(data, &used, 2, 4); /* Alphanumeric mode 0010. */ /* 英数字モードの識別値0010。 */
    append_bits(data, &used, length, 9);
    for (i = 0; i + 1 < length; i += 2) {
        unsigned int pair = (unsigned int)alnum_value((unsigned char)text[i]) * 45u
                         + (unsigned int)alnum_value((unsigned char)text[i + 1]);
        append_bits(data, &used, pair, 11);
    }
    if (i < length)
        append_bits(data, &used, (unsigned int)alnum_value((unsigned char)text[i]), 6);

    finish_data(data, used, V1_DATA_BYTES);
}

/*
 * Byte入力の最大ビット数はV1-Qが104、V2-Qが176、V2-Mが224。
 * モード4・個数8・データ8×長さ・終端4ビットを含み、ECIは付けない。
 */
/* Maximum Byte payload: V1-Q 104, V2-Q 176, V2-M 224 bits, including
 * 4 mode + 8 count + 8*length payload + 4 terminator bits, without ECI.
 */
static void build_byte_data(const unsigned char *payload, unsigned int length,
                            unsigned char *data, unsigned int data_bytes)
{
    unsigned int used = 0;
    unsigned int i;

    memset(data, 0, data_bytes);
    append_bits(data, &used, 4, 4); /* Byte mode 0100, no ECI. */ /* Byteモードの識別値0100。ECIは付けない。 */
    append_bits(data, &used, length, 8); /* Count bytes, not characters. */ /* 文字数ではなくバイト数を格納する。 */
    for (i = 0; i < length; ++i)
        append_bits(data, &used, payload[i], 8);
    finish_data(data, used, data_bytes);
}

/*
 * GF(256)/0x11D上でα=2、i=0～12の積から生成多項式を導く。
 * 係数は降べき順で先頭の1を省略する。
 * 外部表のコピーではなく数学的に導出し、ホストテストで照合する。
 * 本番では対数・指数表を使わない。
 */
/* g(x) = product (x + alpha^i), i=0..12, alpha=2 in GF(256)/0x11D.
 * Descending coefficients x^12..x^0; the leading x^13 coefficient is 1.
 * Derived mathematically and checked in tests/test_qr_encode.py, not copied
 * from an encoder's table. No log/exp tables in production.
 */
static const unsigned char rs_generator_v1[V1_ECC_BYTES] = {
    0x89, 0x49, 0xE3, 0x11, 0xB1, 0x11, 0x34,
    0x0D, 0x2E, 0x2B, 0x53, 0x84, 0x78
};

/*
 * i=0～21の同じ積。
 * xの21次から0次の係数を並べ、22次の1は省略する。
 * 独立に導出し、ホストで全22根を確認する。
 */
/* Same product for i=0..21; descending x^21..x^0, monic x^22 omitted.
 * Independently derived and checked at all 22 roots in host tests.
 */
static const unsigned char rs_generator_v2q[V2Q_ECC_BYTES] = {
    0x59, 0xB3, 0x83, 0xB0, 0xB6, 0xF4, 0x13, 0xBD,
    0x45, 0x28, 0x1C, 0x89, 0x1D, 0x7B, 0x43, 0xFD,
    0x56, 0xDA, 0xE6, 0x1A, 0x91, 0xF5
};

/*
 * V2-MはGF(256)/0x11D上のi=0～15の積。
 * 16次の1を省略した降べき順係数を独立に求め、ホストで根とシンドロームを検査する。
 */
/* V2-M: product (x + alpha^i), i=0..15 in GF(256)/0x11D.
 * Descending x^15..x^0, monic x^16 omitted. Independently derived by
 * polynomial multiplication; all roots and syndromes checked in host tests.
 */
static const unsigned char rs_generator_v2m[V2M_ECC_BYTES] = {
    0x3B, 0x0D, 0x68, 0xBD, 0x44, 0xD1, 0x1E, 0x08,
    0xA3, 0x41, 0x29, 0xE5, 0x62, 0x32, 0x24, 0x3B
};

static unsigned char gf_multiply(unsigned char left, unsigned char right)
{
    unsigned int a = left;
    unsigned int b = right;
    unsigned int product = 0;

    while (b != 0) {
        if (b & 1u)
            product ^= a;
        b >>= 1;
        a <<= 1;
        if (a & 0x100u)
            a ^= 0x11Du;
    }
    return (unsigned char)product;
}

static void build_ecc(const unsigned char *data, unsigned char *ecc,
                      unsigned int data_bytes, unsigned int ecc_bytes,
                      const unsigned char *generator)
{
    /*
     * data(x)にxのECC長乗を掛け、先頭係数1の生成多項式で筆算除算する。
     * 小さな除算バッファもDOSスタックに置かず静的に持つ。
     */
    /* Polynomial long division of data(x)*x^ecc_bytes by monic g(x).
     * Static to keep even the small division buffer off the DOS stack.
     */
    static unsigned char dividend[MAX_TOTAL_BYTES];
    unsigned int i;
    unsigned int j;

    memcpy(dividend, data, data_bytes);
    memset(dividend + data_bytes, 0, ecc_bytes);
    for (i = 0; i < data_bytes; ++i) {
        unsigned char leading = dividend[i];
        dividend[i] = 0;
        for (j = 0; j < ecc_bytes; ++j)
            dividend[i + j + 1] ^= gf_multiply(leading, generator[j]);
    }
    memcpy(ecc, dividend + data_bytes, ecc_bytes);
}

/*
 * 内部ヘルパーは21または25の行を連続配置した行列だけを扱う。
 * 公開APIでサイズと個数を固定し、任意Version用APIは持たない。
 */
/* Private matrix helpers accept only sizes 21 and 25, with packed rows.
 * The public APIs fix every size/count; there is no general-version API.
 */
static unsigned char reserved[QR_V2_SIZE * QR_V2_SIZE];

static void function_cell(unsigned int size, unsigned char *matrix,
                          unsigned int y, unsigned int x, unsigned char value)
{
    reserved[y * size + x] = 1;
    matrix[y * size + x] = value;
}

static void finder(unsigned int size, unsigned char *matrix, int y0, int x0)
{
    int dy;
    int dx;

    /*
     * 周囲の白い輪をセパレーターとし、シンボル端で切り取る。
     */
    /* The surrounding white ring supplies the separator; clip at symbol edge. */
    for (dy = -1; dy <= 7; ++dy) {
        for (dx = -1; dx <= 7; ++dx) {
            int y = y0 + dy;
            int x = x0 + dx;
            unsigned char black = 0;
            if (y < 0 || y >= (int)size || x < 0 || x >= (int)size)
                continue;
            if (dy >= 0 && dy <= 6 && dx >= 0 && dx <= 6) {
                black = (unsigned char)(dy == 0 || dy == 6 || dx == 0 || dx == 6
                                     || (dy >= 2 && dy <= 4 && dx >= 2 && dx <= 4));
            }
            function_cell(size, matrix, (unsigned int)y, (unsigned int)x, black);
        }
    }
}

static unsigned int format_bits(unsigned int mask, unsigned int ecc_format_bits)
{
    unsigned int data = (ecc_format_bits << 3) | mask;
    unsigned int remainder = data << 10;
    int degree;

    for (degree = 14; degree >= 10; --degree) {
        if (remainder & (1u << degree))
            remainder ^= 0x537u << (degree - 10);
    }
    return ((data << 10) | remainder) ^ 0x5412u;
}

static void write_format(unsigned int size, unsigned char *matrix,
                         unsigned int mask, unsigned int ecc_format_bits)
{
    /*
     * V1の形式情報を下位ビットから配置し、タイミング列を避ける。
     * V2では遠端の複製だけを4モジュールずらす。
     */
    /* V1 format positions, least significant bit first, with timing skipped.
     * Shift only the far-edge copies by 4 modules for V2.
     */
    static const unsigned char vertical_y[15] = {
        0, 1, 2, 3, 4, 5, 7, 8, 14, 15, 16, 17, 18, 19, 20
    };
    static const unsigned char horizontal_x[15] = {
        20, 19, 18, 17, 16, 15, 14, 13, 7, 5, 4, 3, 2, 1, 0
    };
    unsigned int word = format_bits(mask, ecc_format_bits);
    unsigned int i;

    for (i = 0; i < 15; ++i) {
        unsigned char bit = (unsigned char)((word >> i) & 1u);
        function_cell(size, matrix, vertical_y[i] + (i >= 8 ? size - QR_V1_SIZE : 0),
                      8, bit);
        function_cell(size, matrix, 8,
                      horizontal_x[i] + (i < 8 ? size - QR_V1_SIZE : 0), bit);
    }
}

static void build_functions(unsigned int size, unsigned char *matrix,
                            unsigned int ecc_format_bits)
{
    unsigned int i;

    memset(matrix, 0, size * size);
    memset(reserved, 0, sizeof(reserved));
    finder(size, matrix, 0, 0);
    finder(size, matrix, 0, (int)size - 7);
    finder(size, matrix, (int)size - 7, 0);
    for (i = 8; i < size - 8u; ++i) {
        unsigned char black = (unsigned char)(i % 2u == 0);
        function_cell(size, matrix, 6, i, black);
        function_cell(size, matrix, i, 6, black);
    }
    if (size == QR_V2_SIZE) {
        unsigned int y;
        unsigned int x;
        /*
         * 3つの検出パターンとの重なりを除くと中心(18,18)だけが残る。
         * 5×5の外周黒・内周白・中心黒で構成する。
         */
        /* Only (18,18) survives overlap with the three finder/separator areas.
         * 5x5: black outer ring, white inner ring, black center.
         */
        for (y = 16; y <= 20; ++y)
            for (x = 16; x <= 20; ++x)
                function_cell(size, matrix, y, x,
                              (unsigned char)(y == 16 || y == 20 || x == 16
                                              || x == 20 || (y == 18 && x == 18)));
    }
    function_cell(size, matrix, size - 8u, 8, 1); /* Fixed dark module (x=8, y=4*version+9). */ /* 固定黒モジュールの座標はx=8、y=4×version+9。 */

    /*
     * 形式情報の両領域を予約する。
     * 各候補は採点前に自身の実際の形式情報で置き換える。
     */
    /* Establish both format reservations. Each candidate replaces these bits
     * with its own real format before scoring the completed symbol.
     */
    write_format(size, matrix, 0, ecc_format_bits);
}

/*
 * row=y、col=x。
 * マスク対象はデータ・ECC・余りのセルだけ。
 */
/* Coordinates: row=y, col=x. Only data/ECC/remainder cells are masked. */
static unsigned int mask_applies(unsigned int mask, unsigned int row,
                                 unsigned int col)
{
    unsigned int product = row * col; /* At most 30976, including V40. */ /* V40を含めても積は最大30976。 */
    switch (mask) {
    case 0: return (row + col) % 2u == 0;
    case 1: return row % 2u == 0;
    case 2: return col % 3u == 0;
    case 3: return (row + col) % 3u == 0;
    case 4: return (row / 2u + col / 3u) % 2u == 0;
    case 5: return product % 2u + product % 3u == 0;
    case 6: return (product % 2u + product % 3u) % 2u == 0;
    default: /* Private callers supply mask 0..7 only. */ /* 内部呼出しのマスク番号は0～7だけ。 */
        return ((row + col) % 2u + product % 3u) % 2u == 0;
    }
}

static void place_codewords(unsigned int size, unsigned int total_bytes,
                            const unsigned char *codewords,
                            unsigned char *matrix,
                            unsigned int mask)
{
    int right;
    unsigned int upwards = 1;
    unsigned int used = 0;
    unsigned int row;
    unsigned int offset;

    /*
     * 右から左へ2列ずつ進み、列6のタイミング線を飛ばす。
     * V1は208セル、V2は352データビットと7余りビット。
     * 余りにもマスクを掛けるが符号語の範囲外は読まない。
     */
    /* Column pairs right to left; skip the vertical timing line at column 6.
     * V1 has 208 cells (26 words); V2 has 359 (44 words + 7 zero remainder
     * bits). Remainder cells receive the mask, but never read past codewords.
     */
    for (right = (int)size - 1; right > 0; right -= 2) {
        if (right == 6)
            --right;
        for (row = 0; row < size; ++row) {
            unsigned int y = upwards ? size - 1u - row : row;
            for (offset = 0; offset < 2; ++offset) {
                unsigned int x = (unsigned int)right - offset;
                unsigned char bit;
                if (reserved[y * size + x])
                    continue;
                bit = 0;
                if (used < total_bytes * 8u)
                    bit = (unsigned char)((codewords[used / 8u] >> (7u - used % 8u)) & 1u);
                matrix[y * size + x] = (unsigned char)(bit ^ mask_applies(mask, y, x));
                ++used;
            }
        }
        upwards ^= 1u;
    }
}

static void place_candidate(unsigned int size, unsigned int total_bytes,
                             const unsigned char *codewords,
                             unsigned char *matrix,
                             unsigned int mask, unsigned int ecc_format_bits)
{
    /*
     * 元のマスク前ビットから、データ・ECC・余りセルをすべて上書きする。
     */
    /* Overwrite all data/ECC/remainder cells from the original unmasked bits. */
    place_codewords(size, total_bytes, codewords, matrix, mask);
    write_format(size, matrix, mask, ecc_format_bits);
}

struct penalties {
    unsigned int n1, n2, n3, n4;
};

static unsigned int run_penalty(unsigned int length)
{
    return length < 5u ? 0u : length - 2u;
}

static void score_line(unsigned int size, const unsigned char *matrix,
                       unsigned int index, unsigned int vertical,
                       struct penalties *score)
{
    /*
     * 最大25ランを行・列処理で共有し、スタックには置かない。
     * これはPC98QRで採用するN3契約であり、ISO/JISの唯一の解釈と主張するものではない。
     */
    /* At most 25 complete runs, shared by row/column calls, never on stack.
     * This is PC98QR's N3 contract, not a claim of a unique ISO/JIS reading.
     */
    static unsigned char runs[QR_V2_SIZE];
    unsigned int count = 1;
    unsigned int i;
    unsigned char first = vertical ? matrix[index] : matrix[index * size];
    unsigned char previous = first;

    runs[0] = 1;
    for (i = 1; i < size; ++i) {
        unsigned char cell = vertical ? matrix[i * size + index] : matrix[index * size + i];
        if (cell == previous) {
            ++runs[count - 1];
        } else {
            runs[count++] = 1;
            previous = cell;
        }
    }
    /*
     * シンボル端に達する場合も最後のランを数える。
     */
    /* Includes the final run, even when it reaches the symbol edge. */
    for (i = 0; i < count; ++i)
        score->n1 += run_penalty(runs[i]);

    for (i = 0; i + 4u < count; ++i) {
        unsigned int k = runs[i];
        unsigned int left;
        unsigned int right;
        if ((first ^ (i % 2u)) == 0 || runs[i + 1] != k
            || runs[i + 2] != 3u * k || runs[i + 3] != k || runs[i + 4] != k)
            continue;
        /*
         * 外側の白ランが存在しないか端に達する場合、N3評価では4k以上と仮定する。
         * 行列やN1/N2/N4は変えない。
         */
        /* A missing outer light run, or one reaching the edge, is virtually
         * long enough for 4k. This affects N3 only, not the matrix or N1/2/4.
         */
        left = i <= 1u ? 4u * k : runs[i - 1];
        right = i + 6u >= count ? 4u * k : runs[i + 5];
        if (left >= 4u * k && right >= k)
            score->n3 += 40u;
        if (right >= 4u * k && left >= k)
            score->n3 += 40u;
        /*
         * 共有されるランも飛ばさず、異なる中心をすべて検査する。
         */
        /* Do not skip shared runs: every distinct center is examined. */
    }
}

static unsigned int balance_penalty(unsigned int size, unsigned int dark)
{
    unsigned int total = size * size;
    unsigned int twice = 2u * dark;
    unsigned int a = twice > total ? twice - total : total - twice;

    return a == 0 ? 0u : 10u * ((10u * a - 1u) / total);
}

static unsigned int score_symbol(unsigned int size, const unsigned char *matrix,
                                 struct penalties *score)
{
    unsigned int row;
    unsigned int col;
    unsigned int dark = 0;

    memset(score, 0, sizeof(*score));
    for (row = 0; row < size; ++row) {
        score_line(size, matrix, row, 0, score);
        score_line(size, matrix, row, 1, score);
        for (col = 0; col < size; ++col) {
            unsigned char cell = matrix[row * size + col];
            dark += cell;
            if (row + 1u < size && col + 1u < size
                && cell == matrix[row * size + col + 1]
                && cell == matrix[(row + 1) * size + col]
                && cell == matrix[(row + 1) * size + col + 1])
                score->n2 += 3u;
        }
    }
    score->n4 = balance_penalty(size, dark);
    /*
     * V2の保守的な上限でも合計点は65536未満、N4の中間値は6250以下。
     * longや浮動小数点を必要としない。
     */
    /* Conservative V2 bounds: N1 <= 1150, N2 <= 1728, N4 <= 100.
     * N3 has at most 13 dark centers per line, 80 each: 52000;
     * the total is < 65536. N4 intermediate <= 6250.
     * No long or floating point needed.
     */
    return score->n1 + score->n2 + score->n3 + score->n4;
}

/*
 * 二つの単一セグメント入力処理で、作業領域と符号化後段を共有する。
 */
/* One shared workspace and backend for both single-segment frontends. */
static unsigned char codewords[MAX_TOTAL_BYTES];

static void encode_codewords(unsigned int size, unsigned int data_bytes,
                              unsigned int ecc_bytes,
                              const unsigned char *generator, unsigned char *matrix,
                              unsigned int ecc_format_bits)
{
    unsigned int mask;
    unsigned int best_mask = 0;
    unsigned int best_score = 0;
    struct penalties components;
    unsigned int total_bytes = data_bytes + ecc_bytes;
    build_ecc(codewords, codewords + data_bytes, data_bytes, ecc_bytes, generator);
    build_functions(size, matrix, ecc_format_bits);
    for (mask = 0; mask < 8; ++mask) {
        unsigned int score;
        place_candidate(size, total_bytes, codewords, matrix, mask, ecc_format_bits);
        score = score_symbol(size, matrix, &components);
        if (mask == 0 || score < best_score) {
            best_mask = mask;
            best_score = score;
        }
    }
    place_candidate(size, total_bytes, codewords, matrix, best_mask, ecc_format_bits);
}

int qr_encode_v1q_alnum(const char *text,
                      unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    unsigned int length;
    int result = validate_text(text, &length);

    if (result != QR_OK)
        return result;
    build_data(text, length, codewords);
    encode_codewords(QR_V1_SIZE, V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1,
                     (unsigned char *)matrix, ECC_FORMAT_Q);
    return QR_OK;
}

int qr_encode_v1q_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    if (length == 0 || length > QR_V1Q_BYTE_MAX)
        return QR_ERROR_LENGTH;
    build_byte_data(data, length, codewords, V1_DATA_BYTES);
    encode_codewords(QR_V1_SIZE, V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1,
                     (unsigned char *)matrix, ECC_FORMAT_Q);
    return QR_OK;
}

int qr_encode_v2q_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE])
{
    if (length == 0 || length > QR_V2Q_BYTE_MAX)
        return QR_ERROR_LENGTH;
    build_byte_data(data, length, codewords, V2Q_DATA_BYTES);
    encode_codewords(QR_V2_SIZE, V2Q_DATA_BYTES, V2Q_ECC_BYTES, rs_generator_v2q,
                     (unsigned char *)matrix, ECC_FORMAT_Q);
    return QR_OK;
}

int qr_encode_v2m_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE])
{
    if (length == 0 || length > QR_V2M_BYTE_MAX)
        return QR_ERROR_LENGTH;
    build_byte_data(data, length, codewords, V2M_DATA_BYTES);
    encode_codewords(QR_V2_SIZE, V2M_DATA_BYTES, V2M_ECC_BYTES, rs_generator_v2m,
                     (unsigned char *)matrix, ECC_FORMAT_M);
    return QR_OK;
}

/*
 * V25-L専用処理。
 * 形状とRSブロック構成は固定で、汎用Version/ECC処理ではない。
 * V1/V2のバイト行列とは分離する。
 */
/* Fixed V25-L path. All geometry and RS group sizes are profile constants,
 * not a general Version/ECC backend. V1/V2 byte matrices remain separate.
 */
#define V25_DATA_BYTES 1276
#define V25_TOTAL_BYTES 1588
#define V25_BLOCKS 12
#define V25_ECC_BYTES 26
#define V25_SHORT_DATA 106
#define V25_LONG_DATA 107
#define ECC_FORMAT_L 1u /* 01 */

static unsigned char v25_data[V25_DATA_BYTES];
static unsigned char v25_interleaved[V25_TOTAL_BYTES];
static unsigned char v25_rs[V25_LONG_DATA + V25_ECC_BYTES];
static unsigned char v25_matrix[QR_V25_PACKED_BYTES];
static unsigned char v25_reserved[QR_V25_PACKED_BYTES];
static unsigned char v25_runs[QR_V25_SIZE];

/*
 * GF(256)/0x11D上でi=0～25の積を独立に計算し、26次の1を省略する。
 * 外部表は使わず、ホスト側で係数・全根・シンドロームを検査する。
 */
/* Product (x + 2^i), i=0..25, in GF(256)/0x11D; monic x^26 omitted.
 * Independently derived by polynomial multiplication, not an encoder table.
 * tests/qr_v25_oracle.py checks coefficients, every root and syndromes.
 */
static const unsigned char rs_generator_v25[V25_ECC_BYTES] = {
    0xF6, 0x33, 0xB7, 0x04, 0x88, 0x62, 0xC7, 0x98,
    0x4D, 0x38, 0xCE, 0x18, 0x91, 0x28, 0xD1, 0x75,
    0xE9, 0x2A, 0x87, 0x44, 0x46, 0x90, 0x92, 0x4D,
    0x2B, 0x5E
};

static unsigned char v25_get(const unsigned char *bits, unsigned int index)
{
    return (unsigned char)((bits[index >> 3] >> (7u - (index & 7u))) & 1u);
}

static void v25_set(unsigned char *bits, unsigned int index)
{
    bits[index >> 3] |= (unsigned char)(0x80u >> (index & 7u));
}

static void v25_clear(unsigned char *bits, unsigned int index)
{
    bits[index >> 3] &= (unsigned char)~(0x80u >> (index & 7u));
}

static void v25_put(unsigned char *bits, unsigned int index, unsigned char value)
{
    if (value)
        v25_set(bits, index);
    else
        v25_clear(bits, index);
}

static void v25_build_data(const unsigned char *payload, unsigned int length,
                           unsigned char *data)
{
    unsigned int used = 0;
    unsigned int i;
    memset(data, 0, V25_DATA_BYTES);
    append_bits(data, &used, 4, 4);
    append_bits(data, &used, length, 16);
    for (i = 0; i < length; ++i)
        append_bits(data, &used, payload[i], 8);
    /*
     * 1273バイトでは合計10208ビットとなり、整列やパディングの追加は不要。
     */
    /* At length 1273: 4+16+10184+4 = 10208; no alignment or pad bytes. */
    finish_data(data, used, V25_DATA_BYTES);
}

static unsigned int v25_block_length(unsigned int block)
{
    return block < 8u ? V25_SHORT_DATA : V25_LONG_DATA;
}

static unsigned int v25_block_offset(unsigned int block)
{
    return block * V25_SHORT_DATA + (block < 8u ? 0u : block - 8u);
}

static void v25_build_block_ecc(const unsigned char *data, unsigned int length)
{
    unsigned int i;
    unsigned int j;
    /*
     * 最大133バイトの1ブロック分の除算領域を12ブロックで再利用する。
     */
    /* Only one block dividend, at most 133 bytes, reused for all 12 blocks. */
    memcpy(v25_rs, data, length);
    memset(v25_rs + length, 0, V25_ECC_BYTES);
    for (i = 0; i < length; ++i) {
        unsigned char leading = v25_rs[i];
        v25_rs[i] = 0;
        for (j = 0; j < V25_ECC_BYTES; ++j)
            v25_rs[i + j + 1] ^= gf_multiply(leading, rs_generator_v25[j]);
    }
}

static void v25_interleave(const unsigned char *data, unsigned char *output)
{
    unsigned int block;
    unsigned int j;
    for (block = 0; block < V25_BLOCKS; ++block) {
        unsigned int length = v25_block_length(block);
        const unsigned char *part = data + v25_block_offset(block);
        for (j = 0; j < V25_SHORT_DATA; ++j)
            output[j * V25_BLOCKS + block] = part[j];
        /*
         * 106列分の後に、長いブロックの追加4バイトを並べる。
         */
        /* Four long-block extra bytes follow all 106 full columns. */
        if (block >= 8u)
            output[V25_SHORT_DATA * V25_BLOCKS + block - 8u] = part[106];
        v25_build_block_ecc(part, length);
        for (j = 0; j < V25_ECC_BYTES; ++j)
            output[V25_DATA_BYTES + j * V25_BLOCKS + block] = v25_rs[length + j];
    }
}

static void v25_function(unsigned char *matrix, unsigned int y, unsigned int x,
                         unsigned char value)
{
    unsigned int index = y * QR_V25_SIZE + x;
    v25_set(v25_reserved, index);
    v25_put(matrix, index, value);
}

static void v25_finder(unsigned char *matrix, int y0, int x0)
{
    int dy;
    int dx;
    for (dy = -1; dy <= 7; ++dy) {
        for (dx = -1; dx <= 7; ++dx) {
            int y = y0 + dy;
            int x = x0 + dx;
            unsigned char black = 0;
            if (y < 0 || y >= QR_V25_SIZE || x < 0 || x >= QR_V25_SIZE)
                continue;
            if (dy >= 0 && dy <= 6 && dx >= 0 && dx <= 6)
                black = (unsigned char)(dy == 0 || dy == 6 || dx == 0 || dx == 6
                                  || (dy >= 2 && dy <= 4 && dx >= 2 && dx <= 4));
            v25_function(matrix, (unsigned int)y, (unsigned int)x, black);
        }
    }
}

static unsigned long v25_version_bits(void)
{
    /*
     * 18ビットはia16のunsigned intに収まらない。
     * 指定生成多項式でGF(2)除算し、Version情報には形式情報用のXORを掛けない。
     */
    /* 18 bits cannot fit in ia16 unsigned int. GF(2) division, generator
     * x^12+x^11+x^10+x^9+x^8+x^5+x^2+1. No format XOR on version bits.
     */
    unsigned long remainder = 25UL << 12;
    int degree;
    for (degree = 17; degree >= 12; --degree) {
        if (remainder & (1UL << degree))
            remainder ^= 0x1F25UL << (degree - 12);
    }
    return (25UL << 12) | remainder;
}

static void v25_write_version(unsigned char *matrix)
{
    unsigned long word = v25_version_bits();
    unsigned int i;
    for (i = 0; i < 18; ++i) {
        unsigned int near = i / 3u;
        unsigned int far = QR_V25_SIZE - 11u + i % 3u;
        unsigned char bit = (unsigned char)((word >> i) & 1UL);
        v25_function(matrix, near, far, bit);
        v25_function(matrix, far, near, bit);
    }
}

static void v25_write_format(unsigned char *matrix, unsigned int mask)
{
    unsigned int word = format_bits(mask, ECC_FORMAT_L);
    unsigned int i;
    for (i = 0; i < 15; ++i) {
        unsigned int y = i < 6u ? i : (i < 8u ? i + 1u : QR_V25_SIZE - 15u + i);
        unsigned int x = i < 8u ? QR_V25_SIZE - 1u - i : (i == 8u ? 7u : 14u - i);
        unsigned char bit = (unsigned char)((word >> i) & 1u);
        v25_function(matrix, y, 8, bit);
        v25_function(matrix, 8, x, bit);
    }
}

static void v25_build_functions(unsigned char *matrix)
{
    static const unsigned char centers[5] = {6, 32, 58, 84, 110};
    unsigned int i;
    unsigned int j;
    memset(matrix, 0, QR_V25_PACKED_BYTES);
    memset(v25_reserved, 0, sizeof(v25_reserved));
    v25_finder(matrix, 0, 0);
    v25_finder(matrix, 0, QR_V25_SIZE - 7);
    v25_finder(matrix, QR_V25_SIZE - 7, 0);
    for (i = 8; i < QR_V25_SIZE - 8u; ++i) {
        v25_function(matrix, 6, i, (unsigned char)(i % 2u == 0));
        v25_function(matrix, i, 6, (unsigned char)(i % 2u == 0));
    }
    for (i = 0; i < 5; ++i) {
        for (j = 0; j < 5; ++j) {
            int dy;
            int dx;
            /*
             * 検出パターンと重なる3箇所だけを除外する。
             * 行・列6に残る6中心では位置合わせパターンがタイミングを上書きする。
             */
            /* Exclude ONLY the three finder overlaps. Alignment overwrites
             * timing modules at the six surviving centers on row/column 6.
             */
            if ((i == 0 && (j == 0 || j == 4)) || (i == 4 && j == 0))
                continue;
            for (dy = -2; dy <= 2; ++dy)
                for (dx = -2; dx <= 2; ++dx)
                    v25_function(matrix, (unsigned int)(centers[i] + dy),
                                 (unsigned int)(centers[j] + dx),
                                 (unsigned char)(dy == -2 || dy == 2 || dx == -2
                                          || dx == 2 || (dy == 0 && dx == 0)));
        }
    }
    v25_function(matrix, QR_V25_SIZE - 8u, 8, 1);
    v25_write_format(matrix, 0);
    v25_write_version(matrix); /* Reserve both copies BEFORE data placement. */ /* データ配置前に両方のVersion情報領域を予約する。 */
}

static void v25_place_codewords(const unsigned char *words, unsigned char *matrix,
                                unsigned int mask)
{
    int right;
    unsigned int upwards = 1;
    unsigned int used = 0;
    unsigned int row;
    unsigned int offset;
    for (right = QR_V25_SIZE - 1; right > 0; right -= 2) {
        if (right == 6)
            --right;
        for (row = 0; row < QR_V25_SIZE; ++row) {
            unsigned int y = upwards ? QR_V25_SIZE - 1u - row : row;
            for (offset = 0; offset < 2; ++offset) {
                unsigned int x = (unsigned int)right - offset;
                unsigned int index = y * QR_V25_SIZE + x;
                unsigned char bit = 0;
                if (v25_get(v25_reserved, index))
                    continue;
                if (used < V25_TOTAL_BYTES * 8u)
                    bit = (unsigned char)((words[used >> 3] >> (7u - (used & 7u))) & 1u);
                /*
                 * 4つの余りセルにもマスクを掛けるが、最後の符号語より先は読まない。
                 * 候補ごとに元の符号語から配置する。
                 */
                /* Four remainder cells also receive the mask, without reading
                 * past the final codeword. Every candidate starts from words.
                 */
                v25_put(matrix, index, (unsigned char)(bit ^ mask_applies(mask, y, x)));
                ++used;
            }
        }
        upwards ^= 1u;
    }
}

static void v25_place_candidate(const unsigned char *words, unsigned char *matrix,
                                unsigned int mask)
{
    v25_place_codewords(words, matrix, mask);
    v25_write_format(matrix, mask);
    /*
     * Version情報は機能パターン構築時に完成し、予約済み。
     */
    /* Version Information was completed by build_functions and is reserved. */
}

struct v25_penalties {
    unsigned long n1, n2, n3, n4;
};

static void v25_score_line(const unsigned char *matrix, unsigned int index,
                           unsigned int vertical, struct v25_penalties *score)
{
    unsigned int count = 1;
    unsigned int i;
    unsigned char first = v25_get(matrix, vertical ? index : index * QR_V25_SIZE);
    unsigned char previous = first;
    v25_runs[0] = 1;
    for (i = 1; i < QR_V25_SIZE; ++i) {
        unsigned char cell = v25_get(matrix, vertical ? i * QR_V25_SIZE + index
                                                      : index * QR_V25_SIZE + i);
        if (cell == previous)
            ++v25_runs[count - 1];
        else {
            v25_runs[count++] = 1;
            previous = cell;
        }
    }
    for (i = 0; i < count; ++i)
        score->n1 += run_penalty(v25_runs[i]);
    /*
     * V1/V2と同じPC98QRのN3ラン・仮想白領域の契約を使う。
     */
    /* Same explicit PC98QR N3 run/virtual-light contract as V1/V2. */
    for (i = 0; i + 4u < count; ++i) {
        unsigned int k = v25_runs[i];
        unsigned int left;
        unsigned int right;
        if ((first ^ (i % 2u)) == 0 || v25_runs[i + 1] != k
            || v25_runs[i + 2] != 3u * k || v25_runs[i + 3] != k || v25_runs[i + 4] != k)
            continue;
        left = i <= 1u ? 4u * k : v25_runs[i - 1];
        right = i + 6u >= count ? 4u * k : v25_runs[i + 5];
        if (left >= 4u * k && right >= k)
            score->n3 += 40UL;
        if (right >= 4u * k && left >= k)
            score->n3 += 40UL;
    }
}

static unsigned long v25_balance_penalty(unsigned int dark)
{
    unsigned int total = QR_V25_SIZE * QR_V25_SIZE;
    unsigned int twice = 2u * dark;
    unsigned int deviation = twice > total ? twice - total : total - twice;
    /*
     * 偏差の10倍は136890に達するので、ia16では乗算前に型を広げる。
     */
    /* 10*deviation reaches 136890: promote BEFORE multiplication on ia16. */
    return deviation == 0 ? 0UL : 10UL * ((10UL * deviation - 1UL) / total);
}

static unsigned long v25_score_symbol(const unsigned char *matrix,
                                      struct v25_penalties *score)
{
    unsigned int y;
    unsigned int x;
    unsigned int dark = 0;
    memset(score, 0, sizeof(*score));
    for (y = 0; y < QR_V25_SIZE; ++y) {
        v25_score_line(matrix, y, 0, score);
        v25_score_line(matrix, y, 1, score);
        for (x = 0; x < QR_V25_SIZE; ++x) {
            unsigned int index = y * QR_V25_SIZE + x;
            unsigned char cell = v25_get(matrix, index);
            dark += cell;
            if (y + 1u < QR_V25_SIZE && x + 1u < QR_V25_SIZE
                && cell == v25_get(matrix, index + 1u)
                && cell == v25_get(matrix, index + QR_V25_SIZE)
                && cell == v25_get(matrix, index + QR_V25_SIZE + 1u))
                score->n2 += 3UL;
        }
    }
    score->n4 = v25_balance_penalty(dark);
    /*
     * 一色の行列でも67368点となるため、各成分と合計はia16で32ビットのunsigned longを使う。
     * 座標と添字は16ビットに収まる。
     */
    /* Even a constant matrix scores 67368. All components and their sum use
     * unsigned long (32 bits on ia16). Coordinates/indices stay 16-bit-safe.
     */
    return score->n1 + score->n2 + score->n3 + score->n4;
}

static void v25_encode_codewords(unsigned char *matrix)
{
    unsigned int mask;
    unsigned int best_mask = 0;
    unsigned long best_score = 0;
    struct v25_penalties components;
    v25_interleave(v25_data, v25_interleaved);
    v25_build_functions(matrix);
    for (mask = 0; mask < 8; ++mask) {
        unsigned long score;
        v25_place_candidate(v25_interleaved, matrix, mask);
        score = v25_score_symbol(matrix, &components);
        if (mask == 0 || score < best_score) {
            best_mask = mask;
            best_score = score;
        }
    }
    v25_place_candidate(v25_interleaved, matrix, best_mask);
}

int qr_encode_v25l_bytes(const unsigned char *data, unsigned int length,
                        unsigned char matrix[QR_V25_PACKED_BYTES])
{
    if (length == 0 || length > QR_V25L_BYTE_MAX)
        return QR_ERROR_LENGTH;
    v25_build_data(data, length, v25_data);
    v25_encode_codewords(v25_matrix);
    memcpy(matrix, v25_matrix, QR_V25_PACKED_BYTES);
    return QR_OK;
}

/*
 * V40-L専用の固定形状・RS構成。
 * 汎用Version/ECC処理ではなく、V25は専用領域のまま保持する。
 */
/* Fixed V40-L path. All geometry and RS group sizes are profile constants,
 * not a general Version/ECC backend. V25 stays unchanged with its own storage.
 */
#define V40_DATA_BYTES 2956
#define V40_TOTAL_BYTES 3706
#define V40_BLOCKS 25
#define V40_ECC_BYTES 30
#define V40_SHORT_DATA 118
#define V40_LONG_DATA 119

static unsigned char v40_data[V40_DATA_BYTES];
static unsigned char v40_interleaved[V40_TOTAL_BYTES];
static unsigned char v40_rs[V40_LONG_DATA + V40_ECC_BYTES];
static unsigned char v40_matrix[QR_V40_PACKED_BYTES];
static unsigned char v40_reserved[QR_V40_PACKED_BYTES];
static unsigned char v40_runs[QR_V40_SIZE];

/*
 * GF(256)/0x11D上でi=0～29の積を独立に計算し、30次の1を省略する。
 * ホスト側で係数・全根・シンドロームを検査する。
 */
/* Product (x + 2^i), i=0..29, in GF(256)/0x11D; monic x^30 omitted.
 * Independently derived by polynomial multiplication, not an encoder table.
 * tests/qr_v40_oracle.py checks coefficients, every root and syndromes.
 */
static const unsigned char rs_generator_v40[V40_ECC_BYTES] = {
    0xD4, 0xF6, 0x4D, 0x49, 0xC3, 0xC0, 0x4B, 0x62,
    0x05, 0x46, 0x67, 0xB1, 0x16, 0xD9, 0x8A, 0x33,
    0xB5, 0xF6, 0x48, 0x19, 0x12, 0x2E, 0xE4, 0x4A,
    0xD8, 0xC3, 0x0B, 0x6A, 0x82, 0x96
};

static unsigned char v40_get(const unsigned char *bits, unsigned int index)
{
    return (unsigned char)((bits[index >> 3] >> (7u - (index & 7u))) & 1u);
}

static void v40_set(unsigned char *bits, unsigned int index)
{
    bits[index >> 3] |= (unsigned char)(0x80u >> (index & 7u));
}

static void v40_clear(unsigned char *bits, unsigned int index)
{
    bits[index >> 3] &= (unsigned char)~(0x80u >> (index & 7u));
}

static void v40_put(unsigned char *bits, unsigned int index, unsigned char value)
{
    if (value)
        v40_set(bits, index);
    else
        v40_clear(bits, index);
}

static void v40_build_data(const unsigned char *payload, unsigned int length,
                           unsigned char *data)
{
    unsigned int used = 0;
    unsigned int i;
    memset(data, 0, V40_DATA_BYTES);
    append_bits(data, &used, 4, 4);
    append_bits(data, &used, length, 16);
    for (i = 0; i < length; ++i)
        append_bits(data, &used, payload[i], 8);
    /*
     * 2953バイトでは合計23648ビットとなり、整列やパディングの追加は不要。
     */
    /* At length 2953: 4+16+23624+4 = 23648; no alignment or pad bytes. */
    finish_data(data, used, V40_DATA_BYTES);
}

static unsigned int v40_block_length(unsigned int block)
{
    return block < 19u ? V40_SHORT_DATA : V40_LONG_DATA;
}

static unsigned int v40_block_offset(unsigned int block)
{
    return block * V40_SHORT_DATA + (block < 19u ? 0u : block - 19u);
}

static void v40_build_block_ecc(const unsigned char *data, unsigned int length)
{
    unsigned int i;
    unsigned int j;
    /*
     * 最大149バイトの1ブロック分の除算領域を25ブロックで再利用する。
     */
    /* Only one block dividend, at most 149 bytes, reused for all 25 blocks. */
    memcpy(v40_rs, data, length);
    memset(v40_rs + length, 0, V40_ECC_BYTES);
    for (i = 0; i < length; ++i) {
        unsigned char leading = v40_rs[i];
        v40_rs[i] = 0;
        for (j = 0; j < V40_ECC_BYTES; ++j)
            v40_rs[i + j + 1] ^= gf_multiply(leading, rs_generator_v40[j]);
    }
}

static void v40_interleave(const unsigned char *data, unsigned char *output)
{
    unsigned int block;
    unsigned int j;
    for (block = 0; block < V40_BLOCKS; ++block) {
        unsigned int length = v40_block_length(block);
        const unsigned char *part = data + v40_block_offset(block);
        for (j = 0; j < V40_SHORT_DATA; ++j)
            output[j * V40_BLOCKS + block] = part[j];
        /*
         * 118列分の後に、長いブロックの追加6バイトを並べる。
         */
        /* Six long-block extra bytes follow all 118 full columns. */
        if (block >= 19u)
            output[V40_SHORT_DATA * V40_BLOCKS + block - 19u] = part[V40_SHORT_DATA];
        v40_build_block_ecc(part, length);
        for (j = 0; j < V40_ECC_BYTES; ++j)
            output[V40_DATA_BYTES + j * V40_BLOCKS + block] = v40_rs[length + j];
    }
}

static void v40_function(unsigned char *matrix, unsigned int y, unsigned int x,
                         unsigned char value)
{
    unsigned int index = y * QR_V40_SIZE + x;
    v40_set(v40_reserved, index);
    v40_put(matrix, index, value);
}

static void v40_finder(unsigned char *matrix, int y0, int x0)
{
    int dy;
    int dx;
    for (dy = -1; dy <= 7; ++dy) {
        for (dx = -1; dx <= 7; ++dx) {
            int y = y0 + dy;
            int x = x0 + dx;
            unsigned char black = 0;
            if (y < 0 || y >= QR_V40_SIZE || x < 0 || x >= QR_V40_SIZE)
                continue;
            if (dy >= 0 && dy <= 6 && dx >= 0 && dx <= 6)
                black = (unsigned char)(dy == 0 || dy == 6 || dx == 0 || dx == 6
                                  || (dy >= 2 && dy <= 4 && dx >= 2 && dx <= 4));
            v40_function(matrix, (unsigned int)y, (unsigned int)x, black);
        }
    }
}

static unsigned long v40_version_bits(void)
{
    /*
     * 18ビットはia16のunsigned intに収まらない。
     * 指定生成多項式でGF(2)除算し、Version情報には形式情報用のXORを掛けない。
     */
    /* 18 bits cannot fit in ia16 unsigned int. GF(2) division, generator
     * x^12+x^11+x^10+x^9+x^8+x^5+x^2+1. No format XOR on version bits.
     */
    unsigned long remainder = 40UL << 12;
    int degree;
    for (degree = 17; degree >= 12; --degree) {
        if (remainder & (1UL << degree))
            remainder ^= 0x1F25UL << (degree - 12);
    }
    return (40UL << 12) | remainder;
}

static void v40_write_version(unsigned char *matrix)
{
    unsigned long word = v40_version_bits();
    unsigned int i;
    for (i = 0; i < 18; ++i) {
        unsigned int near = i / 3u;
        unsigned int far = QR_V40_SIZE - 11u + i % 3u;
        unsigned char bit = (unsigned char)((word >> i) & 1UL);
        v40_function(matrix, near, far, bit);
        v40_function(matrix, far, near, bit);
    }
}

static void v40_write_format(unsigned char *matrix, unsigned int mask)
{
    unsigned int word = format_bits(mask, ECC_FORMAT_L);
    unsigned int i;
    for (i = 0; i < 15; ++i) {
        unsigned int y = i < 6u ? i : (i < 8u ? i + 1u : QR_V40_SIZE - 15u + i);
        unsigned int x = i < 8u ? QR_V40_SIZE - 1u - i : (i == 8u ? 7u : 14u - i);
        unsigned char bit = (unsigned char)((word >> i) & 1u);
        v40_function(matrix, y, 8, bit);
        v40_function(matrix, 8, x, bit);
    }
}

static void v40_build_functions(unsigned char *matrix)
{
    static const unsigned char centers[7] = {6, 30, 58, 86, 114, 142, 170};
    unsigned int i;
    unsigned int j;
    memset(matrix, 0, QR_V40_PACKED_BYTES);
    memset(v40_reserved, 0, sizeof(v40_reserved));
    v40_finder(matrix, 0, 0);
    v40_finder(matrix, 0, QR_V40_SIZE - 7);
    v40_finder(matrix, QR_V40_SIZE - 7, 0);
    for (i = 8; i < QR_V40_SIZE - 8u; ++i) {
        v40_function(matrix, 6, i, (unsigned char)(i % 2u == 0));
        v40_function(matrix, i, 6, (unsigned char)(i % 2u == 0));
    }
    for (i = 0; i < 7; ++i) {
        for (j = 0; j < 7; ++j) {
            int dy;
            int dx;
            /*
             * 検出パターンと重なる3箇所だけを除外する。
             * 行・列6に残る10中心では位置合わせパターンがタイミングを上書きする。
             */
            /* Exclude ONLY the three finder overlaps. Alignment overwrites
             * timing modules at the ten surviving centers on row/column 6.
             */
            if ((i == 0 && (j == 0 || j == 6)) || (i == 6 && j == 0))
                continue;
            for (dy = -2; dy <= 2; ++dy)
                for (dx = -2; dx <= 2; ++dx)
                    v40_function(matrix, (unsigned int)(centers[i] + dy),
                                 (unsigned int)(centers[j] + dx),
                                 (unsigned char)(dy == -2 || dy == 2 || dx == -2
                                          || dx == 2 || (dy == 0 && dx == 0)));
        }
    }
    v40_function(matrix, QR_V40_SIZE - 8u, 8, 1);
    v40_write_format(matrix, 0);
    v40_write_version(matrix); /* Reserve both copies BEFORE data placement. */ /* データ配置前に両方のVersion情報領域を予約する。 */
}

static void v40_place_codewords(const unsigned char *words, unsigned char *matrix,
                                unsigned int mask)
{
    int right;
    unsigned int upwards = 1;
    unsigned int used = 0;
    unsigned int row;
    unsigned int offset;
    for (right = QR_V40_SIZE - 1; right > 0; right -= 2) {
        if (right == 6)
            --right;
        for (row = 0; row < QR_V40_SIZE; ++row) {
            unsigned int y = upwards ? QR_V40_SIZE - 1u - row : row;
            for (offset = 0; offset < 2; ++offset) {
                unsigned int x = (unsigned int)right - offset;
                unsigned int index = y * QR_V40_SIZE + x;
                unsigned char bit;
                if (v40_get(v40_reserved, index))
                    continue;
                /*
                 * 未予約セルはちょうど29648個でV40に余りビットはない。
                 * 各候補は元の符号語ビットから配置する。
                 */
                /* Exactly 29648 unreserved cells: no remainder bits in V40.
                 * Every candidate starts from the original codeword bits.
                 */
                bit = (unsigned char)((words[used >> 3] >> (7u - (used & 7u))) & 1u);
                v40_put(matrix, index, (unsigned char)(bit ^ mask_applies(mask, y, x)));
                ++used;
            }
        }
        upwards ^= 1u;
    }
}

static void v40_place_candidate(const unsigned char *words, unsigned char *matrix,
                                unsigned int mask)
{
    v40_place_codewords(words, matrix, mask);
    v40_write_format(matrix, mask);
    /*
     * Version情報は機能パターン構築時に完成し、予約済み。
     */
    /* Version Information was completed by build_functions and is reserved. */
}

struct v40_penalties {
    unsigned long n1, n2, n3, n4;
};

static void v40_score_line(const unsigned char *matrix, unsigned int index,
                           unsigned int vertical, struct v40_penalties *score)
{
    unsigned int count = 1;
    unsigned int i;
    unsigned char first = v40_get(matrix, vertical ? index : index * QR_V40_SIZE);
    unsigned char previous = first;
    v40_runs[0] = 1;
    for (i = 1; i < QR_V40_SIZE; ++i) {
        unsigned char cell = v40_get(matrix, vertical ? i * QR_V40_SIZE + index
                                                      : index * QR_V40_SIZE + i);
        if (cell == previous)
            ++v40_runs[count - 1];
        else {
            v40_runs[count++] = 1;
            previous = cell;
        }
    }
    for (i = 0; i < count; ++i)
        score->n1 += run_penalty(v40_runs[i]);
    /*
     * V1/V2と同じPC98QRのN3ラン・仮想白領域の契約を使う。
     */
    /* Same explicit PC98QR N3 run/virtual-light contract as V1/V2. */
    for (i = 0; i + 4u < count; ++i) {
        unsigned int k = v40_runs[i];
        unsigned int left;
        unsigned int right;
        if ((first ^ (i % 2u)) == 0 || v40_runs[i + 1] != k
            || v40_runs[i + 2] != 3u * k || v40_runs[i + 3] != k || v40_runs[i + 4] != k)
            continue;
        left = i <= 1u ? 4u * k : v40_runs[i - 1];
        right = i + 6u >= count ? 4u * k : v40_runs[i + 5];
        if (left >= 4u * k && right >= k)
            score->n3 += 40UL;
        if (right >= 4u * k && left >= k)
            score->n3 += 40UL;
    }
}

static unsigned long v40_balance_penalty(unsigned int dark)
{
    unsigned int total = QR_V40_SIZE * QR_V40_SIZE;
    unsigned int twice = 2u * dark;
    unsigned int deviation = twice > total ? twice - total : total - twice;
    /*
     * 偏差の10倍は313290に達するので、ia16では乗算前に型を広げる。
     */
    /* 10*deviation reaches 313290: promote BEFORE multiplication on ia16. */
    return deviation == 0 ? 0UL : 10UL * ((10UL * deviation - 1UL) / total);
}

static unsigned long v40_score_symbol(const unsigned char *matrix,
                                      struct v40_penalties *score)
{
    unsigned int y;
    unsigned int x;
    unsigned int dark = 0;
    memset(score, 0, sizeof(*score));
    for (y = 0; y < QR_V40_SIZE; ++y) {
        v40_score_line(matrix, y, 0, score);
        v40_score_line(matrix, y, 1, score);
        for (x = 0; x < QR_V40_SIZE; ++x) {
            unsigned int index = y * QR_V40_SIZE + x;
            unsigned char cell = v40_get(matrix, index);
            dark += cell;
            if (y + 1u < QR_V40_SIZE && x + 1u < QR_V40_SIZE
                && cell == v40_get(matrix, index + 1u)
                && cell == v40_get(matrix, index + QR_V40_SIZE)
                && cell == v40_get(matrix, index + QR_V40_SIZE + 1u))
                score->n2 += 3UL;
        }
    }
    score->n4 = v40_balance_penalty(dark);
    /*
     * 一色の行列でも154968点となるため、各成分と合計は32ビットunsigned longを使う。
     * 座標と添字は16ビットに収まる。
     */
    /* Even a constant matrix scores 154968. All components and their sum use
     * unsigned long (32 bits on ia16). Coordinates/indices stay 16-bit-safe.
     */
    return score->n1 + score->n2 + score->n3 + score->n4;
}

static void v40_encode_codewords(unsigned char *matrix)
{
    unsigned int mask;
    unsigned int best_mask = 0;
    unsigned long best_score = 0;
    struct v40_penalties components;
    v40_interleave(v40_data, v40_interleaved);
    v40_build_functions(matrix);
    for (mask = 0; mask < 8; ++mask) {
        unsigned long score;
        v40_place_candidate(v40_interleaved, matrix, mask);
        score = v40_score_symbol(matrix, &components);
        if (mask == 0 || score < best_score) {
            best_mask = mask;
            best_score = score;
        }
    }
    v40_place_candidate(v40_interleaved, matrix, best_mask);
}

int qr_encode_v40l_bytes(const unsigned char *data, unsigned int length,
                        unsigned char matrix[QR_V40_PACKED_BYTES])
{
    if (length == 0 || length > QR_V40L_BYTE_MAX)
        return QR_ERROR_LENGTH;
    v40_build_data(data, length, v40_data);
    v40_encode_codewords(v40_matrix);
    memcpy(matrix, v40_matrix, QR_V40_PACKED_BYTES);
    return QR_OK;
}
