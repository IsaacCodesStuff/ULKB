"""
toolchain.py — Toolchain resolution and environment setup.

Reads a toolchain descriptor YAML, downloads + caches the toolchain tarball,
verifies its checksum, and returns an env dict ready to pass to make.
"""

import os
import hashlib
import tarfile
import urllib.request
from pathlib import Path
import yaml
from rich.console import Console

from .download import download_file
from .paths import toolchain_cache


# Toolchains are extracted into <project>/build/toolchains (override with the
# descriptor-level cache_dir or the KBUILD_TOOLCHAIN_CACHE environment variable).


class Toolchain:
    """Resolved toolchain: path on disk + make environment variables."""

    def __init__(self, name: str, version: str, root: Path, env: dict):
        self.name    = name
        self.version = version
        self.root    = root     # directory containing bin/clang etc.
        self.env     = env      # CC, LD, AR, ... — ready to inject into make

    def __repr__(self):
        return f"<Toolchain {self.name} {self.version} at {self.root}>"

    def make_env(self) -> dict:
        """Return os.environ copy with toolchain variables injected."""
        e = os.environ.copy()
        # Prepend bin/ to PATH so the toolchain's clang/lld etc. take priority
        bin_dir = str(self.root / "bin")
        e["PATH"] = bin_dir + ":" + e.get("PATH", "")
        for k, v in self.env.items():
            e[k] = str(self.root / "bin" / v) if not os.sep in v else v
        return e


def resolve_toolchain(toolchain_config: dict, console: Console) -> Toolchain:
    """Resolve a toolchain from a descriptor file, downloading if needed.

    Args:
        toolchain_config: The 'toolchain' section of a build definition.
        console: Rich console for output.

    Returns:
        A ready-to-use Toolchain instance.
    """
    descriptor_path = toolchain_config["descriptor"]
    cache_dir = Path(toolchain_config.get("cache_dir") or toolchain_cache()).expanduser()

    # Load the descriptor
    if not os.path.isfile(descriptor_path):
        raise FileNotFoundError(f"Toolchain descriptor not found: {descriptor_path}")

    with open(descriptor_path) as f:
        desc = yaml.safe_load(f)

    name    = desc["name"]
    version = desc["version"]
    url     = desc.get("url", "")
    checksum = desc.get("checksum") or {}
    env_template = desc.get("env", {})
    strip   = int((desc.get("extract") or {}).get("strip_components", 0))

    console.print(f"  Toolchain: [bold]{name}[/bold] {version}")

    # Pre-installed toolchain (e.g. Neutron via antman): use it where it is
    if desc.get("path"):
        root = Path(desc["path"]).expanduser()
        if not (root / "bin").is_dir():
            raise FileNotFoundError(
                f"Toolchain path has no bin/ directory: {root}\n"
                "Install it first (for Neutron: use antman) or fix 'path' in the descriptor."
            )
        console.print(f"[green]✓[/green] Using installed toolchain: [dim]{root}[/dim]")
        return Toolchain(name=name, version=version, root=root, env=dict(env_template))

    # Determine install path
    slug = f"{name.lower().replace(' ', '-')}-{version}"
    install_dir = cache_dir / slug

    if install_dir.exists() and _marker(install_dir).exists():
        console.print(f"[green]✓[/green] Cached: [dim]{install_dir}[/dim]")
    else:
        if not url:
            raise ValueError(
                f"Toolchain descriptor '{descriptor_path}' has no URL — "
                "either provide a URL or install the toolchain manually and set cache_dir."
            )
        console.print(f"  Downloading toolchain from: [dim]{url}[/dim]")
        archive = cache_dir / url.split("/")[-1]
        cache_dir.mkdir(parents=True, exist_ok=True)

        _download(url, archive, console)

        if checksum.get("value"):
            console.print("  Verifying checksum…")
            _verify(archive, checksum["algorithm"], checksum["value"])
            console.print("[green]✓[/green] Checksum OK")
        else:
            console.print("[yellow]⚠[/yellow] No checksum specified — skipping verification")

        _extract(archive, install_dir, console, strip)
        _marker(install_dir).touch()  # mark as successfully installed

    # Build the env dict — values are binary names (relative to bin/)
    env = {k: v for k, v in env_template.items()}

    tc = Toolchain(name=name, version=version, root=install_dir, env=env)
    console.print(f"[green]✓[/green] Toolchain ready")
    return tc


# ─────────────────────────────────────────────────────────────────────────────
# System toolchain fallback
# ─────────────────────────────────────────────────────────────────────────────

def system_toolchain(console: Console) -> Toolchain:
    """Return a Toolchain representing whatever clang/gcc is on PATH."""
    import shutil
    cc = shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise RuntimeError("No compiler found on PATH")
    console.print(f"[yellow]Using system compiler:[/yellow] {cc}")
    return Toolchain(
        name="system",
        version="detected",
        root=Path(cc).parent.parent,
        env={"CC": Path(cc).name},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _marker(install_dir: Path) -> Path:
    return install_dir / ".kbuild_installed"


def _download(url: str, dest: Path, console: Console):
    if dest.exists():
        console.print(f"  [dim]Archive already downloaded: {dest.name}[/dim]")
        return
    download_file(url, dest, console)
    console.print(f"[green]✓[/green] Downloaded: [dim]{dest.name}[/dim]")


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pin_checksum(descriptor_path: str, console: Console) -> str:
    """Download the toolchain archive, compute its sha256, write it into the descriptor.

    Edits only the checksum lines, so comments and formatting survive.
    """
    import re
    with open(descriptor_path) as f:
        text = f.read()
        desc = yaml.safe_load(text)
    url = desc.get("url")
    if not url:
        raise ValueError("Descriptor has no 'url' to hash.")
    cache = toolchain_cache()
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / url.split("/")[-1]
    _download(url, archive, console)
    digest = file_digest(archive, "sha256")

    new = re.sub(r'(?m)^(\s*algorithm:\s*).*$', r'\1sha256', text, count=1)
    new = re.sub(r'(?m)^(\s*value:\s*)"?[0-9a-fA-F]*"?(\s*(#.*)?)$',
                 lambda m: f'{m.group(1)}"{digest}"', new, count=1)
    if new == text and digest not in text:
        raise ValueError("Could not find a 'checksum: value:' line to update.")
    with open(descriptor_path, "w") as f:
        f.write(new)
    return digest


def _verify(path: Path, algorithm: str, expected: str):
    actual = file_digest(path, algorithm)
    if actual != expected:
        raise ValueError(
            f"Checksum mismatch:\n  expected: {expected}\n  actual:   {actual}"
        )


def _extract(archive: Path, dest: Path, console: Console, strip_components: int = 0):
    console.print(f"  Extracting to: [dim]{dest}[/dim]")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        members = []
        for m in tf.getmembers():
            parts = Path(m.name).parts
            if len(parts) <= strip_components:
                continue                      # leading directory entry itself
            m.name = str(Path(*parts[strip_components:]))
            members.append(m)
        # filter="data" refuses absolute paths, ../ escapes and device nodes
        tf.extractall(dest, members=members, filter="data")
    console.print(f"[green]✓[/green] Extracted")
