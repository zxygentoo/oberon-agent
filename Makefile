# Build a bootable Extended Oberon disk image with our oberon/*.Mod compiled in.
#
# build-eo-image compiles every file in the source tree except those listed in
# the tree's .packonly, ordering them by a topological sort of their IMPORTs. We
# assemble eo/ + oberon/*.Mod into one tree (oberon/ overrides upstream by name)
# and hand it over; Agent.Mod compiles and Agent.rsc is baked into the image,
# and our patched Oberon.Mod loads Agent at boot.

BUILD_IMAGE ?= ./bin/build-eo-image
REF         ?= eo
IMAGE       ?= build/puck.dsk

image:
	rm -rf build/src && mkdir -p build/src
	cp -a $(REF)/. build/src/
	cp oberon/*.Mod build/src/
	$(BUILD_IMAGE) build/src $(IMAGE)
	@echo "built $(IMAGE)"

clean:
	rm -rf build

oberon:
	@mkdir -p log
	@TS=$$(date +%Y%m%d-%H%M%S); LOG=log/oberon-$$TS.log; \
	echo "logging to $$LOG"; \
	./bin/risc --serial-in /tmp/p.in --serial-out /tmp/p.out build/puck.dsk 2>&1 | tee "$$LOG"

agent:
	@mkdir -p log
	@TS=$$(date +%Y%m%d-%H%M%S); ROOT=$$(pwd); LOG=$$ROOT/log/agent-$$TS.log; \
	echo "logging to $$LOG"; \
	cd python && rlwrap uv run pucxy run \
		--serial-in /tmp/p.in --serial-out /tmp/p.out \
		--base-url https://api.deepseek.com --model deepseek-v4-pro \
		--log "$$LOG"

.PHONY: image clean oberon agent
