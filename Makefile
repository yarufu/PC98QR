CC := ia16-elf-gcc
PYTHON ?= python3
HOST_CC ?= cc
HOST_CFLAGS := -std=c99 -O2 -Wall -Wextra -Werror
TARGET := build/QRTEST.EXE
TRANSPORT_SOURCES := src/file_size.c src/frame_count.c src/frame_offset.c src/file_chunk.c
MANUAL_SOURCES := src/manual_transfer.c src/manual_selection.c src/pc98_key_input.c src/pc98_status_font.c
V2_SOURCES := src/filename_v2.c src/frame_v2.c
SOURCES := src/main.c src/pc98_graphics.c src/qr_encode.c src/frame_header.c $(TRANSPORT_SOURCES) $(MANUAL_SOURCES) $(V2_SOURCES)
HEADERS := src/pc98_graphics.h src/qr_encode.h src/frame_header.h \
           src/file_size.h src/frame_count.h src/frame_offset.h src/file_chunk.h \
           src/manual_transfer.h src/manual_selection.h src/pc98_key_input.h src/pc98_status_font.h \
           src/filename_v2.h src/frame_v2.h
LINKER_SCRIPT := build/qrtest-v30.ld
CPPFLAGS :=
# このia16 GCCでは、C99モードで__farを使うために-fasmが必要。
# This ia16 GCC requires -fasm to use __far in C99 mode.
CFLAGS := -std=c99 -mcmodel=small -march=v30 -Os -Wall -Wextra -fasm
LDFLAGS :=

.PHONY: all generate test clean FORCE

all: $(TARGET)

$(TARGET): $(SOURCES) $(HEADERS) $(LINKER_SCRIPT) Makefile | build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -T $(LINKER_SCRIPT) -o $@ $(SOURCES)

# Recheck the active toolchain even after a compiler switch. The helper keeps
# the generated file's mtime when unchanged, so the EXE needs no relink then.
# Host-only targets do not query or require the ia16 compiler.
$(LINKER_SCRIPT): tools/generate_qrtest_linker.py Makefile FORCE | build
	$(PYTHON) tools/generate_qrtest_linker.py --cc "$(CC)" --output $@

build:
	mkdir -p $@

# Explicit Phase 1 reference generation; the EXE needs no Python packages.
generate:
	$(PYTHON) tools/generate_fixed_qr.py

build/test_qr_encode: tests/encode_main.c src/qr_encode.c src/qr_encode.h Makefile | build
	$(HOST_CC) $(HOST_CFLAGS) -o $@ tests/encode_main.c

build/test_qr_v2: tests/encode_v2_main.c src/qr_encode.c src/qr_encode.h Makefile | build
	$(HOST_CC) $(HOST_CFLAGS) -o $@ tests/encode_v2_main.c

build/test_main_render: tests/render_main.c src/main.c src/qr_encode.c src/frame_header.c $(TRANSPORT_SOURCES) $(HEADERS) Makefile | build
	$(HOST_CC) $(HOST_CFLAGS) -o $@ tests/render_main.c src/qr_encode.c src/frame_header.c src/frame_count.c src/frame_offset.c

test: build/test_qr_encode build/test_qr_v2 build/test_main_render
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -f $(TARGET) $(LINKER_SCRIPT) build/test_qr_encode build/test_qr_v2 build/test_main_render
