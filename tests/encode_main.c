/*
 * 本番Cソースを取り込んで内部段階を観察するホスト専用ドライバー。
 * DOSの公開APIは拡張せず、このファイル単独でコンパイルする。
 * 本番は通常どおり別途リンクする。
 */
/* Host-only inspection driver. Including the identical production C source
 * lets tests inspect private stages without expanding the public DOS API.
 * Compile this file alone; production links src/qr_encode.c normally.
 */
#include <stdio.h>
#include <string.h>
#include <assert.h>

#include "../src/qr_encode.c"

#define V1_TOTAL_BYTES (V1_DATA_BYTES + V1_ECC_BYTES)

static void print_hex(const unsigned char *bytes, unsigned int length)
{
    unsigned int i;
    for (i = 0; i < length; ++i)
        printf("%02x", bytes[i]);
    putchar('\n');
}

static void print_matrix(const unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    unsigned int y;
    unsigned int x;
    for (y = 0; y < QR_V1_SIZE; ++y) {
        for (x = 0; x < QR_V1_SIZE; ++x)
            printf("%u", matrix[y][x]);
        putchar('\n');
    }
}

static void inspect_placement(void)
{
    static unsigned char baseline[QR_V1_SIZE][QR_V1_SIZE];
    static unsigned char changed[QR_V1_SIZE][QR_V1_SIZE];
    static unsigned char visited[QR_V1_SIZE][QR_V1_SIZE];
    unsigned char words[V1_TOTAL_BYTES] = {0}; /* Host-only test workspace. */ /* ホスト試験専用の作業領域。 */
    unsigned int bit;
    unsigned int y;
    unsigned int x;
    unsigned int available = 0;

    build_functions(QR_V1_SIZE, (unsigned char *)baseline, ECC_FORMAT_Q);
    place_codewords(QR_V1_SIZE, V1_TOTAL_BYTES, words, (unsigned char *)baseline, 0);
    for (y = 0; y < QR_V1_SIZE; ++y)
        for (x = 0; x < QR_V1_SIZE; ++x)
            available += !reserved[y * QR_V1_SIZE + x];
    assert(available == 208);

    /*
     * 元の各ビットは、それぞれ異なる機能パターン以外のセル一つだけに影響する必要がある。
     */
    /* Each source bit must affect exactly one distinct non-function cell. */
    for (bit = 0; bit < 208; ++bit) {
        unsigned int differences = 0;
        memset(words, 0, sizeof(words));
        words[bit / 8] = (unsigned char)(0x80u >> (bit % 8));
        build_functions(QR_V1_SIZE, (unsigned char *)changed, ECC_FORMAT_Q);
        place_codewords(QR_V1_SIZE, V1_TOTAL_BYTES, words, (unsigned char *)changed, 0);
        for (y = 0; y < QR_V1_SIZE; ++y) {
            for (x = 0; x < QR_V1_SIZE; ++x) {
                if (baseline[y][x] != changed[y][x]) {
                    assert(!reserved[y * QR_V1_SIZE + x] && !visited[y][x]);
                    visited[y][x] = 1;
                    ++differences;
                    printf("%u %u\n", y, x);
                }
            }
        }
        assert(differences == 1);
    }
    for (y = 0; y < QR_V1_SIZE; ++y)
        for (x = 0; x < QR_V1_SIZE; ++x)
            assert(visited[y][x] == !reserved[y * QR_V1_SIZE + x]);
}

static void print_score(const unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    struct penalties score;
    unsigned int total = score_symbol(QR_V1_SIZE, (const unsigned char *)matrix, &score);
    printf("%u %u %u %u %u\n", score.n1, score.n2, score.n3, score.n4, total);
}

static int inspect_bytes(int argc, char **argv)
{
    struct {
        unsigned char before;
        unsigned char data[QR_V1Q_BYTE_MAX + 1]; /* Also exercise length 12. */ /* 長さ12も検証する。 */
        unsigned char after;
    } input, saved;
    struct {
        unsigned char before;
        unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE];
        unsigned char after;
    } output;
    struct {
        unsigned char before;
        unsigned char words[V1_TOTAL_BYTES];
        unsigned char after;
    } guarded;
    unsigned int i;
    int argument;

    for (argument = 2; argument < argc; ++argument) {
        unsigned int digits = (unsigned int)strlen(argv[argument]);
        unsigned int length = digits / 2u;
        int result;
        if (digits % 2u != 0 || length > sizeof(input.data))
            return 10;
        memset(&input, 0xA5, sizeof(input));
        for (i = 0; i < length; ++i) {
            unsigned int value;
            if (sscanf(argv[argument] + i * 2u, "%2x", &value) != 1)
                return 10;
            input.data[i] = (unsigned char)value;
        }
        memcpy(&saved, &input, sizeof(input));
        if (strcmp(argv[1], "bytes-mixed") == 0) {
            assert(qr_encode_v1q_alnum("HELLO", output.matrix) == QR_OK);
            print_matrix(output.matrix);
        }
        memset(&output, 0xA5, sizeof(output));
        result = qr_encode_v1q_bytes(input.data, length, output.matrix);
        assert(memcmp(&input, &saved, sizeof(input)) == 0);
        assert(output.before == 0xA5 && output.after == 0xA5);
        if (result != QR_OK) {
            for (i = 0; i < QR_V1_SIZE * QR_V1_SIZE; ++i)
                assert(output.matrix[i / QR_V1_SIZE][i % QR_V1_SIZE] == 0xA5);
            return result;
        }
        if (strcmp(argv[1], "bytes-matrix") == 0 || strcmp(argv[1], "bytes-mixed") == 0) {
            print_matrix(output.matrix);
            continue;
        }
        memset(&guarded, 0xA5, sizeof(guarded));
        build_byte_data(input.data, length, guarded.words, V1_DATA_BYTES);
        assert(memcmp(&input, &saved, sizeof(input)) == 0);
        assert(guarded.before == 0xA5 && guarded.after == 0xA5);
        for (i = V1_DATA_BYTES; i < V1_TOTAL_BYTES; ++i)
            assert(guarded.words[i] == 0xA5);
        if (strcmp(argv[1], "bytes-data") == 0) {
            print_hex(guarded.words, V1_DATA_BYTES);
            continue;
        }
        build_ecc(guarded.words, guarded.words + V1_DATA_BYTES,
                  V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1);
        assert(guarded.before == 0xA5 && guarded.after == 0xA5);
        if (strcmp(argv[1], "bytes-codewords") == 0) {
            print_hex(guarded.words, V1_TOTAL_BYTES);
        } else if (strcmp(argv[1], "bytes-candidates") == 0) {
            build_functions(QR_V1_SIZE, (unsigned char *)output.matrix, ECC_FORMAT_Q);
            for (i = 0; i < 8; ++i) {
                place_candidate(QR_V1_SIZE, V1_TOTAL_BYTES, guarded.words,
                                (unsigned char *)output.matrix, i, ECC_FORMAT_Q);
                printf("%u %04x ", i, format_bits(i, ECC_FORMAT_Q));
                print_score(output.matrix);
                print_matrix(output.matrix);
            }
            assert(output.before == 0xA5 && output.after == 0xA5);
        } else {
            return 10;
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    unsigned int length;
    unsigned int i;
    int result;
    struct {
        unsigned char before;
        unsigned char data[V1_TOTAL_BYTES];
        unsigned char after;
    } guarded;

    if (argc >= 3 && strncmp(argv[1], "bytes-", 6) == 0)
        return inspect_bytes(argc, argv);

    if (argc == 2 && strcmp(argv[1], "placement") == 0) {
        inspect_placement();
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "layout") == 0) {
        static unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE];
        static const unsigned char zeros[V1_TOTAL_BYTES] = {0};
        build_functions(QR_V1_SIZE, (unsigned char *)matrix, ECC_FORMAT_Q);
        print_matrix((const unsigned char (*)[QR_V1_SIZE])reserved);
        print_matrix(matrix);
        place_codewords(QR_V1_SIZE, V1_TOTAL_BYTES, zeros, (unsigned char *)matrix, 0);
        print_matrix(matrix);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "gf") == 0) {
        unsigned int j;
        for (i = 0; i < 256; ++i)
            for (j = 0; j < 256; ++j)
                printf("%02x", gf_multiply((unsigned char)i, (unsigned char)j));
        putchar('\n');
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "generator") == 0) {
        print_hex(rs_generator_v1, V1_ECC_BYTES);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "formats") == 0) {
        for (i = 0; i < 8; ++i)
            printf("%04x\n", format_bits(i, ECC_FORMAT_Q));
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "maskmaps") == 0) {
        unsigned int row;
        unsigned int col;
        for (i = 0; i < 8; ++i)
            for (row = 0; row < QR_V1_SIZE; ++row) {
                for (col = 0; col < QR_V1_SIZE; ++col)
                    printf("%u", mask_applies(i, row, col));
                putchar('\n');
            }
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "runs") == 0) {
        for (i = 1; i <= QR_V1_SIZE; ++i)
            printf("%u\n", run_penalty(i));
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "balance") == 0) {
        for (i = 0; i <= QR_V1_SIZE * QR_V1_SIZE; ++i)
            printf("%u\n", balance_penalty(QR_V1_SIZE, i));
        return 0;
    }
    if (argc < 3)
        return 10;
    if (strcmp(argv[1], "penalty") == 0) {
        static unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE];
        int argument;
        for (argument = 2; argument < argc; ++argument) {
            if (strlen(argv[argument]) != QR_V1_SIZE * QR_V1_SIZE)
                return 10;
            for (i = 0; i < QR_V1_SIZE * QR_V1_SIZE; ++i) {
                char cell = argv[argument][i];
                if (cell != '0' && cell != '1')
                    return 10;
                matrix[i / QR_V1_SIZE][i % QR_V1_SIZE] = (unsigned char)(cell - '0');
            }
            print_score(matrix);
        }
        return 0;
    }
    if (strcmp(argv[1], "forced") == 0 || strcmp(argv[1], "candidates") == 0) {
        static unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE];
        unsigned char words[V1_TOTAL_BYTES]; /* Host-only. */ /* ホスト試験専用。 */
        int candidates = strcmp(argv[1], "candidates") == 0;
        unsigned int mask = 0;
        int argument;
        if (!candidates) {
            if (argc < 4 || strlen(argv[2]) != 1 || argv[2][0] < '0' || argv[2][0] > '7')
                return 10;
            mask = (unsigned int)(argv[2][0] - '0');
        }
        for (argument = candidates ? 2 : 3; argument < argc; ++argument) {
            result = validate_text(argv[argument], &length);
            if (result != QR_OK)
                return result;
            build_data(argv[argument], length, words);
            build_ecc(words, words + V1_DATA_BYTES, V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1);
            build_functions(QR_V1_SIZE, (unsigned char *)matrix, ECC_FORMAT_Q);
            for (i = candidates ? 0 : mask; i < (candidates ? 8 : mask + 1); ++i) {
                place_candidate(QR_V1_SIZE, V1_TOTAL_BYTES, words, (unsigned char *)matrix, i, ECC_FORMAT_Q);
                if (candidates) {
                    printf("%u %04x ", i, format_bits(i, ECC_FORMAT_Q));
                    print_score(matrix);
                }
                print_matrix(matrix);
            }
        }
        return 0;
    }
    if (strcmp(argv[1], "matrix") == 0) {
        static struct {
            unsigned char before;
            unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE];
            unsigned char after;
        } output;
        int argument;
        for (argument = 2; argument < argc; ++argument) {
            memset(&output, 0xA5, sizeof(output));
            result = qr_encode_v1q_alnum(argv[argument], output.matrix);
            assert(output.before == 0xA5 && output.after == 0xA5);
            if (result != QR_OK) {
                unsigned int y;
                unsigned int x;
                for (y = 0; y < QR_V1_SIZE; ++y)
                    for (x = 0; x < QR_V1_SIZE; ++x)
                        assert(output.matrix[y][x] == 0xA5);
                return result;
            }
            print_matrix(output.matrix);
        }
        return 0;
    }
    if (argc != 3)
        return 10;
    if (strcmp(argv[1], "ecc") == 0) {
        unsigned int value;
        if (strlen(argv[2]) != V1_DATA_BYTES * 2)
            return 10;
        for (i = 0; i < V1_DATA_BYTES; ++i) {
            if (sscanf(argv[2] + i * 2, "%2x", &value) != 1)
                return 10;
            guarded.data[i] = (unsigned char)value;
        }
        build_ecc(guarded.data, guarded.data + V1_DATA_BYTES,
                  V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1);
        print_hex(guarded.data, V1_TOTAL_BYTES);
        return 0;
    }
    result = validate_text(argv[2], &length);
    if (result != QR_OK)
        return result;
    if (strcmp(argv[1], "validate") == 0) {
        printf("%u\n", length);
    } else if (strcmp(argv[1], "data") == 0 || strcmp(argv[1], "codewords") == 0) {
        memset(&guarded, 0xA5, sizeof(guarded));
        build_data(argv[2], length, guarded.data);
        assert(guarded.before == 0xA5 && guarded.after == 0xA5);
        for (i = V1_DATA_BYTES; i < V1_TOTAL_BYTES; ++i)
            assert(guarded.data[i] == 0xA5); /* In particular the 16-char boundary. */ /* 特に16文字の上限を確認する。 */
        if (strcmp(argv[1], "codewords") == 0) {
            build_ecc(guarded.data, guarded.data + V1_DATA_BYTES,
                  V1_DATA_BYTES, V1_ECC_BYTES, rs_generator_v1);
            assert(guarded.before == 0xA5 && guarded.after == 0xA5);
            print_hex(guarded.data, V1_TOTAL_BYTES);
        } else {
            print_hex(guarded.data, V1_DATA_BYTES);
        }
    } else {
        return 10;
    }
    return 0;
}
