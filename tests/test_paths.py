"""Tests for the project layout rules in paths.py."""
from pathlib import Path

from builder import paths
from builder.config import load_build_config


def git(branch):
    return {"type": "git", "repo": "https://example.org/linux", "branch": branch}


def test_source_dir_from_release_tag():
    assert paths.source_dir_name(git("v7.2")) == "kernel-7.2"
    assert paths.source_dir_name(git("v6.12.5")) == "kernel-6.12.5"


def test_source_dir_from_branch_name_is_a_single_folder():
    assert paths.source_dir_name(git("android15-6.6")) == "kernel-android15-6.6"
    assert "/" not in paths.source_dir_name(git("feature/foo"))


def test_source_dir_from_tarball_name():
    src = {"type": "tarball", "url": "https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.8.tar.xz"}
    assert paths.source_dir_name(src) == "kernel-7.2.8"


def test_source_dir_explicit_override_wins():
    src = git("v7.2") | {"directory": "my-tree"}
    assert paths.source_dir_name(src) == "my-tree"


def test_output_layout_for_a_build():
    cfg = {"meta": {"name": "t440-dev"}}
    assert paths.obj_dir(cfg) == Path("out/t440-dev/obj")
    assert paths.default_dist_dir(cfg) == Path("out/t440-dev/dist")


def test_toolchains_and_ccache_live_under_build(monkeypatch):
    monkeypatch.delenv("KBUILD_TOOLCHAIN_CACHE", raising=False)
    assert paths.toolchain_cache() == Path("build/toolchains")
    assert paths.ccache_dir() == Path("build/ccache")


def test_toolchain_cache_can_still_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("KBUILD_TOOLCHAIN_CACHE", str(tmp_path))
    assert paths.toolchain_cache() == tmp_path


def test_build_file_without_output_directory_gets_out_dist(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("""
meta: {name: demo}
kernel: {source: {type: git, repo: https://example.org/l, branch: v7.2}}
target: {arch: x86_64}
toolchain: {descriptor: toolchains/kernel-org-llvm-23.yaml}
config: {base: defconfig}
""")
    assert load_build_config(str(f))["output"]["directory"] == "out/demo/dist"
