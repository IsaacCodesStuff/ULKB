"""
config.py — YAML build definition loader and validator.

Loads a build YAML, applies defaults, and validates required fields
before any build step runs.
"""

import os
import re
import yaml
from typing import Any


class ConfigError(Exception):
    """Raised when a build definition is missing required fields or is invalid."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Schema: required fields and their expected types
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_TOP_LEVEL = ["meta", "kernel", "target", "toolchain", "build", "output"]

REQUIRED_META      = ["name"]
REQUIRED_KERNEL    = ["source"]
REQUIRED_SOURCE    = ["type"]  # "git" or "tarball"
REQUIRED_TARGET    = ["arch"]
REQUIRED_TOOLCHAIN = ["descriptor"]
REQUIRED_OUTPUT    = ["directory"]

VALID_ARCHS        = ["x86_64", "arm64", "arm", "riscv64"]
VALID_SOURCE_TYPES = ["git", "tarball"]
VALID_BUILD_MODES  = ["dev", "release"]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_build_config(path: str) -> dict:
    """Load a YAML build definition file and apply defaults.

    Args:
        path: Path to the .yaml build definition file.

    Returns:
        Parsed config dict with defaults applied.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ConfigError: If the YAML is malformed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, "r") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"YAML parse error in {path}: {e}")

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level.")

    return _apply_defaults(raw)


def validate_build_config(config: dict) -> None:
    """Validate a loaded build config dict.

    Raises:
        ConfigError: On the first validation failure found.
    """
    _require_keys(config, REQUIRED_TOP_LEVEL, "top level")
    _require_keys(config["meta"], REQUIRED_META, "meta")
    _require_keys(config["kernel"], REQUIRED_KERNEL, "kernel")
    _require_keys(config["kernel"]["source"], REQUIRED_SOURCE, "kernel.source")
    _require_keys(config["target"], REQUIRED_TARGET, "target")
    _require_keys(config["toolchain"], REQUIRED_TOOLCHAIN, "toolchain")
    _require_keys(config["output"], REQUIRED_OUTPUT, "output")

    # Value checks
    src_type = config["kernel"]["source"]["type"]
    if src_type not in VALID_SOURCE_TYPES:
        raise ConfigError(
            f"kernel.source.type must be one of {VALID_SOURCE_TYPES}, got '{src_type}'"
        )

    arch = config["target"]["arch"]
    if arch not in VALID_ARCHS:
        raise ConfigError(
            f"target.arch must be one of {VALID_ARCHS}, got '{arch}'"
        )

    if src_type == "git":
        _require_keys(
            config["kernel"]["source"],
            ["repo", "branch"],
            "kernel.source (type: git)",
        )

    if src_type == "tarball":
        _require_keys(
            config["kernel"]["source"],
            ["url"],
            "kernel.source (type: tarball)",
        )

    mode = config.get("config", {}).get("mode", "release")
    if mode not in VALID_BUILD_MODES:
        raise ConfigError(
            f"config.mode must be one of {VALID_BUILD_MODES}, got '{mode}'"
        )

    lv = config.get("config", {}).get("localversion")
    if lv is not None and not re.fullmatch(r"-[A-Za-z0-9._+-]+", str(lv)):
        raise ConfigError(
            "config.localversion must start with '-' and use only letters, "
            f"digits and . _ + - (got {lv!r})"
        )

    for key in ("strict",):
        if key in config.get("config", {}) and not isinstance(config["config"][key], bool):
            raise ConfigError(f"config.{key} must be true or false")
    if "install_modules" in config.get("output", {}) and not isinstance(
            config["output"]["install_modules"], bool):
        raise ConfigError("output.install_modules must be true or false")

    # Referenced files must exist (paths are relative to the repo root / cwd)
    descriptor = config["toolchain"]["descriptor"]
    if not os.path.isfile(descriptor):
        raise ConfigError(f"toolchain.descriptor not found: {descriptor}")

    for frag in config.get("config", {}).get("fragments", []):
        if not os.path.isfile(frag):
            raise ConfigError(f"config.fragments entry not found: {frag}")


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _apply_defaults(config: dict) -> dict:
    """Fill in missing optional fields with sensible defaults."""

    config.setdefault("meta", {})
    config["meta"].setdefault("description", "")
    config["meta"].setdefault("maintainer", "")

    config.setdefault("config", {})
    config["config"].setdefault("base", "defconfig")
    config["config"].setdefault("mode", "release")
    config["config"].setdefault("fragments", [])

    config.setdefault("patches", [])

    config.setdefault("build", {})
    config["build"].setdefault("jobs", "auto")
    config["build"].setdefault("ccache", True)
    config["build"].setdefault("incremental", True)
    config["build"].setdefault("flags", ["LLVM=1", "LLVM_IAS=1"])
    config["build"].setdefault("targets", ["vmlinux", "modules", "bzImage"])

    config.setdefault("output", {})
    config["output"].setdefault("directory", "dist/")
    config["output"].setdefault("artifacts", [])
    config["output"].setdefault("metadata", True)
    config["output"].setdefault("compress", False)

    config.setdefault("target", {})
    config["target"].setdefault("cross_compile", None)

    return config


def _require_keys(d: Any, keys: list, section: str) -> None:
    """Assert that all required keys exist in a dict."""
    if not isinstance(d, dict):
        raise ConfigError(f"'{section}' must be a mapping, got {type(d).__name__}")
    for key in keys:
        if key not in d:
            raise ConfigError(f"Missing required field '{key}' in {section}")


# ─────────────────────────────────────────────────────────────────────────────
# Toolchain descriptors
# ─────────────────────────────────────────────────────────────────────────────

import hashlib
import re

HASH_LENGTHS = {"sha256": 64, "sha512": 128, "sha1": 40}


def load_yaml_mapping(path: str) -> dict:
    """Load a YAML file that must be a top-level mapping."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"YAML parse error in {path}: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level.")
    return raw


def validate_toolchain_descriptor(desc: dict) -> None:
    """Validate a toolchain descriptor (toolchains/*.yaml).

    A descriptor is either downloadable (has 'url') or pre-installed
    (has 'path'). Exactly one of the two is required.

    Raises:
        ConfigError: On the first problem found.
    """
    _require_keys(desc, ["name", "version"], "toolchain descriptor")

    has_url  = bool(desc.get("url"))
    has_path = bool(desc.get("path"))
    if has_url == has_path:
        raise ConfigError("toolchain descriptor needs exactly one of 'url' or 'path'")

    if has_url:
        url = desc["url"]
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ConfigError(f"toolchain 'url' must be an http(s) URL, got {url!r}")
        if "..." in url:
            raise ConfigError("toolchain 'url' still contains a '...' placeholder")

        checksum = desc.get("checksum") or {}
        if not isinstance(checksum, dict):
            raise ConfigError("toolchain 'checksum' must be a mapping")
        value = checksum.get("value", "")
        if value:
            algo = checksum.get("algorithm", "sha256")
            if algo not in HASH_LENGTHS:
                raise ConfigError(f"unsupported checksum algorithm '{algo}'")
            if not re.fullmatch(r"[0-9a-f]{%d}" % HASH_LENGTHS[algo], str(value)):
                raise ConfigError(
                    f"checksum.value is not a valid lowercase {algo} hex digest"
                )

    extract = desc.get("extract") or {}
    strip = extract.get("strip_components", 0)
    if not isinstance(strip, int) or strip < 0:
        raise ConfigError("extract.strip_components must be a non-negative integer")

    env = desc.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError("toolchain 'env' must be a mapping")


# ─────────────────────────────────────────────────────────────────────────────
# Kernel config fragments
# ─────────────────────────────────────────────────────────────────────────────

_SET_RE      = re.compile(r"^CONFIG_[A-Za-z0-9_]+=(y|m|n|\d+|0x[0-9a-fA-F]+|\"[^\"]*\")$")
_NOT_SET_RE  = re.compile(r"^# CONFIG_[A-Za-z0-9_]+ is not set$")


def validate_config_fragment(path: str) -> list:
    """Check a kernel config fragment for lines merge_config.sh can't handle.

    Returns:
        A list of 'line N: problem' strings (empty if the file is fine).
    """
    problems = []
    with open(path, "r") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or (line.startswith("#") and not line.startswith("# CONFIG_")):
                continue
            if _NOT_SET_RE.match(line) or _SET_RE.match(line):
                continue
            if line.startswith("CONFIG_") and " #" in line:
                problems.append(f"line {n}: inline comment after value — move it to its own line")
            elif line.startswith("# CONFIG_"):
                problems.append(f"line {n}: commented-out option must read '# CONFIG_X is not set'")
            else:
                problems.append(f"line {n}: not a valid config line: {line!r}")
    return problems
