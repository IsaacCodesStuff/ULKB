"""
source.py — Kernel source fetching.

Supports two source types:
  - git:     clone/fetch a branch from a remote repo
  - tarball: download and extract a .tar.xz / .tar.gz from kernel.org
"""

import os
import subprocess
import hashlib
import tarfile
import urllib.request
from pathlib import Path
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn


# Where fetched kernel sources live — relative to cwd
SOURCES_DIR = Path("sources")


def fetch_kernel_source(source_config: dict, console: Console) -> Path:
    """Fetch kernel source according to source_config.

    Returns:
        Path to the kernel source directory (ready for make).
    """
    src_type = source_config["type"]

    if src_type == "git":
        return _fetch_git(source_config, console)
    elif src_type == "tarball":
        return _fetch_tarball(source_config, console)
    else:
        raise ValueError(f"Unknown source type: {src_type}")


# ─────────────────────────────────────────────────────────────────────────────
# Git
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_git(cfg: dict, console: Console) -> Path:
    repo   = cfg["repo"]
    branch = cfg["branch"]
    depth  = cfg.get("depth", 1)  # shallow clone by default

    # Derive a local directory name from the repo URL
    repo_name = repo.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = SOURCES_DIR / repo_name

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    if (dest / ".git").exists():
        console.print(f"[green]✓[/green] Repo exists — fetching latest: [dim]{dest}[/dim]")
        _run(
            ["git", "fetch", "origin", branch, f"--depth={depth}"],
            cwd=dest,
            console=console,
        )
        _run(
            ["git", "checkout", "--detach", "FETCH_HEAD"],   # works for branches and tags
            cwd=dest,
            console=console,
        )
    else:
        console.print(f"  Cloning [bold]{repo}[/bold] branch [bold]{branch}[/bold]")
        console.print(f"  Destination: [dim]{dest}[/dim]")
        _run(
            [
                "git", "clone",
                "--depth", str(depth),
                "--branch", branch,
                "--single-branch",
                repo,
                str(dest),
            ],
            console=console,
        )

    commit = _git_rev(dest)
    console.print(f"[green]✓[/green] Source ready at [bold]{dest}[/bold] (HEAD: [dim]{commit[:12]}[/dim])")
    return dest


def _git_rev(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Tarball
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_tarball(cfg: dict, console: Console) -> Path:
    url      = cfg["url"]
    expected = cfg.get("checksum", {})

    filename = url.split("/")[-1]
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = SOURCES_DIR / filename

    # Download if not cached
    if archive_path.exists():
        console.print(f"[green]✓[/green] Cached tarball: [dim]{archive_path}[/dim]")
    else:
        console.print(f"  Downloading: [bold]{url}[/bold]")
        _download_with_progress(url, archive_path, console)

    # Verify checksum if provided
    if expected.get("value"):
        console.print("  Verifying checksum…")
        _verify_checksum(archive_path, expected["algorithm"], expected["value"])
        console.print("[green]✓[/green] Checksum OK")

    # Extract
    extract_dir = _extract_tarball(archive_path, console)
    return extract_dir


def _download_with_progress(url: str, dest: Path, console: Console):
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {dest.name}", total=None)

        def reporthook(block_num, block_size, total_size):
            if total_size > 0:
                progress.update(task, total=total_size, advance=block_size)
            else:
                progress.update(task, advance=block_size)

        part = dest.with_name(dest.name + ".part")   # never leave a truncated archive
        urllib.request.urlretrieve(url, part, reporthook)
        part.replace(dest)


def _verify_checksum(path: Path, algorithm: str, expected: str):
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected:
        raise ValueError(
            f"Checksum mismatch for {path.name}:\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}"
        )


def _extract_tarball(archive: Path, console: Console) -> Path:
    console.print(f"  Extracting: [dim]{archive.name}[/dim]")
    with tarfile.open(archive) as tf:
        # Detect top-level directory inside the archive
        members = tf.getnames()
        top = members[0].split("/")[0] if members else archive.stem
        dest = SOURCES_DIR / top
        if dest.exists():
            console.print(f"[green]✓[/green] Already extracted: [dim]{dest}[/dim]")
        else:
            tf.extractall(SOURCES_DIR, filter="data")
            console.print(f"[green]✓[/green] Extracted to: [dim]{dest}[/dim]")
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list, console: Console, cwd=None):
    """Run a subprocess, streaming output to console. Raises on failure."""
    console.print(f"  [dim]$ {' '.join(str(c) for c in cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        console.print(f"[red]{escape(result.stderr)}[/red]")
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(str(c) for c in cmd)}"
        )
