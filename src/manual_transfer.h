#ifndef PC98QR_MANUAL_TRANSFER_H
#define PC98QR_MANUAL_TRANSFER_H

#include "qr_encode.h" /* Caller buffer capacities; no encoder workspace here. */ /* 呼出し側バッファの容量定義を参照する。符号化用作業領域はここに持たない。 */

/*
 * 処理上の結果でありDOSのERRORLEVELではない。
 * 各実行ファイルが終了コードへ変換する。
 */
/* Semantic results, NOT DOS ERRORLEVELs. Each executable owns its mapping. */
enum pc98qr_transfer_result {
    PC98QR_TRANSFER_FINISHED = 0,
    PC98QR_TRANSFER_CANCELLED,
    PC98QR_TRANSFER_ERROR_ARGUMENT,
    PC98QR_TRANSFER_ERROR_SIZE,
    PC98QR_TRANSFER_ERROR_COUNT,
    PC98QR_TRANSFER_ERROR_INIT,
    PC98QR_TRANSFER_ERROR_SIZE_CHANGED,
    PC98QR_TRANSFER_ERROR_OFFSET,
    PC98QR_TRANSFER_ERROR_READ,
    PC98QR_TRANSFER_ERROR_HEADER,
    PC98QR_TRANSFER_ERROR_ENCODER,
    PC98QR_TRANSFER_ERROR_STATUS,
    PC98QR_TRANSFER_ERROR_KEY,
    PC98QR_TRANSFER_ERROR_INPUT,
    PC98QR_TRANSFER_ERROR_OUTPUT
};

/*
 * DOSテキスト画面から同期的にv1手動転送を行う比較・回帰診断用の入口。
 * I/O前に引数とframe 2953バイト以上・matrix 3917バイト以上を検証する。
 * 両領域とパスは非重複で呼出し中有効とし、大きな領域はスタックに置かない。
 * Sと有効な番号選択時にサイズを再確認するが内容の同一性は保証しない。
 * 画面初期化後の通常終了経路ではshutdownを一度行う。
 * Esc終了は受信完了の証明ではなく、失敗時のバッファ復元や同時符号化も保証しない。
 * 診断は呼出し側が画面終了後に表示する。
 */
/* Synchronous version 01 manual transfer from a normal DOS text console.
 * Retained temporarily for QMAN comparison/regression diagnostics.
 * Validate non-NULL/nonempty path, non-NULL buffers and minimum capacities
 * before any I/O. frame needs >= QR_V40L_BYTE_MAX (2953) writable bytes;
 * matrix needs >= QR_V40_PACKED_BYTES (3917) writable bytes. Caller guarantees
 * these are distinct, non-overlapping objects, also disjoint from path, valid
 * throughout this call. Ordinary near pointers on ia16 small model; no far
 * allocation, heap, retained pointers or internal large buffers. Keep caller
 * storage off the DOS stack. Larger buffers (QRTEST's 2954 bytes) are accepted;
 * bytes beyond the required capacities are never accessed.
 *
 * Display MSTART-style information on stdout (raw write, CR/LF); wait for S/s
 * or Esc. S rechecks size against the displayed size before reading frame 0.
 * Count stays fixed. Each valid frame selection rechecks size again. Helpers
 * independently open/close read-only: unchanged size is NOT file identity or
 * an atomic snapshot. Do not modify the file or exchange media while running.
 *
 * Own init/show/shutdown. Before init, failures/cancellation never shut down;
 * after init, every normal C return path shuts down exactly once, even if the
 * initial status drawing fails before show. Void graphics hardware failures
 * are not detectable. No text writes while graphics is active. No stderr
 * diagnostics here: the caller can report the returned error after cleanup.
 *
 * FINISHED = Esc after QR display; CANCELLED = Esc before starting. Neither
 * proves receiver completion. Failed normal stdout writes return ERROR_OUTPUT,
 * including the cancellation message. S emits no probe-only success message.
 * No flush/debounce: queued events continue into the QR loop, after its first
 * display. Calls are synchronous; Esc waits for the next poll during I/O or
 * encoding/drawing. Buffers may change on failure; no rollback is provided.
 * Existing encoder workspace is not reentrant; no concurrent encoder calls.
 */
enum pc98qr_transfer_result pc98qr_manual_transfer(
    const char *path,
    unsigned char *frame, unsigned int frame_capacity,
    unsigned char *matrix, unsigned int matrix_capacity);

/*
 * QRTEST /M PATH用のv2入口。
 * 容量・非重複・画面終了の契約はv1と共通。
 * I/O前にASCII 8.3パス全体を検証し、元パスで読み取り、終端なしの大文字名を送る。
 * v1への代替処理はない。
 * matrixで読み取り、frameへ直列化してからmatrixへQRを生成する。
 * 失敗時のmatrixはQRとは限らず再描画しない。
 * 以前のQRで復旧する機能を加える際はこの再利用方式を再検討する。
 */
/* Version 02 entry for QRTEST /M PATH; same capacities, disjoint-storage and
 * UI/cleanup contracts as above. Validate the entire ASCII 8.3 path through
 * filename_v2_from_path before any I/O; invalid names return ERROR_ARGUMENT.
 * Keep the original path for DOS I/O, and its canonical filename (<=12 bytes,
 * no terminator) for metadata. No fallback to v1 or filename replacement.
 *
 * matrix first holds raw chunk bytes, then serialize(matrix -> frame) finishes,
 * then encode(frame -> matrix) overwrites it with the complete QR. No new large
 * buffer or encoder-workspace borrowing. On read/serialize/encode failure the
 * matrix may no longer be a QR: exit without redrawing it. No buffer rollback;
 * reconsider this scratch use before adding recovery via the old matrix.
 */
enum pc98qr_transfer_result pc98qr_manual_transfer_v2(
    const char *path,
    unsigned char *frame, unsigned int frame_capacity,
    unsigned char *matrix, unsigned int matrix_capacity);

#endif
