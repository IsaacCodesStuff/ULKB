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

from .download import download_file
from .paths import source_dir, downloads_dir
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn


# Kernel sources live at the project root as kernel-<version>/ (see paths.py).


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

    dest = source_dir(cfg)           # kernel-<version>/ unless kernel.source.directory says otherwise

    if (dest / ".git").exists():
        _check_same_repo(dest, repo)
        console.print(f"[green]✓[/green] Repo exists — fetching latest: [dim]{dest}[/dim]")
        _run(
            ["git", "fetch", "--progress", "origin", branch, f"--depth={depth}"],
            cwd=dest,
            console=console,
            live=True,
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
                "git", "clone", "--progress",
                "--depth", str(depth),
                "--branch", branch,
                "--single-branch",
                repo,
                str(dest),
            ],
            console=console,
            live=True,
        )

    commit = _git_rev(dest)
    console.print(f"[green]✓[/green] Source ready at [bold]{dest}[/bold] (HEAD: [dim]{commit[:12]}[/dim])")
    return dest


def _check_same_repo(dest: Path, repo: str):
    """Refuse to reuse a folder that was cloned from a different repository."""
    def norm(url: str) -> str:
        return url.strip().rstrip("/").removesuffix(".git").lower()

    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=dest, capture_output=True, text=True,
    )
    have = result.stdout.strip()
    if result.returncode == 0 and norm(have) != norm(repo):
        raise RuntimeError(
            f"{dest} was cloned from {have}, but this build wants {repo}.\n"
            "Set kernel.source.directory in the build file to give this source its own folder."
        )


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
    downloads_dir().mkdir(parents=True, exist_ok=True)
    archive_path = downloads_dir() / filename

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

    return _extract_tarball(archive_path, source_dir(cfg), console)


def _download_with_progress(url: str, dest: Path, console: Console):
    download_file(url, dest, console)


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


def _extract_tarball(archive: Path, dest: Path, console: Console) -> Path:
    """Extract into dest, dropping the archive's own top-level folder (linux-7.2/)."""
    console.print(f"  Extracting: [dim]{archive.name}[/dim]")
    if dest.exists():
        console.print(f"[green]✓[/green] Already extracted: [dim]{dest}[/dim]")
        return dest
    with tarfile.open(archive) as tf:
        members = []
        for m in tf.getmembers():
            parts = Path(m.name).parts
            if len(parts) < 2:
                continue                      # the top-level folder entry itself
            m.name = str(Path(*parts[1:]))
            members.append(m)
        tf.extractall(dest, members=members, filter="data")
    console.print(f"[green]✓[/green] Extracted to: [dim]{dest}[/dim]")
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list, console: Console, cwd=None, live: bool = False):
    """Run a subprocess. Raises on failure.

    With live=True (git clone/fetch) git's own progress lines ("Receiving
    objects: 42%") are shown on a spinner line so a slow network is visible,
    and a connection that stays below 1 KB/s for 60 s is aborted instead of
    hanging forever.
    """
    console.print(f"  [dim]$ {' '.join(str(c) for c in cmd)}[/dim]")
    if not live:
        result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
        if result.returncode != 0:
            console.print(f"[red]{escape(result.stderr)}[/red]")
            raise RuntimeError(
                f"Command failed (exit {result.returncode}): {' '.join(str(c) for c in cmd)}"
            )
        return

    env = dict(os.environ, GIT_HTTP_LOW_SPEED_LIMIT="1000", GIT_HTTP_LOW_SPEED_TIME="60")
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    tail, buf = [], ""
    with console.status("[bold]git[/bold] starting…", spinner="dots") as status:
        while True:
            ch = proc.stderr.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line = buf.strip()
                buf = ""
                if line:
                    tail.append(line)
                    del tail[:-20]
                    status.update(f"[bold]git[/bold] {escape(line)}")
            else:
                buf += ch
    code = proc.wait()
    if code != 0:
        console.print(f"[red]{escape(chr(10).join(tail))}[/red]")
        raise RuntimeError(
            f"Command failed (exit {code}): {' '.join(str(c) for c in cmd)}"
        )