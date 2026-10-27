#ifndef PC98QR_FILE_SIZE_H
#define PC98QR_FILE_SIZE_H

enum pc98qr_file_size_result {
    PC98QR_FILE_SIZE_OK = 0,
    PC98QR_FILE_SIZE_ERROR_ARGUMENT,
    PC98QR_FILE_SIZE_ERROR_OPEN,
    PC98QR_FILE_SIZE_ERROR_SEEK,
    PC98QR_FILE_SIZE_ERROR_CLOSE
};

/*
 * 読み取り専用でopenし、末尾へseekしてcloseする。
 * NULLや空パスはI/O前に拒否し、空ファイルは0で成功する。
 * seekとcloseの成功後だけ出力を更新する。
 * open後のcloseは一度で、seekエラーを優先する。
 * 失敗箇所はerrnoではなく戻り値で判断する。
 * 内容読み取りや転送サイズ制限は扱わない。
 */
/* Size of an ordinary binary file: open read-only, seek to end, then close.
 * Reject NULL/empty path or NULL size_out before I/O. Empty files succeed (0).
 * Write size_out only after seek AND close succeed; errors leave it untouched.
 * After a successful open, always attempt close once, preserving a seek error.
 * The result code, not errno after cleanup, identifies the failing operation.
 * Size must be nonnegative and representable by the runtime's signed off_t
 * (32-bit on ia16); unsigned long is 32-bit on ia16. No transport size limit.
 * No content reads, caller-owned fd, position restore, heap or static buffer.
 */
int pc98qr_file_size(const char *path, unsigned long *size_out);

#endif
