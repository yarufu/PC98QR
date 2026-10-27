#ifndef PC98QR_FRAME_V2_H
#define PC98QR_FRAME_V2_H

#include "filename_v2.h"

#define PC98QR_FRAME_V2_VERSION 2U
#define PC98QR_FRAME_V2_HEADER_SIZE 9U
#define PC98QR_FRAME_V2_MAX 2953U
#define PC98QR_FRAME_V2_PAYLOAD_MAX 2944UL
#define PC98QR_FRAME_V2_COUNT_MAX 65535U

enum pc98qr_frame_v2_result {
    PC98QR_FRAME_V2_OK = 0,
    PC98QR_FRAME_V2_ERROR_ARGUMENT,
    PC98QR_FRAME_V2_ERROR_FILENAME,
    PC98QR_FRAME_V2_ERROR_SIZE,
    PC98QR_FRAME_V2_ERROR_INDEX,
    PC98QR_FRAME_V2_ERROR_LENGTH,
    PC98QR_FRAME_V2_ERROR_CAPACITY
};

struct pc98qr_frame_v2_layout {
    unsigned long file_offset;
    unsigned short frame_count;
    unsigned int file_capacity;
    unsigned int file_length;
    unsigned int payload_offset; /* In complete frame, AFTER filename metadata. */ /* 完全なフレーム内で、ファイル名メタデータを除いた後の位置。 */
    unsigned int frame_length;
};

/*
 * v2 APIはC99 small modelのnearポインタを使用し、I/O・QR・heap・静的作業領域を持たない。
 * 入力は保持し、全検証後に出力する。
 * 長さだけの計算では名前の内容は検証しない。
 * 数学的上限は65535×2944−(1+名前長)で、1474560を固定上限とせず、実用転送サイズも保証しない。
 */
/* All v2 APIs: ordinary near pointers, C99 small model, no heap, I/O, QR,
 * graphics, recursion, floating point, static workspace or retained pointers.
 * Inputs are unchanged; outputs are published only after every check passes.
 * Any supplied non-NULL pointer must address the documented valid storage.
 * Length-only arithmetic helpers do not validate actual filename bytes.
 * 1474560 is NOT a production size limit. Mathematical maximum file size is
 * 65535*2944-(1+filename_length), not a practical transfer-size guarantee.
 */

/*
 * 名前長1～12と非空サイズ、1～65535フレームの範囲、出力ポインタを検証する。
 * sizeへ直接加算せず切上げ数を求め、失敗時は出力を保持する。
 */
/* Check filename_length 1..12, nonempty size fitting 1..65535 frames, then
 * non-NULL output. Computes ceil((size+1+N)/2944) WITHOUT adding to size.
 * Every error leaves *frame_count_out untouched.
 */
int pc98qr_frame_v2_count_for_size(unsigned long file_size,
                                 unsigned int filename_length,
                                 unsigned short *frame_count_out);

/*
 * M=1+名前長。
 * 先頭は位置0・容量2944−M・データ開始9+M、後続は位置index×2944−M・容量2944・開始9。
 * 実データ長は残量と容量の小さい方で、全長は2953以下。
 * 乗算前に型を広げ、失敗時は構造体全体を保持する。
 */
/* Same length/size checks, then index < computed count, then non-NULL output.
 * M=1+N: index 0 has offset 0, capacity 2944-M, payload_offset 9+M;
 * others have offset index*2944-M, capacity 2944, payload_offset 9.
 * file_length=min(capacity,size-offset), always 1..capacity on success;
 * frame_length=payload_offset+file_length, always <=2953. No padding.
 * Multiplication is widened before use. Errors leave the entire output alone.
 */
int pc98qr_frame_v2_layout_for_index(unsigned long file_size,
                                   unsigned int filename_length,
                                   unsigned short frame_index,
                                   struct pc98qr_frame_v2_layout *layout_out);

/*
 * v1ヘルパーを流用せず完全なv2フレームを直列化する。
 * 名前は全indexで検証し、chunk長は計算結果と一致が必要。
 * データの出所や内容までは検証しない。
 * 成功時だけ正確な全長を書き、末尾や終端を補わない。
 * 出力は入力と長さ出力に重ねず、失敗時は出力と長さを保持する。
 * 同じ出力領域の逐次再利用は可能だが、大きな領域はDOSスタックに置かない。
 */
/* Serialize a complete v2 frame, with NO v1 helper calls or byte patching.
 * filename is canonical uppercase metadata, explicit length, no terminator;
 * validate it even for nonzero index. file_chunk must be the original bytes
 * at the layout's file_offset, and file_chunk_length MUST equal file_length.
 * The helper validates length, not the provenance/content of caller's chunk.
 * Wire: P98Q,02,index BE16,count BE16; index 0 adds N:u8,name[N]; then raw bytes.
 * Success writes exactly frame_length bytes and sets *length_out. No padding
 * or terminator; larger out capacities are accepted and the tail is untouched.
 *
 * Check order: all pointers non-NULL; canonical filename; size/index layout;
 * chunk length; output capacity. Every error leaves out AND length_out intact.
 * Caller guarantees out has out_capacity bytes and does not overlap filename,
 * file_chunk or length_out; length_out is disjoint from both input ranges.
 * In-place/overlapping serialization is not supported. Sequential reuse of
 * the same output buffer IS supported. Keep large caller buffers off DOS stack.
 */
int pc98qr_frame_v2_serialize(const unsigned char *filename,
                            unsigned int filename_length,
                            unsigned long file_size, unsigned short frame_index,
                            const unsigned char *file_chunk,
                            unsigned int file_chunk_length,
                            unsigned char *out, unsigned int out_capacity,
                            unsigned int *length_out);

#endif
