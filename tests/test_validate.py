"""Tests for the validators in builder.config."""
import pytest

from builder.config import (
    ConfigError,
    validate_config_fragment,
    validate_toolchain_descriptor,
)

GOOD_URL = "https://example.com/llvm.tar.xz"


def test_toolchain_with_url_ok():
    validate_toolchain_descriptor({"name": "x", "version": "1", "url": GOOD_URL})


def test_toolchain_with_path_ok():
    validate_toolchain_descriptor({"name": "x", "version": "1", "path": "~/tc"})


def test_toolchain_needs_url_or_path():
    with pytest.raises(ConfigError):
        validate_toolchain_descriptor({"name": "x", "version": "1"})


def test_toolchain_rejects_placeholder_url():
    with pytest.raises(ConfigError):
        validate_toolchain_descriptor(
            {"name": "x", "version": "1", "url": "https://example.com/..."}
        )


def test_toolchain_rejects_bad_digest():
    with pytest.raises(ConfigError):
        validate_toolchain_descriptor(
            {"name": "x", "version": "1", "url": GOOD_URL,
             "checksum": {"algorithm": "sha256", "value": "abc"}}
        )


def test_toolchain_accepts_good_digest():
    validate_toolchain_descriptor(
        {"name": "x", "version": "1", "url": GOOD_URL,
         "checksum": {"algorithm": "sha256", "value": "a" * 64}}
    )


def test_fragment_ok(tmp_path):
    f = tmp_path / "a.config"
    f.write_text("# comment\nCONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C=\"x\"\nCONFIG_D=100\n")
    assert validate_config_fragment(str(f)) == []


def test_fragment_inline_comment_flagged(tmp_path):
    f = tmp_path / "a.config"
    f.write_text("CONFIG_A=y   # oops\n")
    assert len(validate_config_fragment(str(f))) == 1


def test_fragment_garbage_flagged(tmp_path):
    f = tmp_path / "a.config"
    f.write_text("hello\n")
    assert len(validate_config_fragment(str(f))) == 1


def test_mailbox_patch_detection(tmp_path):
    from builder.patch import _is_mailbox_patch
    mbox = tmp_path / "0001.patch"
    mbox.write_text("From abc Mon Sep 17 00:00:00 2001\nFrom: A <a@b>\nSubject: [PATCH] x\n\n---\n")
    plain = tmp_path / "plain.patch"
    plain.write_text("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n")
    assert _is_mailbox_patch(mbox)
    assert not _is_mailbox_patch(plain)
