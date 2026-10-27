/*
 * QRTEST本番とは意図的に分離した、DOSの単独サイズ確認プログラム。
 */
/* Standalone DOS size probe; deliberately separate from QRTEST production. */
#include <stddef.h>
#include <unistd.h>
#include "file_size.h"

static int write_all(const char *text, size_t length)
{
    while (length != 0) {
        ssize_t written = write(STDOUT_FILENO, text, length);
        if (written <= 0)
            return 0;
        text += written;
        length -= (size_t)written;
    }
    return 1;
}

static int report_error(int result)
{
    /*
     * ヘルパーのエラーコードは1～4の1桁。
     */
    /* The helper's error codes are the single digits 1 through 4. */
    char message[] = "ERROR=0\r\n";
    message[6] = (char)('0' + result);
    write_all(message, sizeof(message) - 1);
    return 1;
}

static int report_size(unsigned long size)
{
    static const char digits[] = "0123456789ABCDEF";
    /*
     * 型の1バイトあたり十進3桁を確保すれば、ia16とホストのunsigned longを表せる。
     */
    /* Three decimal digits per byte cover unsigned long on ia16 and host. */
    char decimal[3 * sizeof(unsigned long)];
    char hex[8];
    char *end = decimal + sizeof(decimal);
    char *first = end;
    unsigned long value = size;
    unsigned int i;

    do {
        *--first = (char)('0' + value % 10UL);
        value /= 10UL;
    } while (value != 0);

    value = size;
    for (i = sizeof(hex); i != 0; --i) {
        hex[i - 1] = digits[value & 15UL];
        value >>= 4;
    }

    return write_all("SIZE=", 5) &&
           write_all(first, (size_t)(end - first)) &&
           write_all("\r\nHEX=", 6) &&
           write_all(hex, sizeof(hex)) &&
           write_all("\r\n", 2) ? 0 : 1;
}

int main(int argc, char **argv)
{
    unsigned long size;
    int result;

    if (argc != 2)
        return report_error(PC98QR_FILE_SIZE_ERROR_ARGUMENT);
    result = pc98qr_file_size(argv[1], &size);
    if (result != PC98QR_FILE_SIZE_OK)
        return report_error(result);
    return report_size(size);
}
