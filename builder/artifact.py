"""
artifact.py — Artifact collection and build metadata generation.

After a successful build, copies specified artifacts to the output
directory and writes a build-info.json with full reproducibility metadata.
"""

import os
import json
import shutil
import hashlib
import datetime
import subprocess
from pathlib import Path
from rich.console import Console


def collect_artifacts(source_dir: Path, config: dict, console: Console):
    """Copy build artifacts to the output directory and write metadata.

    Raises:
        RuntimeError: If a specified artifact isn't found after the build.
    """
    output_dir  = Path(config["output"]["directory"])
    artifacts   = config["output"].get("artifacts", [])
    emit_meta   = config["output"].get("metadata", True)
    compress    = config["output"].get("compress", False)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Copy artifacts ─────────────────────────────────────────────────────
    copied = []
    if artifacts:
        for rel_path in artifacts:
            src = source_dir / rel_path
            if not src.exists():
                # Try common alternative locations
                src = _find_artifact(source_dir, Path(rel_path).name)

            if src and src.exists():
                dest = output_dir / src.name
                shutil.copy2(src, dest)
                console.print(f"[green]✓[/green] {src.name} → [dim]{dest}[/dim]")
                copied.append(dest)
            else:
                raise RuntimeError(
                    f"Artifact not found after build: {rel_path}\n"
                    f"  Searched in: {source_dir}"
                )
    else:
        # Auto-collect common kernel output files if no list specified
        for name in ["vmlinux", "bzImage", "Image", "Image.gz", "System.map", "zImage"]:
            found = _find_artifact(source_dir, name)
            if found:
                dest = output_dir / name
                shutil.copy2(found, dest)
                console.print(f"[green]✓[/green] {name} (auto) → [dim]{dest}[/dim]")
                copied.append(dest)

    # Keep the exact .config that produced these artifacts
    final_cfg = source_dir / ".config"
    if final_cfg.is_file():
        shutil.copy2(final_cfg, output_dir / "config")
        console.print(f"[green]✓[/green] config → [dim]{output_dir / 'config'}[/dim]")

    release = _kernel_release(source_dir)
    if release != "unknown":
        console.print(f"  Kernel release: [bold]{release}[/bold]")

    if compress:
        _compress_artifacts(copied, console)

    # ── Write metadata ─────────────────────────────────────────────────────
    if emit_meta:
        meta_path = output_dir / "build-info.json"
        metadata  = _build_metadata(source_dir, config, copied)
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
        console.print(f"[green]✓[/green] Metadata → [dim]{meta_path}[/dim]")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_artifact(source_dir: Path, name: str) -> Path | None:
    """Search common kernel build output locations for a named file."""
    candidates = [
        source_dir / name,
        source_dir / "arch" / "x86" / "boot" / name,
        source_dir / "arch" / "x86_64" / "boot" / name,
        source_dir / "arch" / "arm64" / "boot" / name,
        source_dir / "arch" / "arm" / "boot" / name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _compress_artifacts(paths: list[Path], console: Console):
    """Gzip-compress artifact files in-place (appends .gz)."""
    import gzip
    for p in paths:
        gz_path = p.with_suffix(p.suffix + ".gz")
        with open(p, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        p.unlink()
        console.print(f"  Compressed: [dim]{gz_path.name}[/dim]")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _kernel_release(source_dir: Path) -> str:
    """The version string the built kernel reports (also the /lib/modules/<name> dir)."""
    rel = source_dir / "include" / "config" / "kernel.release"
    try:
        return rel.read_text().strip()
    except OSError:
        return "unknown"


def _git_info(source_dir: Path) -> dict:
    """Extract git metadata from source_dir if it's a git repo."""
    info = {}
    if not (source_dir / ".git").exists():
        return info
    for key, cmd in [
        ("commit",  ["git", "rev-parse", "HEAD"]),
        ("branch",  ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("describe",["git", "describe", "--tags", "--always"]),
    ]:
        r = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True)
        if r.returncode == 0:
            info[key] = r.stdout.strip()
    return info


def _build_metadata(source_dir: Path, config: dict, artifacts: list[Path]) -> dict:
    """Build the build-info.json payload."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "builder":   "universal-linux-builder",
        "kernel": {
            "source":  config["kernel"]["source"],
            "git":     _git_info(source_dir),
            "release": _kernel_release(source_dir),
        },
        "target": config["target"],
        "toolchain": {
            "descriptor": config["toolchain"]["descriptor"],
        },
        "scheduler": config["meta"].get("name", "unknown"),
        "config": {
            "base":      config["config"]["base"],
            "mode":      config["config"]["mode"],
            "fragments": config["config"].get("fragments", []),
        },
        "patches": config.get("patches", []),
        "build_flags": config["build"].get("flags", []),
        "artifacts": [
            {
                "name":   a.name,
                "size":   a.stat().st_size,
                "sha256": _sha256(a),
            }
            for a in artifacts if a.exists()
        ],
    }
