# Build a bootable Extended Oberon disk image with our oberon/*.Mod compiled in.
#
# The build is self-contained from a fresh clone with submodules:
#   git clone --recurse-submodules ...
#   make image       # tools + eo-source + puck.dsk (~3-5 min cold, ~5s warm)
#
# Pipeline:
#   1. tools       -> cargo build the rust emulator + host-tools (vendor/risc-emu)
#   2. eo-source   -> untar Documentation/S3RISCinstall.tar.gz, extract source via
#                     extract-source (vendor/extended-oberon -> build/eo/)
#   3. image       -> assemble build/src/ = build/eo/. + our oberon/ (patches applied,
#                     new modules dropped in), then build-eo-image -> build/puck.dsk
#
# Modifications to upstream EO modules live in oberon/<Name>.Mod.patch (LF-text
# unified diff, applied via the ob2txt/txt2ob roundtrip). New modules live as
# oberon/<Name>.Mod (LF text; we convert to CR before placing in build/src/).
# See `make wip` and `make patches` for the edit cycle.

EMU         := vendor/risc-emu
BIN         := $(EMU)/target/release
RISC        := $(BIN)/risc
BUILD_EO    := $(BIN)/build-eo-image
EXTRACT     := $(BIN)/extract-source
OB2TXT      := $(BIN)/ob2txt
TXT2OB      := $(BIN)/txt2ob

EO_TARBALL  := vendor/extended-oberon/Documentation/S3RISCinstall.tar.gz
EO_STOCK    := build/eo-stock.dsk
EO_SRC      := build/eo
IMAGE       := build/puck.dsk

FIFO_IN     ?= /tmp/p.in
FIFO_OUT    ?= /tmp/p.out

PATCHES     := $(wildcard oberon/*.patch)
NEW_MODS    := $(wildcard oberon/*.Mod)

.PHONY: image tools eo-source wip patches oberon agent clean distclean

# --- image build -------------------------------------------------------------

image: $(IMAGE)

$(IMAGE): tools eo-source $(PATCHES) $(NEW_MODS)
	@rm -rf build/src && mkdir -p build/src
	cp -a $(EO_SRC)/. build/src/
	@for p in $(PATCHES); do \
	  m=$$(basename $$p .patch); \
	  $(OB2TXT) build/src/$$m >/dev/null; \
	  patch --silent build/src/$$m.txt < $$p; \
	  $(TXT2OB) build/src/$$m.txt >/dev/null; \
	  rm build/src/$$m.txt; \
	done
	@for f in $(NEW_MODS); do \
	  name=$$(basename $$f); \
	  cp $$f build/src/$$name.txt; \
	  $(TXT2OB) build/src/$$name.txt >/dev/null; \
	  rm build/src/$$name.txt; \
	done
	$(BUILD_EO) build/src $(IMAGE)
	@echo "built $(IMAGE)"

# --- prerequisites -----------------------------------------------------------

tools: $(RISC)

$(RISC):
	cargo build --release --manifest-path $(EMU)/Cargo.toml --workspace --bins

eo-source: $(EO_SRC)/.stamp

$(EO_SRC)/.stamp: $(EO_TARBALL) | tools
	@mkdir -p build
	tar -xzf $(EO_TARBALL) -C build --strip-components=1 S3RISCinstall/RISC.img
	mv build/RISC.img $(EO_STOCK)
	$(EXTRACT) $(EO_STOCK) $(EO_SRC)
	@touch $@

# --- patch edit cycle --------------------------------------------------------

# `make wip`     populates build/wip/ with an editable upstream-with-our-patches
#                tree. Edit a file there, then `make patches`.
# `make patches` regenerates oberon/*.patch by diffing build/wip/ against build/eo/
#                (only writing patches for files that actually differ).

wip: eo-source $(PATCHES)
	@rm -rf build/wip && mkdir -p build/wip
	cp -a $(EO_SRC)/. build/wip/
	@for p in $(PATCHES); do \
	  m=$$(basename $$p .patch); \
	  $(OB2TXT) build/wip/$$m >/dev/null; \
	  patch --silent build/wip/$$m.txt < $$p; \
	  $(TXT2OB) build/wip/$$m.txt >/dev/null; \
	  rm build/wip/$$m.txt; \
	done
	@echo "edit build/wip/<file>.Mod; then: make patches"

patches: | tools
	@test -d build/wip || (echo "no build/wip/ — run 'make wip' first" >&2; exit 1)
	@for f in build/wip/*.Mod; do \
	  name=$$(basename $$f); \
	  orig=$(EO_SRC)/$$name; \
	  [ -f $$orig ] || continue; \
	  cmp -s $$f $$orig && continue; \
	  $(OB2TXT) $$orig >/dev/null; \
	  $(OB2TXT) $$f >/dev/null; \
	  diff -u --label $$name --label $$name $$orig.txt $$f.txt > oberon/$$name.patch; \
	  rm -f $$orig.txt $$f.txt; \
	  echo "wrote oberon/$$name.patch"; \
	done

# --- run ---------------------------------------------------------------------

oberon: $(IMAGE)
	@mkdir -p log
	@TS=$$(date +%Y%m%d-%H%M%S); LOG=log/oberon-$$TS.log; \
	echo "logging to $$LOG"; \
	$(RISC) --serial-in $(FIFO_IN) --serial-out $(FIFO_OUT) $(IMAGE) 2>&1 | tee "$$LOG"

agent:
	@mkdir -p log
	@TS=$$(date +%Y%m%d-%H%M%S); ROOT=$$(pwd); LOG=$$ROOT/log/agent-$$TS.log; \
	echo "logging to $$LOG"; \
	cd python && rlwrap uv run pucxy run \
		--serial-in $(FIFO_IN) --serial-out $(FIFO_OUT) \
		--base-url https://api.deepseek.com --model deepseek-v4-pro \
		--log "$$LOG"

# --- cleanup -----------------------------------------------------------------

clean:
	rm -rf build

distclean: clean
	rm -rf vendor/risc-emu/target
