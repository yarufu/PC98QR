/*
 * 実main・符号化器・描画をホストの通常画面領域で実行する。
 * 配置と開始/終了を検証するもので、PC-98のBIOSやVRAM動作の試験ではない。
 */
/* Run the real main/encoder/draw_qr against an ordinary host framebuffer.
 * This checks placement and lifecycle, not PC-98 BIOS or GVRAM behavior.
 */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../src/qr_encode.h"
#include "../src/frame_header.h"
#include "../src/file_size.h"
#include "../src/frame_count.h"
#include "../src/frame_offset.h"
#include "../src/file_chunk.h"
#include "../src/manual_transfer.h"

/*
 * 従来の描画試験から対話制御部へ入ってはならない。
 */
/* Legacy rendering tests must never enter the interactive controller. */
enum pc98qr_transfer_result pc98qr_manual_transfer_v2(
    const char *path, unsigned char *buffer, unsigned int capacity,
    unsigned char *output, unsigned int output_capacity)
{
    (void)path; (void)buffer; (void)capacity;
    (void)output; (void)output_capacity;
    assert(!"legacy CLI called manual transfer");
    return PC98QR_TRANSFER_ERROR_ARGUMENT;
}
static int observed_file_size(const char *path, unsigned long *size);
static int observed_frame_count(unsigned long size, unsigned short *count);
static int observed_frame_offset(unsigned short index, unsigned short count,
                                  unsigned long *offset);
static int observed_chunk(const char *path, unsigned long offset,
                           unsigned char *buffer, unsigned int capacity,
                           unsigned int *length);
#define pc98qr_file_size observed_file_size
#define pc98qr_frame_count_for_size observed_frame_count
#define pc98qr_frame_offset_for_index observed_frame_offset
#define pc98qr_read_file_chunk observed_chunk
#define pc98qr_frame_header_serialize observed_header_serialize
static int observed_header_serialize(unsigned short index, unsigned short count,
                                     unsigned char out[PC98QR_FRAME_HEADER_SIZE]);
#define qr_encode_v1q_alnum observed_alnum
#define qr_encode_v1q_bytes observed_bytes
#define qr_encode_v2q_bytes observed_v2_bytes
#define qr_encode_v2m_bytes observed_v2m_bytes
#define qr_encode_v25l_bytes observed_v25_bytes
#define qr_encode_v40l_bytes observed_v40_bytes
int observed_alnum(const char *text, unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE]);
int observed_bytes(const unsigned char *data, unsigned int length,
                   unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE]);
int observed_v2_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE]);
int observed_v2m_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE]);
int observed_v25_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V25_PACKED_BYTES]);
int observed_v40_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V40_PACKED_BYTES]);
static int observed_open(const char *path, int flags, ...);
static ssize_t observed_read(int fd, void *buffer, size_t count);
static int observed_close(int fd);
static off_t observed_lseek(int fd, off_t offset, int whence);
#define open observed_open
#define read observed_read
#define close observed_close
#define lseek observed_lseek
#define main qrtest_main
#include "../src/main.c"
#undef main
#undef pc98qr_file_size
#undef pc98qr_frame_count_for_size
#undef pc98qr_frame_offset_for_index
#undef pc98qr_read_file_chunk
/*
 * 本番ヘルパーに、従来mainと同じホスト専用システムコール観測を組み合わせる。
 * 本番ソース自体には試験フックを入れない。
 */
/* Real helpers with the same host-only syscall observers as legacy main.
 * The production sources themselves contain no testing hooks.
 */
#include "../src/file_size.c"
#include "../src/file_chunk.c"
#undef pc98qr_frame_header_serialize
#undef qr_encode_v1q_alnum
#undef qr_encode_v1q_bytes
#undef qr_encode_v2q_bytes
#undef qr_encode_v2m_bytes
#undef qr_encode_v25l_bytes
#undef qr_encode_v40l_bytes
#undef open
#undef read
#undef close
#undef lseek

static struct {
    unsigned char before[32];
    unsigned char pixels[SCREEN_HEIGHT][SCREEN_WIDTH];
    unsigned char after[32];
} guarded_frame;
#define frame guarded_frame.pixels
static unsigned int observed_size;
static int stage;
static unsigned int alnum_calls;
static unsigned int byte_calls;
static int file_mode;
static int transport_mode;
static int selected_mode;
static int io_phase; /* 1: size, 2: selected chunk, 0: legacy */ /* 1はサイズ取得、2は選択chunk、0は従来経路。 */
static char order[80];
static unsigned int size_calls, count_calls, offset_calls, chunk_calls;
static unsigned int seek_calls, read_calls, successful_opens;
static unsigned long initial_size, selected_offset;
static unsigned short selected_index, selected_count;
static unsigned int payload_length;
static int chunk_ok;
static const char *io_fault;
static size_t file_capacity;
static unsigned int header_calls;
static int header_error;
static const char *expected_path;
static unsigned int open_calls;
static unsigned int close_calls;
static int file_opened;
static int file_fd = -1;
static unsigned char *file_buffer_start;
static unsigned char file_bytes[QR_V40L_BYTE_MAX + 1];
static size_t file_total;
static int read_eof;
static int read_failed;
static int close_succeeded;
static const char *read_plan;
static int close_error;
static int encoder_error;

static void record(const char *name)
{
    assert(strlen(order) + strlen(name) + 2 < sizeof(order));
    if (order[0] != '\0')
        strcat(order, ",");
    strcat(order, name);
}

static int fault(const char *name)
{
    return io_fault != NULL && strcmp(io_fault, name) == 0;
}

static int observed_file_size(const char *path, unsigned long *size)
{
    int result;
    assert(selected_mode && stage == 0 && order[0] == '\0');
    record("size");
    ++size_calls;
    io_phase = 1;
    result = pc98qr_file_size(path, size);
    if (result == PC98QR_FILE_SIZE_OK) {
        const char *resize = getenv("PC98QR_TEST_RESIZE");
        initial_size = *size;
        if (resize != NULL) {
            int fd = open(path, O_WRONLY);
            assert(fd >= 0);
            assert(ftruncate(fd, (off_t)strtoul(resize, NULL, 10)) == 0);
            assert(close(fd) == 0);
        }
    }
    io_phase = 0;
    return result;
}

static int observed_frame_count(unsigned long size, unsigned short *count)
{
    int result;
    assert(strcmp(order, "size") == 0 && size == initial_size);
    record("count");
    ++count_calls;
    result = pc98qr_frame_count_for_size(size, count);
    if (result == PC98QR_FRAME_COUNT_OK)
        selected_count = *count;
    return result;
}

static int observed_frame_offset(unsigned short index, unsigned short count,
                                  unsigned long *offset)
{
    int result;
    assert(strcmp(order, "size,count") == 0 && count == selected_count);
    record("offset");
    ++offset_calls;
    selected_index = index;
    result = pc98qr_frame_offset_for_index(index, count, offset);
    if (result == PC98QR_FRAME_OFFSET_OK)
        selected_offset = *offset;
    return result;
}

static int observed_chunk(const char *path, unsigned long offset,
                           unsigned char *buffer, unsigned int capacity,
                           unsigned int *length)
{
    int result;
    assert(strcmp(order, "size,count,offset") == 0);
    assert(offset == selected_offset && capacity == 2944 && stage == 0);
    record("chunk");
    ++chunk_calls;
    io_phase = 2;
    file_buffer_start = buffer;
    result = pc98qr_read_file_chunk(path, offset, buffer, capacity, length);
    chunk_ok = result == PC98QR_FILE_CHUNK_OK;
    if (chunk_ok)
        payload_length = *length;
    io_phase = 0;
    return result;
}

/*
 * 通常は実ファイルを使うホストラッパー。
 * 読み取り計画で回数ごとの長さを制限し、一部取得後のエラーも注入できる。
 * close失敗を模擬しても実記述子は閉じる。
 */
/* Host-only wrappers: real files by default. A read plan such as "5,6,0"
 * caps real reads, verifies their results, and can inject -1 after a prefix.
 * A simulated close failure still closes the real host descriptor.
 */
static int observed_open(const char *path, int flags, ...)
{
    int expected_flags = O_RDONLY;
#ifdef O_BINARY
    expected_flags |= O_BINARY;
#endif
    assert(stage == 0 && file_mode && file_fd == -1);
    assert(selected_mode ? (io_phase == 1 || io_phase == 2) : open_calls == 0);
    assert(path == expected_path && path[0] != '\0');
    assert(flags == expected_flags);
    ++open_calls;
    if ((io_phase == 1 && fault("size_open")) ||
        (io_phase == 2 && fault("chunk_open"))) {
        errno = EIO;
        return -1;
    }
    file_fd = open(path, flags);
    file_opened = file_fd >= 0;
    successful_opens += file_opened;
    return file_fd;
}

static off_t observed_lseek(int fd, off_t offset, int whence)
{
    off_t result;
    assert(selected_mode && stage == 0 && fd == file_fd && file_opened);
    assert(io_phase == 1 || io_phase == 2);
    assert(whence == (io_phase == 1 ? SEEK_END : SEEK_SET));
    assert(offset == (io_phase == 1 ? 0 : (off_t)selected_offset));
    ++seek_calls;
    if ((io_phase == 1 && fault("size_seek")) ||
        (io_phase == 2 && fault("chunk_seek"))) {
        errno = EIO;
        return -1;
    }
    result = lseek(fd, offset, whence);
    return io_phase == 2 && fault("chunk_wrong_seek") ? result + 1 : result;
}

static ssize_t observed_read(int fd, void *buffer, size_t count)
{
    ssize_t actual;
    long planned = -2;
    assert(stage == 0 && file_opened && fd == file_fd);
    assert(selected_mode ? (io_phase == 2 && close_calls == 1) : close_calls == 0);
    ++read_calls;
    assert(!read_eof && !read_failed);
    assert(file_total < sizeof(file_bytes));
    assert(count == file_capacity - file_total);
    if (file_buffer_start == NULL)
        file_buffer_start = buffer;
    assert(buffer == file_buffer_start + file_total);
    if (read_plan != NULL) {
        char *end;
        assert(*read_plan != '\0'); /* Reject unexpected extra reads. */ /* 予期しない追加readを拒否する。 */
        planned = strtol(read_plan, &end, 10);
        assert(end != read_plan && (*end == ',' || *end == '\0'));
        assert(planned >= -1 && planned <= (long)count);
        read_plan = *end == ',' ? end + 1 : end;
        if (planned < 0) {
            read_failed = 1;
            errno = EIO;
            return -1;
        }
        if (planned > 0)
            count = (size_t)planned;
    }
    actual = read(fd, buffer, count);
    if (planned >= 0)
        assert(actual == planned); /* In particular, 0 must be a real EOF. */ /* 特に0は本当のEOFである必要がある。 */
    if (actual > 0) {
        assert((size_t)actual <= count);
        memcpy(file_bytes + file_total, buffer, (size_t)actual);
        file_total += (size_t)actual;
    } else if (actual == 0) {
        read_eof = 1;
    } else {
        read_failed = 1;
    }
    return actual;
}

static int observed_close(int fd)
{
    int result;
    assert(stage == 0 && file_opened && fd == file_fd);
    assert(close_calls + 1 == successful_opens);
    ++close_calls;
    result = close(fd);
    file_fd = -1;
    if (close_error || (io_phase == 1 && fault("size_close")) ||
        (io_phase == 2 && fault("chunk_close"))) {
        assert(result == 0);
        errno = EIO;
        result = -1;
    }
    close_succeeded = result == 0;
    return result;
}

static int observed_header_serialize(unsigned short index, unsigned short count,
                                     unsigned char out[PC98QR_FRAME_HEADER_SIZE])
{
    assert(stage == 0 && transport_mode && header_calls == 0);
    assert(close_calls == (selected_mode ? 2u : 1u) && close_succeeded && file_fd == -1);
    assert(!read_failed && file_total >= 1 && file_total <= 2944);
    assert(out + 9 == file_buffer_start);
    if (selected_mode) {
        unsigned long remaining = initial_size - selected_offset;
        assert(strcmp(order, "size,count,offset,chunk") == 0 && chunk_ok);
        assert(payload_length == (remaining > 2944 ? 2944 : remaining));
        assert(index == selected_index && count == selected_count);
        record("header");
    } else {
        assert(read_eof && index == 0 && count == 1);
    }
    ++header_calls;
    if (header_error)
        return PC98QR_FRAME_HEADER_ERROR_ARGUMENT;
    return pc98qr_frame_header_serialize(index, count, out);
}

int observed_alnum(const char *text, unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    assert(stage == 0);
    ++alnum_calls;
    observed_size = QR_V1_SIZE;
    return qr_encode_v1q_alnum(text, matrix);
}

static void observe_bytes(const unsigned char *data, unsigned int length)
{
    assert(stage == 0);
    ++byte_calls;
    if (file_mode) {
        assert(close_calls == (selected_mode ? 2u : 1u) && close_succeeded && file_fd == -1);
        assert((selected_mode || read_eof) && !read_failed);
        if (transport_mode) {
            static const unsigned char expected_header[9] = {
                0x50, 0x39, 0x38, 0x51, 0x01, 0x00, 0x00, 0x00, 0x01
            };
            assert(header_calls == 1 && !header_error);
            assert(length == 9 + file_total && data + 9 == file_buffer_start);
            if (selected_mode) {
                assert(strcmp(order, "size,count,offset,chunk,header") == 0);
                record("encoder");
            } else {
                assert(memcmp(data, expected_header, 9) == 0);
            }
            assert(memcmp(data + 9, file_bytes, file_total) == 0);
            assert(observed_size == QR_V40_SIZE);
        } else {
            assert(length == file_total);
            assert(memcmp(data, file_bytes, length) == 0);
        }
    }
}

int observed_bytes(const unsigned char *data, unsigned int length,
                   unsigned char matrix[QR_V1_SIZE][QR_V1_SIZE])
{
    assert(length >= 1 && length <= QR_V1Q_BYTE_MAX);
    observed_size = QR_V1_SIZE;
    observe_bytes(data, length);
    if (encoder_error)
        return QR_ERROR_LENGTH;
    return qr_encode_v1q_bytes(data, length, matrix);
}

int observed_v2_bytes(const unsigned char *data, unsigned int length,
                      unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE])
{
    /*
     * 公開V2 APIは短いデータも受けるが、mainはその長さではV1を選ばなければならない。
     */
    /* The public V2 API accepts shorter data, but main must choose V1 for it. */
    assert(length > QR_V1Q_BYTE_MAX && length <= QR_V2Q_BYTE_MAX);
    observed_size = QR_V2_SIZE;
    observe_bytes(data, length);
    if (encoder_error)
        return QR_ERROR_LENGTH;
    return qr_encode_v2q_bytes(data, length, matrix);
}

int observed_v2m_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V2_SIZE][QR_V2_SIZE])
{
    /*
     * CLIの振分け範囲は固定プロファイルの公開APIより狭い。
     */
    /* CLI routing is narrower than the fixed-profile public API. */
    assert(length > QR_V2Q_BYTE_MAX && length <= QR_V2M_BYTE_MAX);
    observed_size = QR_V2_SIZE;
    observe_bytes(data, length);
    if (encoder_error)
        return QR_ERROR_LENGTH;
    return qr_encode_v2m_bytes(data, length, matrix);
}

int observed_v25_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V25_PACKED_BYTES])
{
    assert(file_mode); /* /B must never reach V25. */ /* /BはV25へ到達してはならない。 */
    assert(length > QR_V2M_BYTE_MAX && length <= QR_V25L_BYTE_MAX);
    observed_size = QR_V25_SIZE;
    observe_bytes(data, length);
    if (encoder_error)
        return QR_ERROR_LENGTH;
    return qr_encode_v25l_bytes(data, length, matrix);
}

int observed_v40_bytes(const unsigned char *data, unsigned int length,
                       unsigned char matrix[QR_V40_PACKED_BYTES])
{
    assert(file_mode); /* /B must never reach V40. */ /* /BはV40へ到達してはならない。 */
    if (transport_mode)
        assert(length >= 10 && length <= 2953);
    else
        assert(length > QR_V25L_BYTE_MAX && length <= QR_V40L_BYTE_MAX);
    observed_size = QR_V40_SIZE;
    observe_bytes(data, length);
    if (encoder_error)
        return QR_ERROR_LENGTH;
    return qr_encode_v40l_bytes(data, length, matrix);
}

void graphics_init(void)
{
    assert(stage == 0);
    assert(alnum_calls + byte_calls == 1 && !encoder_error);
    if (selected_mode) {
        assert(strcmp(order, "size,count,offset,chunk,header,encoder") == 0);
        record("graphics");
    }
    stage = 1;
}

void screen_clear(void)
{
    assert(stage == 1);
    memset(frame, 255, sizeof(frame));
    stage = 2;
}

void box_fill(unsigned int x, unsigned int y,
              unsigned int width, unsigned int height)
{
    unsigned int row;
    unsigned int col;

    assert(stage == 2);
    assert(width == (observed_size == QR_V40_SIZE ? 2u
                     : observed_size == QR_V25_SIZE ? 3u : 12u));
    assert(height == width);
    assert(x < SCREEN_WIDTH && y < SCREEN_HEIGHT);
    assert(x + width <= SCREEN_WIDTH && y + height <= SCREEN_HEIGHT);
    for (row = y; row < y + height; ++row) {
        for (col = x; col < x + width; ++col) {
            assert(frame[row][col] == 255); /* Modules never overlap. */ /* モジュールの画素領域は重ならない。 */
            frame[row][col] = 0;
        }
    }
}

void graphics_show(void)
{
    assert(stage == 2);
    stage = 3;
}

void wait_key(void)
{
    assert(stage == 3);
    stage = 4;
}

void graphics_shutdown(void)
{
    assert(stage == 4);
    stage = 5;
}

static void check_frame(void)
{
    unsigned int x, y;
    unsigned int scale = observed_size == QR_V40_SIZE ? 2u
                         : observed_size == QR_V25_SIZE ? 3u : 12u;
    unsigned int body_x = observed_size == QR_V40_SIZE ? 143u
                          : observed_size == QR_V25_SIZE ? 144u
                          : observed_size == QR_V1_SIZE ? 194u : 170u;
    unsigned int body_y = observed_size == QR_V40_SIZE ? 23u
                          : observed_size == QR_V25_SIZE ? 24u
                          : observed_size == QR_V1_SIZE ? 74u : 50u;
    const unsigned char *cells = observed_size == QR_V1_SIZE
                                ? &matrix.v1[0][0] : &matrix.v2[0][0];

    /*
     * 独立した原点定数と全画素を使い、白セル・余白・最終セル・2×2/3×3/12×12の正確な被覆を検査する。
     */
    /* Independent literal origins plus every pixel: white cells, quiet zone,
     * margins, last cell, and exact 2x2/3x3/12x12 coverage all count. */
    for (y = 0; y < SCREEN_HEIGHT; ++y) {
        for (x = 0; x < SCREEN_WIDTH; ++x) {
            unsigned char expected = 255;
            if (x >= body_x && x < body_x + observed_size * scale &&
                y >= body_y && y < body_y + observed_size * scale) {
                unsigned int index = (y - body_y) / scale * observed_size
                                   + (x - body_x) / scale;
                int black = observed_size == QR_V40_SIZE
                            ? ((matrix.v40[index / 8u] >> (7u - index % 8u)) & 1u)
                            : observed_size == QR_V25_SIZE
                            ? ((matrix.v25[index / 8u] >> (7u - index % 8u)) & 1u)
                            : cells[index];
                if (black)
                    expected = 0;
            }
            assert(frame[y][x] == expected);
        }
    }
}

int main(int argc, char **argv)
{
    int result;
    unsigned int i;
    memset(&guarded_frame, 0xA5, sizeof(guarded_frame));
    selected_mode = argc == 4 && strcmp(argv[1], "/T") == 0;
    transport_mode = selected_mode || (argc == 3 && strcmp(argv[1], "/T") == 0);
    file_mode = transport_mode || (argc == 3 && strcmp(argv[1], "/F") == 0);
    file_capacity = selected_mode ? 2944 : transport_mode ? 2945 : sizeof(file_bytes);
    expected_path = selected_mode ? argv[3] : file_mode ? argv[2] : NULL;
    io_fault = getenv("PC98QR_TEST_IO_FAULT");
    read_plan = getenv("PC98QR_TEST_READ_PLAN");
    close_error = getenv("PC98QR_TEST_CLOSE_ERROR") != NULL;
    encoder_error = getenv("PC98QR_TEST_ENCODER_ERROR") != NULL;
    header_error = getenv("PC98QR_TEST_HEADER_ERROR") != NULL;
    result = qrtest_main(argc, argv);
    for (i = 0; i < sizeof(guarded_frame.before); ++i) {
        assert(guarded_frame.before[i] == 0xA5);
        assert(guarded_frame.after[i] == 0xA5);
    }
    if (!selected_mode) {
        assert(open_calls == (unsigned int)(file_mode && expected_path[0] != '\0'));
        assert(header_calls == (unsigned int)(transport_mode && read_eof &&
               !read_failed && close_succeeded && file_total >= 1 && file_total <= 2944));
    }
    assert(close_calls == successful_opens && file_fd == -1);
    if (getenv("PC98QR_TEST_TRACE") != NULL) {
        FILE *trace = fopen(getenv("PC98QR_TEST_TRACE"), "w");
        assert(trace != NULL);
        fprintf(trace, "{\"order\":\"%s\",\"size\":%lu,\"count\":%u,"
                "\"index\":%u,\"offset\":%lu,\"payload\":%u,"
                "\"calls\":[%u,%u,%u,%u,%u,%u,%d],"
                "\"io\":[%u,%u,%u,%u]}\n", order, initial_size,
                (unsigned int)selected_count, (unsigned int)selected_index,
                selected_offset, payload_length, size_calls, count_calls,
                offset_calls, chunk_calls, header_calls, byte_calls, stage,
                open_calls, seek_calls, read_calls, close_calls);
        assert(fclose(trace) == 0);
    }
    if (read_plan != NULL && file_opened)
        assert(*read_plan == '\0');
    if (result != 0) {
        assert(stage == 0); /* Invalid input must never initialize graphics. */ /* 不正入力でグラフィックを初期化してはならない。 */
        if (encoder_error && byte_calls != 0)
            assert(alnum_calls == 0 && byte_calls == 1);
        else if (argc != 2) /* File, HEX and usage errors precede encoding. */ /* ファイル・HEX・usageのエラーは符号化より前に処理する。 */
            assert(alnum_calls == 0 && byte_calls == 0);
        return result;
    }
    assert(stage == 5);
    assert(alnum_calls == (argc == 2) && byte_calls == (argc == 3 || selected_mode));
    check_frame();
    printf("P5\n%d %d\n255\n", SCREEN_WIDTH, SCREEN_HEIGHT);
    return fwrite(frame, 1, sizeof(frame), stdout) == sizeof(frame) ? 0 : 1;
}
