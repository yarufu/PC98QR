/*
 * encode_mainと同じ方法でV2内部を観察するホスト専用処理。
 * ここでのスタック配列やstdioはDOS実行ファイルへ入らない。
 */
/* Host-only V2 inspection, using the same private-stage access as encode_main.c.
 * Stack arrays and stdio here never enter the DOS executable.
 */
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "../src/qr_encode.c"

static void print_hex(const unsigned char *data, unsigned int length)
{
    unsigned int i;
    for (i = 0; i < length; ++i)
        printf("%02x", data[i]);
    putchar('\n');
}

static void print_matrix(const unsigned char *matrix)
{
    unsigned int i;
    for (i = 0; i < QR_V2_SIZE * QR_V2_SIZE; ++i) {
        printf("%u", matrix[i]);
        if (i % QR_V2_SIZE == QR_V2_SIZE - 1)
            putchar('\n');
    }
}

static void print_score(const unsigned char *matrix)
{
    struct penalties score;
    unsigned int total = score_symbol(QR_V2_SIZE, matrix, &score);
    printf("%u %u %u %u %u\n", score.n1, score.n2, score.n3, score.n4, total);
}

static void inspect_placement(unsigned int ecc_format_bits)
{
    unsigned char baseline[625], changed[625], visited[625] = {0};
    unsigned char words[44] = {0}; /* Exact length: sanitizers detect overreads. */ /* 正確な長さで確保し、サニタイザーで過剰読み取りを検出する。 */
    unsigned int bit, i, available = 0, remainder = 0;
    build_functions(QR_V2_SIZE, baseline, ecc_format_bits);
    place_codewords(QR_V2_SIZE, 44, words, baseline, 0);
    for (i = 0; i < 625; ++i)
        available += !reserved[i];
    assert(available == 359);
    for (bit = 0; bit < 352; ++bit) {
        unsigned int differences = 0;
        memset(words, 0, sizeof(words));
        words[bit / 8] = (unsigned char)(0x80u >> (bit % 8));
        build_functions(QR_V2_SIZE, changed, ecc_format_bits);
        place_codewords(QR_V2_SIZE, 44, words, changed, 0);
        for (i = 0; i < 625; ++i) {
            if (baseline[i] != changed[i]) {
                assert(!reserved[i] && !visited[i]);
                visited[i] = 1;
                ++differences;
                printf("%u %u\n", i / 25, i % 25);
            }
        }
        assert(differences == 1);
    }
    for (i = 0; i < 625; ++i) {
        if (!reserved[i] && !visited[i]) {
            assert(baseline[i] == mask_applies(0, i / 25, i % 25));
            ++remainder;
        }
    }
    assert(remainder == 7);
}

int main(int argc, char **argv)
{
    struct {
        unsigned char before, data[27], after;
    } input, saved;
    struct {
        unsigned char before, matrix[25][25], after;
    } output;
    struct {
        unsigned char before, words[44], after;
    } guarded;
    unsigned char *flat = (unsigned char *)output.matrix;
    const char *mode;
    const unsigned char *generator;
    unsigned int data_bytes, ecc_bytes, maximum, ecc_format_bits;
    int (*encode)(const unsigned char *, unsigned int, unsigned char [25][25]);
    unsigned int i;
    int argument;
    if (argc < 2)
        return 10;
    /*
     * 既存のQコマンドを維持し、m-接頭辞で固定V2-Mを選ぶ。
     */
    /* Preserve every existing Q command; the m- prefix selects fixed V2-M. */
    mode = argv[1];
    if (strncmp(mode, "m-", 2) == 0) {
        mode += 2;
        data_bytes = 28;
        ecc_bytes = 16;
        maximum = 26;
        ecc_format_bits = ECC_FORMAT_M;
        generator = rs_generator_v2m;
        encode = qr_encode_v2m_bytes;
    } else {
        data_bytes = 22;
        ecc_bytes = 22;
        maximum = 20;
        ecc_format_bits = ECC_FORMAT_Q;
        generator = rs_generator_v2q;
        encode = qr_encode_v2q_bytes;
    }
    if (argc == 2 && strcmp(mode, "invalid-pointers") == 0) {
        assert(encode(NULL, 0, NULL) == QR_ERROR_LENGTH);
        assert(encode(NULL, maximum + 1, NULL) == QR_ERROR_LENGTH);
        assert(encode(NULL, UINT_MAX, NULL) == QR_ERROR_LENGTH);
        return 0;
    }
    if (argc == 2 && strcmp(mode, "generator") == 0) {
        print_hex(generator, ecc_bytes);
        return 0;
    }
    if (argc == 2 && strcmp(mode, "formats") == 0) {
        for (i = 0; i < 8; ++i)
            printf("%04x\n", format_bits(i, ecc_format_bits));
        return 0;
    }
    if (argc == 2 && strcmp(mode, "placement") == 0) {
        inspect_placement(ecc_format_bits);
        return 0;
    }
    if (argc == 2 && strcmp(mode, "layout") == 0) {
        const unsigned char zeros[44] = {0};
        build_functions(QR_V2_SIZE, flat, ecc_format_bits);
        print_matrix(reserved);
        print_matrix(flat);
        place_codewords(QR_V2_SIZE, 44, zeros, flat, 0);
        print_matrix(flat);
        return 0;
    }
    if (argc == 2 && strcmp(mode, "balance") == 0) {
        for (i = 0; i <= 625; ++i)
            printf("%u\n", balance_penalty(QR_V2_SIZE, i));
        return 0;
    }
    if (argc == 2 && strcmp(mode, "maskmaps") == 0) {
        unsigned int mask;
        for (mask = 0; mask < 8; ++mask) {
            for (i = 0; i < 625; ++i)
                flat[i] = (unsigned char)mask_applies(mask, i / 25, i % 25);
            print_matrix(flat);
        }
        return 0;
    }
    for (argument = 2; argument < argc; ++argument) {
        unsigned int digits = (unsigned int)strlen(argv[argument]);
        unsigned int length = digits / 2;
        int result;
        if (strcmp(mode, "penalty") == 0) {
            assert(digits == 625);
            for (i = 0; i < 625; ++i) {
                assert(argv[argument][i] == '0' || argv[argument][i] == '1');
                flat[i] = (unsigned char)(argv[argument][i] - '0');
            }
            print_score(flat);
            continue;
        }
        memset(&guarded, 0xA5, sizeof(guarded));
        if (strcmp(mode, "ecc") == 0) {
            assert(digits == data_bytes * 2);
            for (i = 0; i < data_bytes; ++i) {
                unsigned int value;
                assert(sscanf(argv[argument] + 2 * i, "%2x", &value) == 1);
                guarded.words[i] = (unsigned char)value;
            }
            build_ecc(guarded.words, guarded.words + data_bytes, data_bytes, ecc_bytes, generator);
            assert(guarded.before == 0xA5 && guarded.after == 0xA5);
            print_hex(guarded.words, 44);
            continue;
        }
        assert(digits % 2 == 0 && length <= sizeof(input.data));
        memset(&input, 0xA5, sizeof(input));
        for (i = 0; i < length; ++i) {
            unsigned int value;
            assert(sscanf(argv[argument] + 2 * i, "%2x", &value) == 1);
            input.data[i] = (unsigned char)value;
        }
        saved = input;
        memset(&output, 0xA5, sizeof(output));
        result = encode(input.data, length, output.matrix);
        assert(memcmp(&input, &saved, sizeof(input)) == 0);
        assert(output.before == 0xA5 && output.after == 0xA5);
        if (result != QR_OK) {
            for (i = 0; i < 625; ++i)
                assert(flat[i] == 0xA5);
            return result;
        }
        if (strcmp(mode, "mixed") == 0) {
            struct {
                unsigned char before, matrix[21][21], after;
            } v1;
            unsigned char alnum[21][21], bytes[21][21], v2[25][25];
            unsigned char q[25][25], m[25][25];
            unsigned int v1_length = length < 11 ? length : 11;
            unsigned int q_length = length < 20 ? length : 20;
            memcpy(v2, output.matrix, sizeof(v2));
            memset(&v1, 0xA5, sizeof(v1));
            assert(qr_encode_v1q_alnum("HELLO", alnum) == QR_OK);
            assert(qr_encode_v1q_bytes(input.data, v1_length, bytes) == QR_OK);
            assert(qr_encode_v2q_bytes(input.data, q_length, q) == QR_OK);
            assert(qr_encode_v2m_bytes(input.data, length, m) == QR_OK);
            assert(qr_encode_v1q_bytes(input.data, v1_length, v1.matrix) == QR_OK);
            assert(memcmp(v1.matrix, bytes, sizeof(bytes)) == 0);
            assert(qr_encode_v2m_bytes(input.data, length, output.matrix) == QR_OK);
            assert(memcmp(output.matrix, m, sizeof(m)) == 0);
            assert(qr_encode_v2q_bytes(input.data, q_length, output.matrix) == QR_OK);
            assert(memcmp(output.matrix, q, sizeof(q)) == 0);
            assert(qr_encode_v1q_alnum("HELLO", v1.matrix) == QR_OK);
            assert(memcmp(v1.matrix, alnum, sizeof(alnum)) == 0);
            assert(qr_encode_v2m_bytes(input.data, length, output.matrix) == QR_OK);
            assert(memcmp(output.matrix, m, sizeof(m)) == 0);
            assert(qr_encode_v1q_bytes(input.data, v1_length, v1.matrix) == QR_OK);
            assert(memcmp(v1.matrix, bytes, sizeof(bytes)) == 0);
            assert(encode(input.data, length, output.matrix) == QR_OK);
            assert(memcmp(v2, output.matrix, sizeof(v2)) == 0);
            assert(v1.before == 0xA5 && v1.after == 0xA5);
            assert(output.before == 0xA5 && output.after == 0xA5);
            assert(memcmp(&input, &saved, sizeof(input)) == 0);
            print_matrix(flat);
        } else if (strcmp(mode, "matrix") == 0) {
            print_matrix(flat);
        } else {
            build_byte_data(input.data, length, guarded.words, data_bytes);
            assert(guarded.before == 0xA5 && guarded.after == 0xA5);
            assert(memcmp(&input, &saved, sizeof(input)) == 0);
            for (i = data_bytes; i < 44; ++i)
                assert(guarded.words[i] == 0xA5);
            if (strcmp(mode, "data") == 0) {
                print_hex(guarded.words, data_bytes);
                continue;
            }
            build_ecc(guarded.words, guarded.words + data_bytes, data_bytes, ecc_bytes, generator);
            assert(guarded.before == 0xA5 && guarded.after == 0xA5);
            assert(memcmp(codewords, guarded.words, sizeof(guarded.words)) == 0);
            if (strcmp(mode, "codewords") == 0) {
                print_hex(guarded.words, 44);
            } else if (strcmp(mode, "candidates") == 0) {
                build_functions(QR_V2_SIZE, flat, ecc_format_bits);
                for (i = 0; i < 8; ++i) {
                    place_candidate(QR_V2_SIZE, 44, guarded.words, flat, i, ecc_format_bits);
                    printf("%u %04x ", i, format_bits(i, ecc_format_bits));
                    print_score(flat);
                    print_matrix(flat);
                }
                assert(output.before == 0xA5 && output.after == 0xA5);
            } else {
                return 10;
            }
        }
    }
    return 0;
}
