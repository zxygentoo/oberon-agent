# Build bootable Project Oberon 2013 / Extended Oberon disk images with
# AgentTool.Mod compiled in and our patches applied.
#
# Pipeline per variant V (po | eo):
#   1. tools         -> cargo build the rust emulator + host-tools
#   2. <v>-source    -> extract source from a stock disk image
#                       (PO: vendor/oberon-risc-emu-rs/DiskImage/Oberon-2020-08-18.dsk
#                        EO: downloaded S3RISCinstall.tar.gz from upstream)
#   3. <v>-image     -> assemble build/<v>-src/ = source + Mod/<Variant>/ + patches,
#                       then build-{po,eo}-image -> DiskImage/<Variant>Oberon.dsk
#
# `make image` builds both. `make po-image` / `make eo-image` build one each.

EMU         := vendor/oberon-risc-emu-rs
BIN         := $(EMU)/target/release
RISC        := $(BIN)/risc
BUILD_PO    := $(BIN)/build-po-image
BUILD_EO    := $(BIN)/build-eo-image
EXTRACT     := $(BIN)/extract-source
OB2TXT      := $(BIN)/ob2txt
TXT2OB      := $(BIN)/txt2ob

OAT_BIN     := oat/target/release/oat
OAT_SOURCES := $(wildcard oat/src/*.rs) oat/Cargo.toml oat/Cargo.lock

PO_STOCK    := $(EMU)/DiskImage/Oberon-2020-08-18.dsk
PO_SRC      := build/po
PO_IMAGE    := DiskImage/ProjectOberon.dsk
PO_MOD_DIR  := Mod/ProjectOberon
PO_PATCHES  := $(wildcard $(PO_MOD_DIR)/*.patch)
PO_NEW_MODS := $(wildcard $(PO_MOD_DIR)/*.Mod)

# EO stock disk image: downloaded from upstream (not vendored — the full
# Oberon-extended repo is ~12 MB but we only consume this one file).
EO_TARBALL_URL := https://github.com/andreaspirklbauer/Oberon-extended/raw/refs/heads/master/Documentation/S3RISCinstall.tar.gz
EO_TARBALL  := build/S3RISCinstall.tar.gz
EO_STOCK    := build/eo-stock.dsk
EO_SRC      := build/eo
EO_IMAGE    := DiskImage/ExtendedOberon.dsk
EO_MOD_DIR  := Mod/ExtendedOberon
EO_PATCHES  := $(wildcard $(EO_MOD_DIR)/*.patch)
EO_NEW_MODS := $(wildcard $(EO_MOD_DIR)/*.Mod)

FIFO_IN     ?= /tmp/p.in
FIFO_OUT    ?= /tmp/p.out

.PHONY: image po-image eo-image tools oat po-source eo-source po-emu eo-emu check-fifos clean distclean

# Default goal — bare `make` builds both images.
.DEFAULT_GOAL := image

# --- combined targets --------------------------------------------------------

image: po-image eo-image

po-image: tools $(PO_IMAGE)

eo-image: tools $(EO_IMAGE)

# --- PO image ----------------------------------------------------------------

$(PO_IMAGE): $(RISC) $(PO_SRC)/.stamp $(PO_PATCHES) $(PO_NEW_MODS) | DiskImage
	@rm -rf build/po-src && mkdir -p build/po-src
	cp -a $(PO_SRC)/. build/po-src/
	@for p in $(PO_PATCHES); do \
	  m=$$(basename $$p .patch); \
	  $(OB2TXT) build/po-src/$$m >/dev/null; \
	  patch --silent build/po-src/$$m.txt < $$p; \
	  $(TXT2OB) build/po-src/$$m.txt >/dev/null; \
	  rm build/po-src/$$m.txt; \
	done
	@for f in $(PO_NEW_MODS); do \
	  name=$$(basename $$f); \
	  cp $$f build/po-src/$$name.txt; \
	  $(TXT2OB) build/po-src/$$name.txt >/dev/null; \
	  rm build/po-src/$$name.txt; \
	done
	$(BUILD_PO) build/po-src $(PO_IMAGE)
	@echo "built $(PO_IMAGE)"

po-source: $(PO_SRC)/.stamp

$(PO_SRC)/.stamp: $(PO_STOCK) | tools
	@mkdir -p $(PO_SRC)
	$(EXTRACT) $(PO_STOCK) $(PO_SRC)
	@touch $@

# --- EO image ----------------------------------------------------------------

$(EO_IMAGE): $(RISC) $(EO_SRC)/.stamp $(EO_PATCHES) $(EO_NEW_MODS) | DiskImage
	@rm -rf build/eo-src && mkdir -p build/eo-src
	cp -a $(EO_SRC)/. build/eo-src/
	@for p in $(EO_PATCHES); do \
	  m=$$(basename $$p .patch); \
	  $(OB2TXT) build/eo-src/$$m >/dev/null; \
	  patch --silent build/eo-src/$$m.txt < $$p; \
	  $(TXT2OB) build/eo-src/$$m.txt >/dev/null; \
	  rm build/eo-src/$$m.txt; \
	done
	@for f in $(EO_NEW_MODS); do \
	  name=$$(basename $$f); \
	  cp $$f build/eo-src/$$name.txt; \
	  $(TXT2OB) build/eo-src/$$name.txt >/dev/null; \
	  rm build/eo-src/$$name.txt; \
	done
	$(BUILD_EO) build/eo-src $(EO_IMAGE)
	@echo "built $(EO_IMAGE)"

eo-source: $(EO_SRC)/.stamp

$(EO_SRC)/.stamp: $(EO_TARBALL) | tools
	@mkdir -p build
	tar --warning=no-unknown-keyword -xzf $(EO_TARBALL) \
	  -C build --strip-components=1 S3RISCinstall/RISC.img
	mv build/RISC.img $(EO_STOCK)
	$(EXTRACT) $(EO_STOCK) $(EO_SRC)
	@touch $@

$(EO_TARBALL):
	@mkdir -p build
	@echo "downloading $(EO_TARBALL_URL)"
	@if command -v curl >/dev/null 2>&1; then \
	  curl -fsSL -o $@ $(EO_TARBALL_URL); \
	elif command -v wget >/dev/null 2>&1; then \
	  wget -q -O $@ $(EO_TARBALL_URL); \
	else \
	  echo "need curl or wget to fetch the EO stock image" >&2; exit 1; \
	fi

# --- prerequisites -----------------------------------------------------------

tools: $(RISC) $(OAT_BIN)

$(RISC):
	cargo build --release --manifest-path $(EMU)/Cargo.toml --workspace --bins

oat: $(OAT_BIN)

$(OAT_BIN): $(OAT_SOURCES)
	cargo build --release --manifest-path oat/Cargo.toml

DiskImage:
	@mkdir -p $@

# --- run ---------------------------------------------------------------------

# `make {eo,po}-emu` builds the image if stale, then boots it on the FIFO pair.
# Override the FIFOs with `make eo-emu FIFO_IN=... FIFO_OUT=...`.
eo-emu: eo-image check-fifos
	$(RISC) --serial-in $(FIFO_IN) --serial-out $(FIFO_OUT) $(EO_IMAGE)

po-emu: po-image check-fifos
	$(RISC) --serial-in $(FIFO_IN) --serial-out $(FIFO_OUT) $(PO_IMAGE)

# Verify both FIFOs exist and are actually named pipes (vs missing or a
# regular file someone `touch`ed by accident).
check-fifos:
	@for f in $(FIFO_IN) $(FIFO_OUT); do \
	  if [ ! -e "$$f" ]; then \
	    echo "missing FIFO: $$f" >&2; \
	    echo "  create with: mkfifo $(FIFO_IN) $(FIFO_OUT)" >&2; \
	    exit 1; \
	  elif [ ! -p "$$f" ]; then \
	    echo "not a FIFO (regular file?): $$f" >&2; \
	    echo "  remove it and run: mkfifo $(FIFO_IN) $(FIFO_OUT)" >&2; \
	    exit 1; \
	  fi; \
	done

# --- cleanup -----------------------------------------------------------------

clean:
	rm -rf build DiskImage

distclean: clean
	rm -rf vendor/oberon-risc-emu-rs/target oat/target
