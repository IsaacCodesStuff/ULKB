"""
paths.py — where ULKB keeps things, relative to the project root (the directory
you run it from; build_kernel.sh cd's there for you).

    ULKB/
    ├── builds/ configs/ toolchains/   build definitions (committed)
    ├── kernel-7.2/                    kernel source, pristine (never built in)
    ├── build/                         toolchains and ccache (downloaded)
    ├── out/<build>/obj/               make O=… output: .config, objects, bzImage
    ├── out/<build>/dist/              what you take away: bzImage, config, modules
    └── build_kernel.sh

The kernel source is never written to while building (make O=), so one
checkout serves every build file, and nothing needs `make mrproper` in between.
"""

import os
import re
from pathlib import Path


def build_dir() -> Path:
    """Downloaded tooling: toolchains and the ccache."""
    return Path("build")


def toolchain_cache() -> Path:
    """Where toolchain archives are extracted (KBUILD_TOOLCHAIN_CACHE overrides)."""
    env = os.environ.get("KBUILD_TOOLCHAIN_CACHE")
    return Path(env).expanduser() if env else build_dir() / "toolchains"


def ccache_dir() -> Path:
    return build_dir() / "ccache"


def downloads_dir() -> Path:
    return build_dir() / "downloads"


def source_dir_name(source: dict) -> str:
    """Directory (at the project root) holding this kernel's source.

    kernel.source.directory wins. Otherwise it is derived from the version:
    branch/tag "v7.2" -> kernel-7.2, tarball linux-7.2.8.tar.xz -> kernel-7.2.8.
    """
    explicit = source.get("directory")
    if explicit:
        return explicit
    if source["type"] == "git":
        branch = source["branch"]
        m = re.fullmatch(r"v(\d[\w.+-]*)", branch)
        label = m.group(1) if m else branch.replace("/", "-")
        return f"kernel-{label}"
    name = source["url"].rstrip("/").split("/")[-1]
    name = re.sub(r"\.tar(\.\w+)?$|\.tgz$", "", name)
    name = re.sub(r"^linux-", "", name)
    return f"kernel-{name}"


def source_dir(source: dict) -> Path:
    return Path(source_dir_name(source))


def out_dir(config: dict) -> Path:
    return Path("out") / config["meta"]["name"]


def obj_dir(config: dict) -> Path:
    """The `make O=` directory: every generated file of this build lives here."""
    return out_dir(config) / "obj"


def default_dist_dir(config: dict) -> Path:
    return out_dir(config) / "dist"
