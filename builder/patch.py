"""
patch.py — Patch series application.

Applies patches to the kernel source tree in order.
Supports:
  - Single patch files (.patch)
  - Directories of patches (applied in lexicographic order)
  - git am (preferred — respects git history)
  - patch -p1 (fallback for non-git sources)
"""

import os
import glob
import subprocess
from pathlib import Path
from rich.console import Console


def apply_patches(source_dir: Path, patches_config: list, console: Console):
    """Apply all patches listed in patches_config to source_dir.

    patches_config can be:
      - A list of .patch file paths
      - A list of directory paths (all *.patch files inside, sorted)
      - A mixed list of both

    Raises:
        RuntimeError: If any patch fails to apply.
    """
    if not patches_config:
        console.print("  [dim]No patches to apply.[/dim]")
        return

    patch_files = _resolve_patch_list(patches_config)

    if not patch_files:
        console.print("  [dim]Patch list resolved to zero files.[/dim]")
        return

    console.print(f"  Applying {len(patch_files)} patch(es)…")

    # Prefer git am if source is a git repo
    use_git_am = (source_dir / ".git").exists()

    for pf in patch_files:
        _apply_single(pf, source_dir, use_git_am, console)

    console.print(f"[green]✓[/green] All patches applied cleanly")


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_patch_list(patches_config: list) -> list[Path]:
    """Expand directories and globs into a sorted list of .patch paths."""
    resolved = []
    for entry in patches_config:
        p = Path(entry)
        if p.is_dir():
            files = sorted(glob.glob(str(p / "*.patch")))
            resolved.extend(Path(f) for f in files)
        elif p.is_file() and p.suffix == ".patch":
            resolved.append(p)
        elif "*" in str(p):
            resolved.extend(sorted(Path(f) for f in glob.glob(str(p))))
        else:
            # Entry in config but file doesn't exist yet — skip with warning
            # (normal during early development when patches/ dirs are empty)
            pass
    return resolved


_GIT_IDENT = {
    "GIT_COMMITTER_NAME":  "ULKB",
    "GIT_COMMITTER_EMAIL": "ulkb@localhost",
    "GIT_AUTHOR_NAME":     "ULKB",
    "GIT_AUTHOR_EMAIL":    "ulkb@localhost",
}


def _is_mailbox_patch(patch: Path) -> bool:
    """True for `git format-patch` output (what `git am` needs), False for plain diffs."""
    with open(patch, "r", errors="replace") as f:
        head = f.read(4096)
    first = head.splitlines()[0] if head else ""
    return first.startswith("From ") and "\nSubject:" in head


def _apply_single(patch: Path, source_dir: Path, use_git_am: bool, console: Console):
    console.print(f"  [dim]Applying: {patch.name}[/dim]")

    if use_git_am and not _is_mailbox_patch(patch):
        # A plain `diff -u` / `git diff` file: git am cannot read it, git apply can.
        result = subprocess.run(
            ["git", "apply", "--3way", str(patch.resolve())],
            cwd=source_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git apply failed for {patch.name}:\n{result.stderr}"
            )
        # Commit it so the tree is clean again: a later `git am` refuses to
        # run on a dirty index.
        for cmd in (["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", f"ULKB: apply {patch.name}"]):
            r = subprocess.run(cmd, cwd=source_dir, capture_output=True, text=True,
                               env={**os.environ, **_GIT_IDENT})
            if r.returncode != 0:
                raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stderr}")
    elif use_git_am:
        result = subprocess.run(
            ["git", "am", "--3way", str(patch.resolve())],
            cwd=source_dir,
            env={**os.environ, **_GIT_IDENT},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Abort the failed am so the tree isn't left in a bad state
            subprocess.run(["git", "am", "--abort"], cwd=source_dir,
                           capture_output=True)
            raise RuntimeError(
                f"git am failed for {patch.name}:\n{result.stderr}"
            )
    else:
        result = subprocess.run(
            ["patch", "-p1", "--forward", "-i", str(patch.resolve())],
            cwd=source_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"patch -p1 failed for {patch.name}:\n{result.stderr}"
            )

    console.print(f"  [green]✓[/green] {patch.name}")
