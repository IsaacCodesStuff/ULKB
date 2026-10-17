"""Toolchain paths must be absolute: make runs inside the kernel source folder."""
import os
import subprocess

import pytest
from rich.console import Console

from builder.toolchain import Toolchain, resolve_toolchain


def make_toolchain_dir(root, tools=("clang", "ld.lld")):
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for t in tools:
        f = bin_dir / t
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
    return root


def test_relative_root_becomes_absolute(tmp_path, monkeypatch):
    make_toolchain_dir(tmp_path / "build" / "toolchains" / "x")
    monkeypatch.chdir(tmp_path)
    tc = Toolchain("x", "1", "build/toolchains/x", {"CC": "clang"})
    assert tc.root.is_absolute()
    first = tc.make_env()["PATH"].split(":")[0]
    assert os.path.isabs(first) and first == str(tc.root / "bin")


def test_toolchain_tools_are_found_from_another_directory(tmp_path, monkeypatch):
    """The bug: PATH held 'build/toolchains/x/bin', which only works from the project root."""
    make_toolchain_dir(tmp_path / "build" / "toolchains" / "x", tools=("ulkb-fake-ld",))
    monkeypatch.chdir(tmp_path)
    tc = Toolchain("x", "1", "build/toolchains/x", {})
    elsewhere = tmp_path / "kernel-7.2"
    elsewhere.mkdir()
    r = subprocess.run(["sh", "-c", "command -v ulkb-fake-ld"], cwd=elsewhere,
                       env=tc.make_env(), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("/ulkb-fake-ld")


def test_resolve_with_relative_path_descriptor(tmp_path, monkeypatch):
    make_toolchain_dir(tmp_path / "tc")
    (tmp_path / "t.yaml").write_text(
        "name: t\nversion: '1'\npath: tc\nenv: {CC: clang, LD: ld.lld}\n")
    monkeypatch.chdir(tmp_path)
    tc = resolve_toolchain({"descriptor": "t.yaml"}, Console())
    assert tc.root == (tmp_path / "tc").resolve()


def test_missing_tool_is_reported_up_front(tmp_path):
    make_toolchain_dir(tmp_path / "tc", tools=("clang",))
    tc = Toolchain("x", "1", tmp_path / "tc", {"CC": "clang", "LD": "ld.lld"})
    with pytest.raises(FileNotFoundError, match="bin/ld.lld"):
        tc.verify()


def test_verify_passes_when_everything_is_there(tmp_path):
    make_toolchain_dir(tmp_path / "tc")
    Toolchain("x", "1", tmp_path / "tc", {"CC": "clang", "LD": "ld.lld"}).verify()
