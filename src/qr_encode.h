#ifndef PC98QR_ENCODE_H
#define PC98QR_ENCODE_H

#define QR_V1_SIZE 21
#define QR_V2_SIZE 25
#define QR_V1Q_ALNUM_MAX 16
#define QR_V1Q_BYTE_MAX 11
#define QR_V2Q_BYTE_MAX 20
#define QR_V2M_BYTE_MAX 26
#define QR_V25_SIZE 117
#define QR_V25_PACKED_BYTES 1712
#define QR_V25L_BYTE_MAX 1273
#define QR_V40_SIZE 177
#define QR_V40_PACKED_BYTES 3917
#define QR_V40L_BYTE_MAX 2953

enum qr_result {
    QR_OK = 0,
    QR_ERROR_LENGTH = 1,
    QR_ERROR_CHARACTER = 2
};

/*
 * 以下の全符号化APIに共通の、PC98QRが採用するN3採点契約。
 * ISO/JISの唯一の解釈であるとは主張しない。
 * 各行・各列を同色セルの完全な連続runに分けて評価する。
 * 候補は黒k / 白k / 黒3k / 白k / 黒kの連続5 runで、kは正の整数。
 * 長いrunの一部分だけを切り出した一致は候補にしない。
 * 候補の直前・直後の外側白run長をL/Rとする（行は左/右、列は上/下）。
 * 外側白runがsymbol境界へ達する側、または候補の端の黒runが境界に接して
 * 外側白runが存在しない側は、N3判定上その長さを4*kの仮想白領域として扱う。
 * L >= 4*k && R >= kなら+40、R >= 4*k && L >= kなら+40を独立に加点する。
 * 両方成立なら+80。同じ候補・同じ方向は白runがさらに長くても最大+40。
 * 異なる候補はrunを共有していても個別に数える。
 * 仮想白領域はN3判定専用で、実matrix・Quiet Zone・N1/N2/N4を変更しない。
 * 実Quiet Zoneの4セルを単純に付加する意味ではない。
 * 詳細な設計根拠・判定例・検証記録はdocs/PHASE2B.mdを参照。
 */
/* PC98QR's adopted N3 scoring contract, shared by all encoder APIs below.
 * This is not a claim of a unique ISO/JIS interpretation.
 * Evaluate each row and column as complete runs of same-color cells.
 * A candidate is five consecutive runs: black k / white k / black 3k /
 * white k / black k, where k is a positive integer. A match using only part
 * of a longer run is not a candidate.
 * Let L/R be the immediately preceding/following outer white run lengths
 * (left/right for rows, above/below for columns). If an outer white run
 * reaches the symbol boundary, or is absent because the candidate's end
 * black run touches that boundary, use a virtual white length of 4*k for
 * that side in the N3 checks.
 * Independently add +40 for L >= 4*k && R >= k, and +40 for R >= 4*k && L >= k.
 * Both conditions give +80; longer white runs still give at most +40 per
 * candidate per direction. Count distinct candidates even if they share runs.
 * Virtual white is used only for N3; the actual matrix, Quiet Zone and
 * N1/N2/N4 are unchanged. It does not mean appending four real Quiet Zone cells.
 * See docs/PHASE2B.md for design rationale, examples, and validation history.
 */

/*
 * Model 2・V1-Qの英数字単一セグメント。
 * 1～16文字のNUL終端ASCIIを受け、8マスクの最小点を採用し同点なら小さい番号を選ぶ。
 * N3は上記の共通契約に従う。
 * 行列は呼出し側所有で白0・黒1、入力エラー時は保持する。
 * 静的作業領域のため再入不可で、大きな行列はスタックに置かない。
 */
/* Model 2 / V1 / ECC Q / one Alphanumeric segment / automatic mask 0..7.
 * Lowest N1+N2+N3+N4 score wins; ties select the lowest mask number.
 * N3 follows the shared PC98QR contract above.
 * text: non-NULL, NUL-terminated, 1..16 ASCII characters; no conversion.
 * matrix: non-NULL caller storage, [row][column], 0 = white, 1 = black.
 * On input error matrix is untouched. No heap, I/O or graphics dependencies.
 * Internal static workspace: not reentrant. Keep caller matrix off the DOS stack.
 */
int qr_encode_v1q_alnum(const char *text,
                      unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE]);

/*
 * V1-QのByte単一セグメントでECIなし。
 * 1～11バイトをそのまま扱い、00/1A/80/FFもデータとする。
 * 範囲外の長さは入力・出力に触れずエラー。
 * マスクや静的領域の契約は英数字APIと共通。
 */
/* Model 2 / V1 / ECC Q / one Byte segment / no ECI / automatic mask 0..7.
 * Same mask, matrix and static-workspace contracts as the Alphanumeric API.
 * data: non-NULL binary pointer, length: 1..11 bytes, input is never modified.
 * All byte values (including 00/1A/80/FF) are data; no string termination.
 * Length 0 or >11 returns QR_ERROR_LENGTH before touching data or matrix.
 * No heap, recursion, floating point or I/O.
 */
int qr_encode_v1q_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE]);

/*
 * V2-QのByte単一セグメントで1～20バイト。
 * 長さエラー時は入力と625バイトの行列を保持する。
 * このAPIは常にV2を生成するが、CLIは1～11バイトではV1を選ぶ。
 */
/* Model 2 / V2 / ECC Q / one Byte segment / no ECI / automatic mask 0..7.
 * Same binary, error and shared static-workspace contracts as V1 above.
 * length: 1..20; length 0 or >20 leaves data and all 625 matrix bytes untouched.
 * The CLI continues to use V1 for 1..11 bytes; this API always produces V2.
 */
int qr_encode_v2q_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE]);

/*
 * V2-MのByte単一セグメントで1～26バイト。
 * 範囲外はポインタ参照前にエラーとし行列を保持する。
 * APIは常にV2-Mを生成し、CLIでは21～26バイトに使用する。
 */
/* Model 2 / V2 / ECC M / one Byte segment / no ECI / automatic mask 0..7.
 * Same binary, error and shared static-workspace contracts as V1 above.
 * length: 1..26; length 0 or >26 returns QR_ERROR_LENGTH before pointer access.
 * Input is unchanged; length errors leave all 625 matrix bytes untouched.
 * Always produces V2-M; the CLI uses it only for 21..26 bytes.
 */
int qr_encode_v2m_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE]);

/*
 * V25-Lで1～1273バイトを符号化する。
 * 範囲外は領域に触れずエラー。
 * 正常入力では非NULL・非重複領域が必要。
 * 出力はy×117+xをMSBから詰めたビット列で、黒1・白0、行の埋め草やQuiet Zoneなし。
 * 最終バイトの下位7ビットは0。
 * 専用静的領域を使うため再入不可。
 */
/* Model 2 / V25 / ECC L / one Byte segment / no ECI / automatic mask 0..7.
 * length: 1..1273 raw bytes, input unchanged. Length 0 or >1273 returns
 * QR_ERROR_LENGTH before accessing either pointer; output is untouched.
 * Valid lengths require non-NULL data and matrix, with non-overlapping storage.
 * Unlike V1/V2, output is a bitset: index = y*117+x, byte = index>>3,
 * bit = 0x80 >> (index&7), black=1 / white=0 (MSB first, no row padding).
 * The last byte's unused low seven bits are always zero. No quiet zone.
 * Same minimum-penalty / lowest-mask tie rule and N3 contract as above.
 * No heap, recursion or I/O. Separate static workspace, not reentrant.
 * Keep caller storage off the DOS stack.
 */
int qr_encode_v25l_bytes(const unsigned char *data, unsigned int length,
                        unsigned char matrix[QR_V25_PACKED_BYTES]);

/*
 * V40-Lで1～2953バイトを符号化する。
 * 範囲外は領域に触れずエラー。
 * 正常入力は非NULL・非重複。
 * 出力はy×177+xをMSBから詰め、黒1・白0、最終下位7ビットは0。
 * Quiet Zoneは含めない。
 * マスク評価はV25と同じで専用静的領域を使う。
 * 正式/Fでは1274～2953バイトに使用する。
 */
/* Model 2 / V40 / ECC L / one Byte segment / no ECI / automatic mask 0..7.
 * length: 1..2953 raw bytes, input unchanged. Length 0 or >2953 returns
 * QR_ERROR_LENGTH before accessing either pointer; output is untouched.
 * Valid lengths require non-NULL data and matrix, with non-overlapping storage.
 * Packed output: index = y*177+x, byte = index>>3, bit = 0x80 >> (index&7),
 * black=1 / white=0 (MSB first, no row padding), no quiet zone.
 * The final byte's unused low seven bits are zero. Same mask/N3 rule as V25.
 * No heap, recursion or I/O. Dedicated static workspace, not reentrant.
 * Keep caller storage off the DOS stack.
 * Used by production /F for 1274..2953 bytes since Phase 3D-2.
 */
int qr_encode_v40l_bytes(const unsigned char *data, unsigned int length,
                        unsigned char matrix[QR_V40_PACKED_BYTES]);

#endif
