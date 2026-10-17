"""
ULKB — Universal Linux Kernel Builder
CLI entry point — run with: python -m builder <command> [options]
"""

import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.markup import escape
from rich import print as rprint

from .config import (
    load_build_config, validate_build_config, ConfigError,
    load_yaml_mapping, validate_toolchain_descriptor, validate_config_fragment,
)
from .source import fetch_kernel_source
from .toolchain import resolve_toolchain
from .patch import apply_patches
from .kconfig import configure_kernel
from .build import build_kernel
from .artifact import collect_artifacts
from .paths import obj_dir as obj_dir_for

console = Console()

BANNER = """[bold cyan]ULKB — Universal Linux Kernel Builder[/bold cyan]
[dim]Declarative Linux kernel build system for REM / ICS scheduler research[/dim]"""


@click.group()
def cli():
    """Universal Linux kernel build system."""
    console.print(Panel(BANNER, border_style="cyan", padding=(0, 2)))


# ─────────────────────────────────────────────────────────────────────────────
# build
# ─────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("build_file", metavar="BUILD_FILE")
@click.option(
    "--toolchain-override",
    metavar="TOOLCHAIN_YAML",
    default=None,
    help="Override the toolchain descriptor specified in the build file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Parse and validate the build file, then exit without building.",
)
@click.option(
    "--jobs", "-j",
    default=None,
    type=int,
    help="Number of parallel build jobs (overrides build file setting).",
)
@click.option(
    "--no-ccache",
    is_flag=True,
    default=False,
    help="Disable ccache even if enabled in the build file.",
)
def build(build_file, toolchain_override, dry_run, jobs, no_ccache):
    """Build a kernel from a YAML build definition file."""

    # ── 1. Load and validate config ──────────────────────────────────────────
    console.rule("[bold]Step 1 — Loading build definition[/bold]")
    try:
        config = load_build_config(build_file)
        validate_build_config(config)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {escape(str(e))}")
        sys.exit(1)
    except FileNotFoundError:
        console.print(f"[bold red]Error:[/bold red] Build file not found: {build_file}")
        sys.exit(1)

    console.print(f"[green]✓[/green] Loaded: [bold]{config['meta']['name']}[/bold]")
    console.print(f"  {config['meta'].get('description', '')}")

    # Apply CLI overrides
    if toolchain_override:
        config["toolchain"]["descriptor"] = toolchain_override
        console.print(f"  [dim]Toolchain override: {toolchain_override}[/dim]")
    if jobs:
        config.setdefault("build", {})["jobs"] = jobs
    if no_ccache:
        config.setdefault("build", {})["ccache"] = False

    if dry_run:
        console.print("\n[yellow]Dry run — stopping before fetch.[/yellow]")
        _print_build_summary(config)
        return

    _print_build_summary(config)

    # ── 2. Fetch kernel source ────────────────────────────────────────────────
    console.rule("[bold]Step 2 — Kernel source[/bold]")
    try:
        source_dir = fetch_kernel_source(config["kernel"]["source"], console)
        obj_dir = obj_dir_for(config)
        console.print(f"  Build output: [dim]{obj_dir}[/dim] [dim](source stays untouched)[/dim]")
    except Exception as e:
        console.print(f"[bold red]Source fetch failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # ── 3. Resolve toolchain ──────────────────────────────────────────────────
    console.rule("[bold]Step 3 — Toolchain[/bold]")
    try:
        toolchain = resolve_toolchain(config["toolchain"], console)
    except Exception as e:
        console.print(f"[bold red]Toolchain error:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # ── 4. Apply patches ──────────────────────────────────────────────────────
    console.rule("[bold]Step 4 — Patches[/bold]")
    try:
        apply_patches(source_dir, config.get("patches", []), console)
    except Exception as e:
        console.print(f"[bold red]Patch failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # ── 5. Configure ──────────────────────────────────────────────────────────
    console.rule("[bold]Step 5 — Kernel configuration[/bold]")
    try:
        configure_kernel(source_dir, config, toolchain, console, obj_dir)
    except Exception as e:
        console.print(f"[bold red]Config failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # ── 6. Build ──────────────────────────────────────────────────────────────
    console.rule("[bold]Step 6 — Build[/bold]")
    try:
        build_kernel(source_dir, config, toolchain, console, obj_dir)
    except Exception as e:
        console.print(f"[bold red]Build failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    # ── 7. Collect artifacts ──────────────────────────────────────────────────
    console.rule("[bold]Step 7 — Artifacts[/bold]")
    try:
        collect_artifacts(source_dir, config, console, obj_dir)
    except Exception as e:
        console.print(f"[bold red]Artifact collection failed:[/bold red] {escape(str(e))}")
        sys.exit(1)

    console.rule()
    console.print(f"\n[bold green]✓ Build complete![/bold green]")
    console.print(f"  Artifacts in: [bold]{config['output']['directory']}[/bold]\n")


# ─────────────────────────────────────────────────────────────────────────────
# validate
# ─────────────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path", metavar="PATH")
@click.option(
    "--kind", type=click.Choice(["build", "toolchain"]), default="build",
    show_default=True,
    help="What the YAML files describe: build definitions or toolchain descriptors.",
)
def validate(path, kind):
    """Validate build definitions or toolchain descriptors without building.

    PATH can be a single YAML file or a directory of YAML files.
    """
    import os
    import glob

    files = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.yaml")))
        if not files:
            console.print(f"[yellow]No .yaml files found in {path}[/yellow]")
            return
    elif os.path.isfile(path):
        files = [path]
    else:
        console.print(f"[bold red]Error:[/bold red] Path not found: {path}")
        sys.exit(1)

    errors = []
    for f in files:
        try:
            if kind == "toolchain":
                validate_toolchain_descriptor(load_yaml_mapping(f))
            else:
                validate_build_config(load_build_config(f))
            console.print(f"[green]✓[/green] {f}")
        except ConfigError as e:
            console.print(f"[red]✗[/red] {f}: {escape(str(e))}")
            errors.append(f)
        except Exception as e:
            console.print(f"[red]✗[/red] {f}: Unexpected error — {escape(str(e))}")
            errors.append(f)

    if errors:
        console.print(f"\n[bold red]{len(errors)} file(s) failed validation.[/bold red]")
        sys.exit(1)
    else:
        console.print(f"\n[bold green]All {len(files)} file(s) valid.[/bold green]")


# ─────────────────────────────────────────────────────────────────────────────
# validate-configs
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("validate-configs")
@click.argument("path", metavar="PATH")
def validate_configs(path):
    """Check kernel config fragments (*.config) for lines merge_config.sh can't handle."""
    import os
    import glob

    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "**", "*.config"), recursive=True))
    elif os.path.isfile(path):
        files = [path]
    else:
        console.print(f"[bold red]Error:[/bold red] Path not found: {path}")
        sys.exit(1)

    if not files:
        console.print(f"[yellow]No .config fragments found in {path}[/yellow]")
        return

    bad = 0
    for f in files:
        problems = validate_config_fragment(f)
        if problems:
            bad += 1
            console.print(f"[red]✗[/red] {f}")
            for pr in problems:
                console.print(f"    {pr}")
        else:
            console.print(f"[green]✓[/green] {f}")

    if bad:
        console.print(f"\n[bold red]{bad} fragment(s) have problems.[/bold red]")
        sys.exit(1)
    console.print(f"\n[bold green]All {len(files)} fragment(s) valid.[/bold green]")


# ─────────────────────────────────────────────────────────────────────────────
# pin-toolchain
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("pin-toolchain")
@click.argument("descriptor", metavar="TOOLCHAIN_YAML")
def pin_toolchain(descriptor):
    """Download a toolchain and write its sha256 into the descriptor.

    Run this once after changing a toolchain's URL, then compare the printed
    digest with the publisher's checksum list before committing.
    """
    from .toolchain import pin_checksum
    try:
        digest = pin_checksum(descriptor, console)
    except Exception as e:
        console.print(f"[bold red]Pin failed:[/bold red] {escape(str(e))}")
        sys.exit(1)
    console.print(f"[green]✓[/green] Wrote sha256 to {descriptor}")
    console.print(f"  {digest}")
    console.print("  [yellow]Verify this against the publisher's signed checksum list.[/yellow]")


# ─────────────────────────────────────────────────────────────────────────────
# list-builds
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("list-builds")
@click.option(
    "--dir", "builds_dir",
    default="builds",
    show_default=True,
    help="Directory containing build definition files.",
)
def list_builds(builds_dir):
    """List all available build definitions."""
    import os
    import glob
    from rich.table import Table

    files = sorted(glob.glob(os.path.join(builds_dir, "*.yaml")))
    if not files:
        console.print(f"[yellow]No build definitions found in {builds_dir}/[/yellow]")
        return

    table = Table(title="Available Builds", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Arch")
    table.add_column("File", style="dim")

    for f in files:
        try:
            config = load_build_config(f)
            table.add_row(
                config.get("meta", {}).get("name", "?"),
                config.get("meta", {}).get("description", ""),
                config.get("target", {}).get("arch", "?"),
                os.path.basename(f),
            )
        except Exception:
            table.add_row("[red]parse error[/red]", "", "", os.path.basename(f))

    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_build_summary(config: dict):
    """Print a compact summary of what's about to be built."""
    from rich.table import Table

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()

    src = config["kernel"]["source"]
    src_str = f"{src.get('repo', src.get('url', '?'))} @ {src.get('branch', src.get('tag', '?'))}"

    t.add_row("Kernel:", src_str)
    t.add_row("Arch:", config["target"]["arch"])
    t.add_row("Toolchain:", config["toolchain"].get("descriptor", "?"))

    fragments = config.get("config", {}).get("fragments", [])
    t.add_row("Config fragments:", str(len(fragments)))

    patches = config.get("patches", [])
    patch_count = sum(
        1 if isinstance(p, str) else len(list(__import__("glob").glob(p + "/*.patch")))
        for p in patches
    ) if patches else 0
    t.add_row("Patches:", str(patch_count) if patch_count else "none")

    t.add_row("Output:", config["output"]["directory"])

    console.print(t)
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    cli()


if __name__ == "__main__":
    main()
