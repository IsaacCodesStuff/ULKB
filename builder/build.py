"""
build.py — Kernel build invocation.

Calls make with the right flags, handles ccache setup,
determines job count, and streams build output live.
"""

import os
import subprocess
import time
import shutil
from pathlib import Path
from rich.console import Console
from rich.markup import escape

from .toolchain import Toolchain
from .kconfig import _kernel_arch
from .paths import obj_dir as default_obj_dir, ccache_dir as default_ccache_dir


def build_kernel(
    source_dir: Path,
    config: dict,
    toolchain: Toolchain,
    console: Console,
    obj_dir: Path = None,
):
    """Run make to build the kernel, out of tree (make O=obj_dir).

    Raises:
        RuntimeError: If make exits non-zero.
    """
    arch        = config["target"]["arch"]
    obj_dir     = Path(obj_dir if obj_dir is not None else default_obj_dir(config)).resolve()
    build_cfg   = config["build"]
    build_flags = build_cfg.get("flags", [])
    targets     = build_cfg.get("targets", ["vmlinux", "modules", "bzImage"])

    jobs    = _resolve_jobs(build_cfg.get("jobs", "auto"))
    ccache  = build_cfg.get("ccache", True)
    env     = toolchain.make_env()
    cc_args = []

    # ── ccache setup ────────────────────────────────────────────────────────
    if ccache:
        if shutil.which("ccache"):
            ccache_dir = Path(
                build_cfg.get("ccache_dir") or default_ccache_dir()
            ).expanduser().resolve()
            ccache_dir.mkdir(parents=True, exist_ok=True)
            env["CCACHE_DIR"] = str(ccache_dir)
            # Wrap the compiler with ccache. With LLVM=1 the kernel Makefile
            # assigns CC itself, which overrides the environment, so these
            # must go on the make command line.
            cc_args = [
                f"CC=ccache {Path(env.get('CC', 'clang')).name}",
                f"HOSTCC=ccache {Path(env.get('HOSTCC', 'clang')).name}",
            ]
            console.print(f"  ccache: [green]enabled[/green] ({ccache_dir})")
        else:
            console.print("  [yellow]⚠[/yellow] ccache not found on PATH — building without it")

    # ── Build command ────────────────────────────────────────────────────────
    cmd = [
        "make",
        f"ARCH={_kernel_arch(arch)}",
        f"O={obj_dir}",
        f"-j{jobs}",
        *build_flags,
        *cc_args,
        *targets,
    ]

    cross = config["target"].get("cross_compile")
    if cross:
        cmd.insert(1, f"CROSS_COMPILE={cross}")

    console.print(f"  Jobs: [bold]{jobs}[/bold]")
    console.print(f"  Targets: [bold]{' '.join(targets)}[/bold]")
    console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")
    console.print()

    start = time.monotonic()
    _stream_make(cmd, source_dir, env, console)
    elapsed = time.monotonic() - start

    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    console.print(f"\n[green]✓[/green] Build finished in [bold]{minutes}m {seconds}s[/bold]")

    # ── Optional: stage modules into the output directory (never into / ) ────
    if config["output"].get("install_modules", False):
        stage = Path(config["output"]["directory"]).resolve() / "rootfs"
        stage.mkdir(parents=True, exist_ok=True)
        mod_cmd = [
            "make",
            f"ARCH={_kernel_arch(arch)}",
            f"O={obj_dir}",
            f"-j{jobs}",
            *build_flags,
            f"INSTALL_MOD_PATH={stage}",
            "INSTALL_MOD_STRIP=1",     # strip debug info: modules go from GBs to MBs
            "modules_install",
        ]
        console.print(f"  Staging modules → [dim]{stage}[/dim]")
        _stream_make(mod_cmd, source_dir, env, console)
        console.print("[green]✓[/green] Modules staged")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_jobs(jobs_setting) -> int:
    """Resolve 'auto' or an integer job count to an actual number."""
    if jobs_setting == "auto" or jobs_setting is None:
        return os.cpu_count() or 4
    return int(jobs_setting)


def _stream_make(cmd: list, cwd: Path, env: dict, console: Console):
    """Run make, streaming stderr/stdout to the console in real time.

    We stream output because kernel builds take minutes — silent progress
    bars are not useful here. Show the last error lines on failure.
    """
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    lines_seen = []
    try:
        for line in process.stdout:
            line = line.rstrip()
            lines_seen.append(line)
            # Show errors and warnings; suppress noisy CC/LD lines in normal flow
            if any(tag in line for tag in ("error:", "warning:", "fatal:", "ld:", "undefined")):
                console.print(f"  [yellow]{escape(line)}[/yellow]")
            else:
                # Print progress lines dimmed
                console.print(f"  [dim]{escape(line)}[/dim]", highlight=False)
    finally:
        process.wait()

    if process.returncode != 0:
        tail = "\n".join(lines_seen[-30:])
        raise RuntimeError(f"make failed (exit {process.returncode}).\nLast output:\n{tail}")
