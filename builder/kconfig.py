"""
kconfig.py — Kernel configuration management.

Handles:
  1. Base config generation (defconfig, localmodconfig)
  2. Config fragment merging (scripts/kconfig/merge_config.sh)
  3. make olddefconfig to resolve new symbols
"""

import os
import re
import subprocess
import shutil
from pathlib import Path
from rich.console import Console
from rich.table import Table

from .toolchain import Toolchain


def configure_kernel(
    source_dir: Path,
    config: dict,
    toolchain: Toolchain,
    console: Console,
):
    """Configure the kernel in source_dir according to build config.

    Steps:
      1. Generate base .config (defconfig or localmodconfig)
      2. Merge any config fragments
      3. Run olddefconfig to finalize

    Raises:
        RuntimeError: If any make step fails.
    """
    source_dir  = Path(source_dir).resolve()   # make runs with cwd=source_dir, so paths must be absolute
    arch        = config["target"]["arch"]
    base        = config["config"]["base"]
    mode        = config["config"]["mode"]
    fragments   = config["config"].get("fragments", [])
    build_flags = config["build"].get("flags", [])
    env         = toolchain.make_env()

    # ── Step 1: Base config ────────────────────────────────────────────────────
    if is_config_file(base):
        # Start from an existing .config (e.g. your distro's /proc/config.gz)
        src_cfg = Path(base).expanduser()
        if not src_cfg.is_file():
            raise FileNotFoundError(
                f"config.base points at a file that does not exist: {src_cfg}\n"
                "Create it on the machine you are building for, e.g.:\n"
                "  zcat /proc/config.gz > " + str(src_cfg) + "\n"
                "and commit it if you build with GitHub Actions."
            )
        console.print(f"  Base config: [bold]{src_cfg}[/bold] [dim](existing .config)[/dim]")
        shutil.copy2(src_cfg, source_dir / ".config")
        _make(source_dir, arch, ["olddefconfig"], env, build_flags, console)
    else:
        # localmodconfig trims an existing .config, so always generate the base first.
        console.print(f"  Base config: [bold]{base}[/bold]")
        _make(source_dir, arch, [base], env, build_flags, console)

    lsmod_file = config["config"].get("lsmod_file")
    if mode == "dev" and (lsmod_file or shutil.which("lsmod")):
        # Drop every module the host isn't using — much faster dev builds.
        # It reads the modules loaded on the machine running the build, so it
        # only makes sense when you build on the machine you will boot.
        # Set config.lsmod_file to a saved `lsmod` output to build elsewhere.
        console.print("  [dim]Mode: dev — running make localmodconfig[/dim]")
        lm_env = dict(env)
        if lsmod_file:
            if not os.path.isfile(os.path.expanduser(lsmod_file)):
                raise FileNotFoundError(f"config.lsmod_file not found: {lsmod_file}")
            lm_env["LSMOD"] = os.path.abspath(os.path.expanduser(lsmod_file))
        _make(source_dir, arch, ["localmodconfig"], lm_env, build_flags, console)

    # ── Step 2: Merge fragments ────────────────────────────────────────────────
    # merge_config.sh runs with cwd=source_dir, so fragment paths must be absolute
    existing_fragments = [os.path.abspath(f) for f in fragments if os.path.isfile(f)]
    missing_fragments  = [f for f in fragments if not os.path.isfile(f)]

    # A per-build version suffix keeps this kernel's modules in their own
    # /lib/modules/<release> directory, away from your distro kernel's.
    localversion = config["config"].get("localversion")
    if localversion:
        lv_file = source_dir / ".ulkb-localversion.config"
        lv_file.write_text(
            f'CONFIG_LOCALVERSION="{localversion}"\n'
            "# CONFIG_LOCALVERSION_AUTO is not set\n"
        )
        existing_fragments.append(str(lv_file.resolve()))

    if missing_fragments:
        for mf in missing_fragments:
            console.print(f"  [yellow]⚠[/yellow] Config fragment not found (skipping): {mf}")

    if existing_fragments:
        console.print(f"  Merging {len(existing_fragments)} config fragment(s)…")
        merge_script = source_dir / "scripts" / "kconfig" / "merge_config.sh"

        if not merge_script.exists():
            raise RuntimeError(
                f"merge_config.sh not found at {merge_script} — "
                "is the kernel source complete?"
            )

        result = subprocess.run(
            [
                "bash", str(merge_script),
                "-m",               # merge into existing .config
                ".config",
                *existing_fragments,
            ],
            cwd=source_dir,
            env={**env, "ARCH": _kernel_arch(arch)},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"merge_config.sh failed:\n{result.stderr}")

        console.print(f"[green]✓[/green] Fragments merged")
    else:
        if not fragments:
            console.print("  [dim]No config fragments specified.[/dim]")

    # ── Step 3: olddefconfig ───────────────────────────────────────────────────
    console.print("  Running make olddefconfig…")
    _make(source_dir, arch, ["olddefconfig"], env, build_flags, console)

    # ── Step 4: Audit — did the kernel actually accept what the fragments asked for? ──
    if existing_fragments:
        problems = audit_fragments(existing_fragments, source_dir / ".config")
        _print_audit(problems, console)
        if problems and config["config"].get("strict"):
            raise RuntimeError(
                f"{len(problems)} requested config symbol(s) were not honoured "
                "(config.strict is on)."
            )

    console.print("[green]✓[/green] Kernel configured")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make(
    source_dir: Path,
    arch: str,
    targets: list,
    env: dict,
    extra_flags: list,
    console: Console,
):
    cmd = ["make", f"ARCH={_kernel_arch(arch)}", *extra_flags, *targets]
    console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")

    result = subprocess.run(
        cmd,
        cwd=source_dir,
        env=env,
        stdin=subprocess.DEVNULL,   # never block waiting for a prompt
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Print last 20 lines of stderr to avoid flooding the terminal
        lines = result.stderr.strip().splitlines()
        tail  = "\n".join(lines[-20:])
        raise RuntimeError(f"make {' '.join(targets)} failed:\n{tail}")


def _kernel_arch(arch: str) -> str:
    """Map build-definition arch strings to Linux ARCH= values."""
    mapping = {
        "x86_64":  "x86_64",
        "arm64":   "arm64",
        "arm":     "arm",
        "riscv64": "riscv",
    }
    return mapping.get(arch, arch)


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers and audit
# ─────────────────────────────────────────────────────────────────────────────

def is_config_file(base: str) -> bool:
    """True if 'config.base' names a .config file rather than a make target."""
    return "/" in base or base.endswith(".config")


_SET_LINE     = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
_NOT_SET_LINE = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")


def parse_config(path) -> dict:
    """Parse a .config or fragment into {symbol: value}. 'is not set' becomes 'n'."""
    result = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            m = _SET_LINE.match(line)
            if m:
                result[m.group(1)] = m.group(2)
                continue
            m = _NOT_SET_LINE.match(line)
            if m:
                result[m.group(1)] = "n"
    return result


def audit_fragments(fragment_paths: list, dotconfig) -> list:
    """Compare what the fragments requested with what ended up in .config.

    Returns a list of (symbol, requested, actual, reason) for every mismatch.
    A tristate asked for as 'm' but built in ('y') is fine and not reported.
    """
    final = parse_config(dotconfig)
    wanted = {}
    for fp in fragment_paths:            # later fragments override earlier ones
        wanted.update(parse_config(fp))

    problems = []
    for sym, req in sorted(wanted.items()):
        if sym not in final:
            if req == "n":
                continue                 # asking for 'off' on an unknown symbol is harmless
            problems.append((sym, req, "(absent)",
                             "not in the final config: misspelt, removed in this kernel, or hidden by an unmet dependency"))
            continue
        act = final[sym]
        if act == req or (req == "m" and act == "y"):
            continue
        if req == "n":
            reason = "something else selects it"
        elif act == "n":
            reason = "a dependency is not enabled"
        else:
            reason = "a dependency limits it"
        problems.append((sym, req, act, reason))
    return problems


def _print_audit(problems: list, console: Console):
    if not problems:
        console.print("[green]✓[/green] Config audit: every fragment symbol was honoured")
        return
    table = Table(title=f"Config audit — {len(problems)} symbol(s) not honoured",
                  border_style="yellow")
    table.add_column("Symbol")
    table.add_column("Asked")
    table.add_column("Got")
    table.add_column("Why", style="dim")
    for sym, req, act, why in problems:
        table.add_row(sym, req, act, why)
    console.print(table)
