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
    for (i = 0; i < QR_V40_SIZE * QR_V40_SIZE; ++i)
        putchar('0' + v40_get(matrix, i));
    putchar('"');
}

static unsigned long score_json(const unsigned char *matrix)
{
    struct v40_penalties p;
    unsigned long total = v40_score_symbol(matrix, &p);
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
    static const unsigned int lengths[] = {0, 2954, 65535u, UINT_MAX};
    unsigned char output[QR_V40_PACKED_BYTES];
    unsigned int i, j;
    for (i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i) {
        assert(qr_encode_v40l_bytes(NULL, lengths[i], NULL) == QR_ERROR_LENGTH);
        memset(output, 0xA5, sizeof(output));
        assert(qr_encode_v40l_bytes(NULL, lengths[i], output) == QR_ERROR_LENGTH);
        for (j = 0; j < sizeof(output); ++j)
            assert(output[j] == 0xA5);
    }
    puts("true");
}

static void mixed(const unsigned char *payload, unsigned int length)
{
    unsigned char a[21][21], b[21][21], q[25][25], m[25][25];
    unsigned char small[25][25], v1[21][21];
    unsigned char old1000[QR_V25_PACKED_BYTES], old1273[QR_V25_PACKED_BYTES];
    unsigned char old_again[QR_V25_PACKED_BYTES], old_payload[1273];
    unsigned char large[QR_V40_PACKED_BYTES], again[QR_V40_PACKED_BYTES];
    static const unsigned char special[] = {0,0x1A,0x80,0xFF,13,10,0,0x41,0xC3,0x28,0x7F};
    unsigned int i;
    unsigned int nb = length < 11u ? length : 11u;
    unsigned int nq = length < 20u ? length : 20u;
    unsigned int nm = length < 26u ? length : 26u;
    /*
     * 特別な接頭辞を含め、編集前ドライバーと同じV25ベクトルを使う。
     */
    /* Same V25 vectors as the pre-edit driver, including its special prefix. */
    for (i = 0; i < sizeof(old_payload); ++i)
        old_payload[i] = i < sizeof(special) ? special[i]
                          : (unsigned char)((i - sizeof(special)) * 73u + 19u);
    assert(qr_encode_v1q_alnum("HELLO", a) == QR_OK);
    assert(qr_encode_v1q_bytes(payload, nb, b) == QR_OK);
    assert(qr_encode_v2q_bytes(payload, nq, q) == QR_OK);
    assert(qr_encode_v2m_bytes(payload, nm, m) == QR_OK);
    assert(qr_encode_v25l_bytes(old_payload, 1000, old1000) == QR_OK);
    assert(qr_encode_v25l_bytes(old_payload, 1273, old1273) == QR_OK);
    assert(qr_encode_v40l_bytes(payload, length, large) == QR_OK);
    for (i = 0; i < 4; ++i) {
        assert(qr_encode_v40l_bytes(payload, i % 2 ? length : 1, again) == QR_OK);
        assert(qr_encode_v25l_bytes(old_payload, 1000, old_again) == QR_OK);
        assert(memcmp(old1000, old_again, sizeof(old_again)) == 0);
        assert(qr_encode_v1q_alnum("HELLO", v1) == QR_OK);
        assert(memcmp(a, v1, sizeof(a)) == 0);
        assert(qr_encode_v2m_bytes(payload, nm, small) == QR_OK);
        assert(memcmp(m, small, sizeof(m)) == 0);
        assert(qr_encode_v1q_bytes(payload, nb, v1) == QR_OK);
        assert(memcmp(b, v1, sizeof(b)) == 0);
        assert(qr_encode_v2q_bytes(payload, nq, small) == QR_OK);
        assert(memcmp(q, small, sizeof(q)) == 0);
        assert(qr_encode_v25l_bytes(old_payload, 1273, old_again) == QR_OK);
        assert(memcmp(old1273, old_again, sizeof(old_again)) == 0);
        assert(qr_encode_v40l_bytes(payload, length, again) == QR_OK);
        assert(memcmp(large, again, sizeof(large)) == 0);
    }
    printf("{\"v40\":"); hex(again, sizeof(again));
    printf(",\"v25_1000\":"); hex(old1000, sizeof(old1000));
    printf(",\"v25_1273\":"); hex(old1273, sizeof(old1273));
    puts("}");
}

static void placement(void)
{
    /*
     * 入力を正確に3706バイトとし、直後をASanで検出する。
     * 15枚のビット識別面で29648個の元ビットを区別し、個別の全行列生成より軽く検査する。
     */
    /* Exactly 3706 source bytes: ASan poisons the byte immediately after it.
     * Fifteen binary label planes distinguish every one of 29648 bit sources.
     * This is cheaper than 29648 one-hot full-matrix placements.
     */
    unsigned char words[V40_TOTAL_BYTES];
    unsigned char matrix[QR_V40_PACKED_BYTES];
    unsigned int plane, bit, mask;
    printf("{\"planes\":[");
    v40_build_functions(matrix);
    for (plane = 0; plane < 15; ++plane) {
        memset(words, 0, sizeof(words));
        for (bit = 0; bit < V40_TOTAL_BYTES * 8u; ++bit)
            if (((bit + 1u) >> plane) & 1u)
                words[bit >> 3] |= (unsigned char)(0x80u >> (bit & 7u));
        v40_place_codewords(words, matrix, 0);
        if (plane) putchar(',');
        logical(matrix);
    }
    printf("],\"zero_masks\":[");
    memset(words, 0, sizeof(words));
    for (mask = 0; mask < 8; ++mask) {
        v40_place_candidate(words, matrix, mask);
        if (mask) putchar(',');
        logical(matrix);
    }
    puts("]}");
}

static void inspect(const unsigned char *payload, unsigned int length)
{
    struct { unsigned char before, data[V40_DATA_BYTES], after; } data;
    struct { unsigned char before, words[V40_TOTAL_BYTES], after; } words;
    struct { unsigned char before, matrix[QR_V40_PACKED_BYTES], after; } output;
    unsigned char candidate[QR_V40_PACKED_BYTES];
    unsigned int block, mask, best = 0;
    unsigned long best_score = 0;
    memset(&data, 0xA5, sizeof(data));
    memset(&words, 0xA5, sizeof(words));
    memset(&output, 0xA5, sizeof(output));
    assert(qr_encode_v40l_bytes(payload, length, output.matrix) == QR_OK);
    v40_build_data(payload, length, data.data);
    v40_interleave(data.data, words.words);
    assert(memcmp(v40_data, data.data, sizeof(data.data)) == 0);
    assert(memcmp(v40_interleaved, words.words, sizeof(words.words)) == 0);
    assert(data.before == 0xA5 && data.after == 0xA5);
    assert(words.before == 0xA5 && words.after == 0xA5);
    assert(output.before == 0xA5 && output.after == 0xA5);
    assert((output.matrix[QR_V40_PACKED_BYTES - 1] & 0x7Fu) == 0);
    printf("{\"data\":"); hex(data.data, sizeof(data.data));
    printf(",\"blocks\":[");
    for (block = 0; block < V40_BLOCKS; ++block) {
        unsigned int offset = v40_block_offset(block), n = v40_block_length(block);
        if (block) putchar(',');
        printf("{\"offset\":%u,\"data\":", offset); hex(data.data + offset, n);
        v40_build_block_ecc(data.data + offset, n);
        printf(",\"ecc\":"); hex(v40_rs + n, V40_ECC_BYTES); putchar('}');
    }
    printf("],\"interleaved\":"); hex(words.words, sizeof(words.words));
    v40_build_functions(candidate);
    printf(",\"reserved\":"); logical(v40_reserved);
    printf(",\"reserved_packed\":"); hex(v40_reserved, sizeof(v40_reserved));
    printf(",\"functions\":"); logical(candidate);
    printf(",\"candidates\":[");
    for (mask = 0; mask < 8; ++mask) {
        unsigned long score;
        v40_place_candidate(words.words, candidate, mask);
        if (mask) putchar(',');
        printf("{\"score\":"); score = score_json(candidate);
        printf(",\"matrix\":"); logical(candidate); putchar('}');
        if (mask == 0 || score < best_score) { best = mask; best_score = score; }
    }
    printf("],\"selected\":%u,\"packed\":", best);
    hex(output.matrix, sizeof(output.matrix));
    printf(",\"logical\":"); logical(v40_matrix);
    puts("}");
}

int main(int argc, char **argv)
{
    unsigned char payload[2956], saved[2956];
    unsigned int length, i;
    assert(argc >= 2);
    if (strcmp(argv[1], "invalid") == 0) { invalid(); return 0; }
    if (strcmp(argv[1], "placement") == 0) { placement(); return 0; }
    if (strcmp(argv[1], "constants") == 0) {
        printf("{\"generator\":"); hex(rs_generator_v40, V40_ECC_BYTES);
        printf(",\"version\":%lu,\"formats\":[", v40_version_bits());
        for (i = 0; i < 8; ++i) printf("%s%u", i ? "," : "", format_bits(i, ECC_FORMAT_L));
        puts("]}"); return 0;
    }
    if (strcmp(argv[1], "balance") == 0) {
        putchar('[');
        for (i = 0; i <= 31329; ++i) printf("%s%lu", i ? "," : "", v40_balance_penalty(i));
        puts("]"); return 0;
    }
    assert(argc == 3);
    if (strcmp(argv[1], "penalty") == 0) {
        unsigned char matrix[QR_V40_PACKED_BYTES] = {0};
        assert(strlen(argv[2]) == QR_V40_SIZE * QR_V40_SIZE);
        for (i = 0; i < QR_V40_SIZE * QR_V40_SIZE; ++i) {
            assert(argv[2][i] == '0' || argv[2][i] == '1');
            v40_put(matrix, i, (unsigned char)(argv[2][i] - '0'));
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
        assert(length == 118 || length == 119);
        exact = malloc(length);
        assert(exact != NULL);
        memcpy(exact, payload, length);
        v40_build_block_ecc(exact, length);
        assert(memcmp(exact, payload, length) == 0);
        hex(v40_rs + length, V40_ECC_BYTES); putchar('\n');
        free(exact);
    } else if (strcmp(argv[1], "interleave") == 0) {
        unsigned char words[V40_TOTAL_BYTES];
        assert(length == V40_DATA_BYTES);
        v40_interleave(payload, words);
        hex(words, sizeof(words)); putchar('\n');
    } else {
        unsigned char *exact;
        assert(length >= 1 && length <= 2953);
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
