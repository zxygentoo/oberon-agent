#!/usr/bin/env python3
"""Assemble a Project Oberon disk image with our Oberon code baked in.

bin/build-image only compiles a fixed module list, so it can't compile our
oberon/*.Mod directly. We work around that: each of our modules is compiled by
placing it under an unused leaf-module filename (which IS in build-image's list),
then we harvest the resulting device .rsc/.smb and install them alongside the
sources. The image then has a loadable Agent.rsc — bring-up is just `Agent.Run`.

Use --no-precompile to ship sources only (then compile on-device:
`ORP.Compile Agent.Mod/s` before `Agent.Run`). See ../README.md.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Leaf modules in build-image's fixed list with no dependents — safe to repurpose
# as compile slots. Ordered as they appear in the build (imports must precede importers).
LEAF_SLOTS = ["Blink", "Checkers", "Hilbert", "Sierpinski", "Stars"]


def _module_name(text: str) -> str:
    m = re.search(r"\bMODULE\s+(\w+)", text)
    if not m:
        raise SystemExit("could not find 'MODULE <name>' in source")
    return m.group(1)


def _precompile(
    sources: Path, mods: list[Path], build_image: Path, workdir: Path
) -> dict[str, bytes]:
    """Cross-compile our modules to device .rsc/.smb by leaf-slot substitution.
    Returns {filename: bytes}."""
    if len(mods) > len(LEAF_SLOTS):
        raise SystemExit(f"too many modules to precompile (max {len(LEAF_SLOTS)})")
    tree = workdir / "pre"
    shutil.copytree(sources, tree)
    names = []
    for mod, slot in zip(mods, LEAF_SLOTS):
        text = mod.read_text(encoding="latin1")
        (tree / f"{slot}.Mod").write_text(text, encoding="latin1")  # keeps its real MODULE name
        names.append(_module_name(text))
    # build-image fails the leaf .rsc existence check (we made <real>.rsc instead) and
    # leaves the scratch dir, where the compiled objects live.
    r = subprocess.run(
        [str(build_image), str(tree), str(workdir / "pre.dsk")], capture_output=True, text=True
    )
    out = r.stdout + r.stderr
    m = re.search(r"intermediates left in (\S+)", out)
    if not m:
        sys.stderr.write(out)
        raise SystemExit("precompile: build-image left no scratch dir (unexpected)")
    scratch = Path(m.group(1))
    artifacts: dict[str, bytes] = {}
    for name in names:
        for ext in ("rsc", "smb"):
            f = scratch / "oberon" / f"{name}.{ext}"
            if f.exists():
                artifacts[f"{name}.{ext}"] = f.read_bytes()
    shutil.rmtree(scratch, ignore_errors=True)
    missing = [n for n in names if f"{n}.rsc" not in artifacts]
    if missing:
        sys.stderr.write(out)
        raise SystemExit(f"precompile: did not compile {missing} (see ORS diagnostics above)")
    return artifacts


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--sources", type=Path, default=REPO / "op2013-src", help="PO2013 reference tree"
    )
    ap.add_argument("--oberon", type=Path, default=REPO / "oberon", help="our *.Mod sources")
    ap.add_argument("--build-image", type=Path, default=REPO / "bin" / "build-image")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "puck.dsk")
    ap.add_argument(
        "--no-precompile",
        action="store_true",
        help="ship sources only; compile on-device before Agent.Run",
    )
    a = ap.parse_args()

    if not a.sources.is_dir():
        sys.exit(f"sources tree not found: {a.sources}")
    if not a.build_image.exists():
        sys.exit(f"build-image not found: {a.build_image}")
    mods = sorted(a.oberon.glob("*.Mod"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        artifacts: dict[str, bytes] = {}
        if mods and not a.no_precompile:
            print(f"precompiling {[m.name for m in mods]} ...")
            artifacts = _precompile(a.sources, mods, a.build_image, tmp)
            print(f"baked objects: {sorted(artifacts)}")

        tree = tmp / "src"
        shutil.copytree(a.sources, tree)
        for mod in mods:
            shutil.copy(mod, tree / mod.name)
            print(f"baked source {mod.name}")
        for fn, data in artifacts.items():
            (tree / fn).write_bytes(data)

        a.out.parent.mkdir(parents=True, exist_ok=True)
        print(f"building {a.out} ...")
        return subprocess.run([str(a.build_image), str(tree), str(a.out)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
