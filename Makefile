# Build bootable Project Oberon 2013 / Extended Oberon disk images with
# AgentTool.Mod compiled in and our patches applied.
#
# Pipeline per variant V (po | eo):
#   1. tools         -> cargo build the rust emulator + host-tools
#   2. <v>-source    -> extract source from a stock disk image
#                       (PO: vendor/risc-emu/DiskImage/Oberon-2020-08-18.dsk
#                        EO: vendor/extended-oberon/Documentation/S3RISCinstall.tar.gz)
#   3. <v>-image     -> assemble build/<v>-src/ = source + Mod/<Variant>/ + patches,
#                       then build-{po,eo}-image -> DiskImage/<Variant>Oberon.dsk
#
# `make image` builds both. `make po-image` / `make eo-image` build one each.

EMU         := vendor/risc-emu
BIN         := $(EMU)/target/release
RISC        := $(BIN)/risc
BUILD_PO    := $(BIN)/build-po-image
BUILD_EO    := $(BIN)/build-eo-image
EXTRACT     := $(BIN)/extract-source
OB2TXT      := $(BIN)/ob2txt
TXT2OB      := $(BIN)/txt2ob

PO_STOCK    := $(EMU)/DiskImage/Oberon-2020-08-18.dsk
PO_SRC      := build/po
PO_IMAGE    := DiskImage/ProjectOberon.dsk
PO_MOD_DIR  := Mod/ProjectOberon
PO_PATCHES  := $(wildcard $(PO_MOD_DIR)/*.patch)
PO_NEW_MODS := $(wildcard $(PO_MOD_DIR)/*.Mod)

EO_TARBALL  := vendor/extended-oberon/Documentation/S3RISCinstall.tar.gz
EO_STOCK    := build/eo-stock.dsk
EO_SRC      := build/eo
EO_IMAGE    := DiskImage/ExtendedOberon.dsk
EO_MOD_DIR  := Mod/ExtendedOberon
EO_PATCHES  := $(wildcard $(EO_MOD_DIR)/*.patch)
EO_NEW_MODS := $(wildcard $(EO_MOD_DIR)/*.Mod)

FIFO_IN     ?= /tmp/p.in
FIFO_OUT    ?= /tmp/p.out

# Default variant for `make oberon` (override on the command line: VARIANT=po).
VARIANT     ?= eo

.PHONY: image po-image eo-image tools po-source eo-source oberon clean distclean

# --- combined targets --------------------------------------------------------

image: po-image eo-image

po-image: $(PO_IMAGE)

eo-image: $(EO_IMAGE)

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

# --- prerequisites -----------------------------------------------------------

tools: $(RISC)

$(RISC):
	cargo build --release --manifest-path $(EMU)/Cargo.toml --workspace --bins

DiskImage:
	@mkdir -p $@

# --- run ---------------------------------------------------------------------

# `make oberon` boots the default variant ($(VARIANT)). Override with
#   make oberon VARIANT=po
oberon:
	@mkdir -p log
	@case "$(VARIANT)" in \
	  po) IMG=$(PO_IMAGE) ;; \
	  eo) IMG=$(EO_IMAGE) ;; \
	  *)  echo "VARIANT must be 'po' or 'eo'" >&2; exit 1 ;; \
	esac; \
	TS=$$(date +%Y%m%d-%H%M%S); LOG=log/oberon-$(VARIANT)-$$TS.log; \
	echo "logging to $$LOG"; \
	$(RISC) --serial-in $(FIFO_IN) --serial-out $(FIFO_OUT) $$IMG 2>&1 | tee "$$LOG"

# --- cleanup -----------------------------------------------------------------

clean:
	rm -rf build DiskImage

distclean: clean
	rm -rf vendor/risc-emu/target
