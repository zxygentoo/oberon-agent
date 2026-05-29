# Build a Project Oberon disk image with our oberon/*.Mod compiled in.
#
# build-image compiles every file in the source tree except those listed in the
# tree's .packonly, ordering them by a topological sort of their IMPORTs. So we
# just assemble po2013/ (which carries .packonly) + oberon/*.Mod into one
# tree and hand it over; Agent.Mod is compiled and Agent.rsc baked into the image.

BUILD_IMAGE ?= ./bin/build-image
REF         ?= po2013
IMAGE       ?= build/puck.dsk

.PHONY: image clean
image:
	rm -rf build/src && mkdir -p build/src
	cp -a $(REF)/. build/src/
	cp oberon/*.Mod build/src/
	$(BUILD_IMAGE) build/src $(IMAGE)
	@echo "built $(IMAGE)"

clean:
	rm -rf build
