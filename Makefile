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
	./bin/risc --serial-in /tmp/p.in --serial-out /tmp/p.out build/puck.dsk

agent:
	cd python && rlwrap uv run pucxy run \
		--serial-in /tmp/p.in --serial-out /tmp/p.out \
		--base-url https://api.deepseek.com --model deepseek-v4-pro

.PHONY: image clean oberon agent
