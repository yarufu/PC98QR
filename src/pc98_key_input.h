#ifndef PC98QR_KEY_INPUT_H
#define PC98QR_KEY_INPUT_H

#include "manual_selection.h" /* Logical events; no state-machine calls here. */ /* 論理イベントの定義を参照するが、ここでは状態機械を呼ばない。 */

enum pc98qr_key_result {
    PC98QR_KEY_EMPTY = 0,
    PC98QR_KEY_READY,
    PC98QR_KEY_ERROR_ARGUMENT,
    PC98QR_KEY_ERROR_STATUS
};

/*
 * INT 18h AH=01hのBHが0なら未入力、1ならAH=00hで1キー取得する。
 * それ以外は待機せずエラー。
 * READY時だけ、先読み値ではなく消費したAXを返す。
 * NULLはBIOS前に拒否する。
 * 入力破棄や再初期化はせず、確認と取得の間に別の読み手がいない前提で、不可分操作ではない。
 */
/* INT 18h AH=01h: BH=0 -> EMPTY; BH=1 -> consume one key using AH=00h.
 * Any other BH -> ERROR_STATUS without a blocking read. Write ax_out only
 * on READY, using the consumed AX, not the earlier peek. NULL is rejected
 * before BIOS access. No flush, AH=04h, timer, controller I/O or reinit.
 * Assumes no competing consumer between peek/read; not an atomic operation.
 */
int pc98qr_key_poll(unsigned short *ax_out);

/*
 * 消費済みAXを分類するだけ。
 * Enter・Backspace・Escは完全一致で、それ以外はALのASCII数字を認める。
 * テンキーのAH差は許し、物理キー解放・リピート・NumLockは解釈しない。
 */
/* Pure classification of consumed AX. Exact Enter/BS/Esc, otherwise ASCII
 * digit AL (including the keypad's different AH); everything else is IGNORE.
 * No interpretation of physical release, repetition, or NumLock state.
 */
unsigned int pc98qr_key_classify(unsigned short ax);

#endif
