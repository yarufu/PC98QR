/*
 * 本番ソースの内部段階を公開せず観察するホスト専用処理。
 * 試験用フック・stdio・保存領域は本番へ入れない。
 */
/* Host-only inspection. No hooks, stdio, or test storage enter production.
 * Include the original C to observe the private stages without exporting them.
 */
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/qr_encode.c"

static void hex(const unsigned char *data, unsigned int length)
{
    unsigned int i;
    putchar('"');
    for (i = 0; i < length; ++i)
        printf("%02x", data[i]);
    putchar('"');
}

static void logical(const unsigned char *matrix)
{
    unsigned int i;
    putchar('"');
    for (i = 0; i < QR_V25_SIZE * QR_V25_SIZE; ++i)
        putchar('0' + v25_get(matrix, i));
    putchar('"');
}

static unsigned long score_json(const unsigned char *matrix)
{
    struct v25_penalties p;
    unsigned long total = v25_score_symbol(matrix, &p);
    printf("[%lu,%lu,%lu,%lu,%lu]", p.n1, p.n2, p.n3, p.n4, total);
    return total;
}

static unsigned int unhex(const char *text, unsigned char *data, unsigned int maximum)
{
    unsigned int i;
    unsigned int length = (unsigned int)strlen(text) / 2;
    assert(strlen(text) % 2 == 0 && length <= maximum);
    for (i = 0; i < length; ++i) {
        unsigned int v;
        assert(sscanf(text + 2 * i, "%2x", &v) == 1);
        data[i] = (unsigned char)v;
    }
    return length;
}

static void invalid(void)
{
    static const unsigned int lengths[] = {0, 1274, 65535u, UINT_MAX};
    unsigned char output[QR_V25_PACKED_BYTES];
    unsigned int i, j;
    for (i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i) {
        assert(qr_encode_v25l_bytes(NULL, lengths[i], NULL) == QR_ERROR_LENGTH);
        memset(output, 0xA5, sizeof(output));
        assert(qr_encode_v25l_bytes(NULL, lengths[i], output) == QR_ERROR_LENGTH);
        for (j = 0; j < sizeof(output); ++j)
            assert(output[j] == 0xA5);
    }
    puts("true");
}

static void mixed(const unsigned char *payload, unsigned int length)
{
    unsigned char a[21][21], b[21][21], q[25][25], m[25][25];
    unsigned char small[25][25], v1[21][21];
    unsigned char large[QR_V25_PACKED_BYTES], again[QR_V25_PACKED_BYTES];
    unsigned int n, i;
    unsigned int nb = length < 11u ? length : 11u;
    unsigned int nq = length < 20u ? length : 20u;
    unsigned int nm = length < 26u ? length : 26u;
    assert(qr_encode_v1q_alnum("HELLO", a) == QR_OK);
    assert(qr_encode_v1q_bytes(payload, nb, b) == QR_OK);
    assert(qr_encode_v2q_bytes(payload, nq, q) == QR_OK);
    assert(qr_encode_v2m_bytes(payload, nm, m) == QR_OK);
    assert(qr_encode_v25l_bytes(payload, length, large) == QR_OK);
    for (i = 0; i < 4; ++i) {
        n = i % 2 ? length : 1;
        assert(qr_encode_v25l_bytes(payload, n, again) == QR_OK);
        assert(qr_encode_v1q_alnum("HELLO", v1) == QR_OK);
        assert(memcmp(a, v1, sizeof(a)) == 0);
        assert(qr_encode_v2m_bytes(payload, nm, small) == QR_OK);
        assert(memcmp(m, small, sizeof(m)) == 0);
        assert(qr_encode_v1q_bytes(payload, nb, v1) == QR_OK);
        assert(memcmp(b, v1, sizeof(b)) == 0);
        assert(qr_encode_v2q_bytes(payload, nq, small) == QR_OK);
        assert(memcmp(q, small, sizeof(q)) == 0);
        assert(qr_encode_v25l_bytes(payload, length, again) == QR_OK);
        assert(memcmp(large, again, sizeof(large)) == 0);
    }
    hex(again, sizeof(again));
    putchar('\n');
}

static void placement(void)
{
    /*
     * 入力を正確に1588バイトとし、その直後をASanの検出対象にする。
     * 14枚のビット識別面で12704個の元ビットを区別し、1ビットずつ全行列を作るより軽く検査する。
     */
    /* Exactly 1588 source bytes: ASan poisons the byte immediately after it.
     * Fourteen binary label planes distinguish every one of 12704 bit sources.
     * This is cheaper than 12704 one-hot full-matrix placements.
     */
    unsigned char words[V25_TOTAL_BYTES];
    unsigned char matrix[QR_V25_PACKED_BYTES];
    unsigned int plane, bit, mask;
    printf("{\"planes\":[");
    v25_build_functions(matrix);
    for (plane = 0; plane < 14; ++plane) {
        memset(words, 0, sizeof(words));
        for (bit = 0; bit < V25_TOTAL_BYTES * 8u; ++bit)
            if (((bit + 1u) >> plane) & 1u)
                words[bit >> 3] |= (unsigned char)(0x80u >> (bit & 7u));
        v25_place_codewords(words, matrix, 0);
        if (plane) putchar(',');
        logical(matrix);
    }
    printf("],\"zero_masks\":[");
    memset(words, 0, sizeof(words));
    for (mask = 0; mask < 8; ++mask) {
        v25_place_candidate(words, matrix, mask);
        if (mask) putchar(',');
        logical(matrix);
    }
    puts("]}");
}

static void inspect(const unsigned char *payload, unsigned int length)
{
    struct { unsigned char before, data[V25_DATA_BYTES], after; } data;
    struct { unsigned char before, words[V25_TOTAL_BYTES], after; } words;
    struct { unsigned char before, matrix[QR_V25_PACKED_BYTES], after; } output;
    unsigned char candidate[QR_V25_PACKED_BYTES];
    unsigned int block, mask, best = 0;
    unsigned long best_score = 0;
    memset(&data, 0xA5, sizeof(data));
    memset(&words, 0xA5, sizeof(words));
    memset(&output, 0xA5, sizeof(output));
    assert(qr_encode_v25l_bytes(payload, length, output.matrix) == QR_OK);
    v25_build_data(payload, length, data.data);
    v25_interleave(data.data, words.words);
    assert(memcmp(v25_data, data.data, sizeof(data.data)) == 0);
    assert(memcmp(v25_interleaved, words.words, sizeof(words.words)) == 0);
    assert(data.before == 0xA5 && data.after == 0xA5);
    assert(words.before == 0xA5 && words.after == 0xA5);
    assert(output.before == 0xA5 && output.after == 0xA5);
    assert((output.matrix[QR_V25_PACKED_BYTES - 1] & 0x7Fu) == 0);
    printf("{\"data\":"); hex(data.data, sizeof(data.data));
    printf(",\"blocks\":[");
    for (block = 0; block < V25_BLOCKS; ++block) {
        unsigned int offset = v25_block_offset(block), n = v25_block_length(block);
        if (block) putchar(',');
        printf("{\"offset\":%u,\"data\":", offset); hex(data.data + offset, n);
        v25_build_block_ecc(data.data + offset, n);
        printf(",\"ecc\":"); hex(v25_rs + n, V25_ECC_BYTES); putchar('}');
    }
    printf("],\"interleaved\":"); hex(words.words, sizeof(words.words));
    v25_build_functions(candidate);
    printf(",\"reserved\":"); logical(v25_reserved);
    printf(",\"reserved_packed\":"); hex(v25_reserved, sizeof(v25_reserved));
    printf(",\"functions\":"); logical(candidate);
    printf(",\"candidates\":[");
    for (mask = 0; mask < 8; ++mask) {
        unsigned long score;
        v25_place_candidate(words.words, candidate, mask);
        if (mask) putchar(',');
        printf("{\"score\":"); score = score_json(candidate);
        printf(",\"matrix\":"); logical(candidate); putchar('}');
        if (mask == 0 || score < best_score) { best = mask; best_score = score; }
    }
    printf("],\"selected\":%u,\"packed\":", best);
    hex(output.matrix, sizeof(output.matrix));
    printf(",\"logical\":"); logical(v25_matrix);
    puts("}");
}

int main(int argc, char **argv)
{
    unsigned char payload[1276], saved[1276];
    unsigned int length, i;
    assert(argc >= 2);
    if (strcmp(argv[1], "invalid") == 0) { invalid(); return 0; }
    if (strcmp(argv[1], "placement") == 0) { placement(); return 0; }
    if (strcmp(argv[1], "constants") == 0) {
        printf("{\"generator\":"); hex(rs_generator_v25, V25_ECC_BYTES);
        printf(",\"version\":%lu,\"formats\":[", v25_version_bits());
        for (i = 0; i < 8; ++i) printf("%s%u", i ? "," : "", format_bits(i, ECC_FORMAT_L));
        puts("]}"); return 0;
    }
    if (strcmp(argv[1], "balance") == 0) {
        putchar('[');
        for (i = 0; i <= 13689; ++i) printf("%s%lu", i ? "," : "", v25_balance_penalty(i));
        puts("]"); return 0;
    }
    assert(argc == 3);
    if (strcmp(argv[1], "penalty") == 0) {
        unsigned char matrix[QR_V25_PACKED_BYTES] = {0};
        assert(strlen(argv[2]) == QR_V25_SIZE * QR_V25_SIZE);
        for (i = 0; i < QR_V25_SIZE * QR_V25_SIZE; ++i) {
            assert(argv[2][i] == '0' || argv[2][i] == '1');
            v25_put(matrix, i, (unsigned char)(argv[2][i] - '0'));
        }
        score_json(matrix); putchar('\n'); return 0;
    }
    memset(payload, 0xA5, sizeof(payload));
    length = unhex(argv[2], payload, sizeof(payload));
    memcpy(saved, payload, sizeof(payload));
    if (strcmp(argv[1], "ecc") == 0) {
        /*
         * ASanが正確な入力境界を検出できるよう、ホストで意図的にmallocする。
         */
        /* Host malloc deliberately gives ASan an exact input bound. */
        unsigned char *exact;
        assert(length == 106 || length == 107);
        exact = malloc(length);
        assert(exact != NULL);
        memcpy(exact, payload, length);
        v25_build_block_ecc(exact, length);
        assert(memcmp(exact, payload, length) == 0);
        hex(v25_rs + length, V25_ECC_BYTES); putchar('\n');
        free(exact);
    } else if (strcmp(argv[1], "interleave") == 0) {
        unsigned char words[V25_TOTAL_BYTES];
        assert(length == V25_DATA_BYTES);
        v25_interleave(payload, words);
        hex(words, sizeof(words)); putchar('\n');
    } else {
        unsigned char *exact;
        assert(length >= 1 && length <= 1273);
        exact = malloc(length);
        assert(exact != NULL);
        memcpy(exact, payload, length);
        if (strcmp(argv[1], "inspect") == 0) inspect(exact, length);
        else if (strcmp(argv[1], "mixed") == 0) mixed(exact, length);
        else { free(exact); return 10; }
        assert(memcmp(exact, payload, length) == 0);
        free(exact);
    }
    assert(memcmp(payload, saved, sizeof(payload)) == 0);
    return 0;
}
