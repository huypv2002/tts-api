# -*- coding: utf-8 -*-
"""
Copy non-Python package data into Nuitka standalone dist.

Nuitka often compiles .py but omits package data (json/zip/mmdb/yml).
Camoufox dependency chain needs these at import time:

  apify_fingerprint_datapoints/data/*.zip
  language_tags/data/json/*.json
  camoufox/*.yml, GeoLite2-City.mmdb, webgl/*, ...
  browserforge/injectors/data/*
  playwright/driver/*
  tls_client/dependencies/*
  certifi/cacert.pem

Usage:
  python preview_studio/ci_ensure_package_data.py path/to/PreviewStudio.dist
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

# Packages whose non-.py files must exist next to the frozen app
PACKAGES = [
    "apify_fingerprint_datapoints",
    "language_tags",
    "browserforge",
    "camoufox",
    "playwright",
    "tls_client",
    "certifi",
    "ua_parser",
    "screeninfo",
    "platformdirs",
]

# Files that MUST exist after copy (fail CI if missing)
REQUIRED = [
    "apify_fingerprint_datapoints/data/input-network-definition.zip",
    "apify_fingerprint_datapoints/data/header-network-definition.zip",
    "apify_fingerprint_datapoints/data/fingerprint-network-definition.zip",
    "apify_fingerprint_datapoints/data/headers-order.json",
    "apify_fingerprint_datapoints/data/browser-helper-file.json",
    "language_tags/data/json/index.json",
    "language_tags/data/json/language.json",
    "language_tags/data/json/registry.json",
    "camoufox/browserforge.yml",
    "camoufox/fonts.json",
    "camoufox/warnings.yml",
    "browserforge/injectors/data/utils.js.xz",
    "certifi/cacert.pem",
]

SKIP_SUFFIX = {".py", ".pyc", ".pyo", ".pyi"}
SKIP_NAMES = {"py.typed"}


def package_root(name: str) -> Path | None:
    try:
        mod = importlib.import_module(name)
    except Exception as e:
        print(f"  skip {name}: import fail {e}")
        return None
    f = getattr(mod, "__file__", None)
    if f:
        return Path(f).resolve().parent
    paths = getattr(mod, "__path__", None)
    if paths:
        return Path(list(paths)[0]).resolve()
    return None


def copy_package_data(src: Path, dst: Path) -> int:
    n = 0
    if not src.is_dir():
        return 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts:
            continue
        if f.suffix.lower() in SKIP_SUFFIX or f.name in SKIP_NAMES:
            continue
        # skip huge accidental caches
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_ensure_package_data.py <dist_dir>")
        return 2
    dist = Path(sys.argv[1]).resolve()
    if not dist.is_dir():
        print("dist not found:", dist)
        return 2
    print("dist:", dist)
    total = 0
    for name in PACKAGES:
        src = package_root(name)
        if not src:
            continue
        dst = dist / name
        # Always merge data into dist/<package>/
        c = copy_package_data(src, dst)
        total += c
        print(f"  {name}: copied {c} data files  {src} -> {dst}")
    print(f"total data files copied: {total}")

    missing = []
    for rel in REQUIRED:
        p = dist / rel.replace("/", "\\") if False else dist.joinpath(*rel.split("/"))
        if p.is_file() and p.stat().st_size > 0:
            print("  OK", rel, f"({p.stat().st_size}B)")
        else:
            print("  MISSING", rel)
            missing.append(rel)
    if missing:
        print("FAIL missing required data:", missing)
        return 1

    # Smoke: import using dist on path (pure-python data loaders)
    # Note: may still use system bytecode for some modules; validates data paths
    # relative to packages that live under dist.
    sys.path.insert(0, str(dist))
    # Prefer dist package locations
    try:
        import apify_fingerprint_datapoints as afd
        from pathlib import Path as P

        # Force using dist copy if present
        z = dist / "apify_fingerprint_datapoints" / "data" / "input-network-definition.zip"
        assert z.is_file(), z
        print("smoke apify zip OK", z)
    except Exception as e:
        print("smoke apify warn:", e)

    try:
        # language_tags loads json on import of Subtag
        import importlib

        # Remove cached system language_tags if any
        for k in list(sys.modules):
            if k == "language_tags" or k.startswith("language_tags."):
                del sys.modules[k]
        # Put dist first
        import language_tags  # noqa: F401

        from language_tags import tags

        # touch data
        _ = tags.tag("en-US")
        print("smoke language_tags OK")
    except Exception as e:
        print("smoke language_tags FAIL:", e)
        return 1

    print("ALL PACKAGE DATA OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
