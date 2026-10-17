# ULKB — Universal Linux Kernel Builder

A declarative, YAML-driven Linux kernel build system for iterative scheduler
research and development.

Built to support the upcoming **REM** and **ICS** scheduler projects (both
EEVDF extensions), but general enough to build any upstream or downstream
Linux kernel tree.

> **Status:** early development (~15%). The CLI, validators and build
> pipeline exist; no real kernel build has been run end to end yet.

## Features

- YAML build definitions — describe what to build, not how
- Toolchain management — kernel.org LLVM (stable), Neutron Clang (rolling), Google Clang
- Config fragment merging — compose kernel configs from reusable fragments
- Patch series management — apply ordered patch sets with conflict detection
- Incremental builds with ccache
- Build metadata — every artifact set ships with a `build-info.json`
- Matrix builds — scheduler × compiler × kernel version (`builder/matrix.py`, not wired into the CLI yet)
- GitHub Actions: validation on every push, and a manual "Build kernel" workflow

## Supported targets

| Arch   | Use case                   |
| ------ | -------------------------- |
| x86_64 | ThinkPad (REM/ICS dev)     |
| arm64  | Android / ACK (Rem kernel) |

## Project layout

The layout follows the Samsung kernel trees: one folder holds everything, the
kernel source stays untouched, and all output goes to `out/`.

```
ULKB/
├── build_kernel.sh        entry point:  ./build_kernel.sh t440-dev
├── builds/                build definitions (YAML)
├── configs/               config fragments, base configs, saved lsmod files
├── toolchains/            toolchain descriptors
├── builder/               the build system itself (Python)
├── vendor/                patch sets (REM, ICS) and out-of-tree code
│
├── kernel-7.2/            kernel source, fetched on demand, never built in
├── build/                 downloaded toolchains and the ccache
└── out/<build>/
    ├── obj/               make O=... output: .config, objects, bzImage
    └── dist/              what you take away: bzImage, config, modules, build-info.json
```

The last three are generated and not committed. Because the build happens in
`out/<build>/obj` (`make O=`), one source folder serves every build file and
nothing needs `make mrproper` in between.

## Quick start

```bash
pip install -r requirements.txt
./build_kernel.sh --list
./build_kernel.sh vanilla-eevdf --dry-run
```

Before the first real build, pin the toolchain checksum (downloads the
archive once):

```bash
./build_kernel.sh pin-toolchain toolchains/kernel-org-llvm-23.yaml
```

`build_kernel.sh` works from any folder and accepts every command below, so
`./build_kernel.sh validate builds/` is the same as `python -m builder validate builds/`
run from the project root.

## First target: ThinkPad T440

See [docs/T440.md](docs/T440.md) for the two T440 builds, the setup steps,
how to read the config audit and how to install the result.

Builds run on GitHub with **Actions → Build kernel → Run workflow** (`.github/workflows/build.yml`).

## Commands

| Command                              | What it does                                              |
| ------------------------------------ | --------------------------------------------------------- |
| `NAME` or `build FILE [--dry-run]`   | Fetch source, toolchain, patch, configure, build, collect |
| `list-builds`                        | List the definitions in `builds/`                         |
| `validate PATH`                      | Validate build definitions                                |
| `validate --kind toolchain PATH`     | Validate toolchain descriptors                            |
| `validate-configs PATH`              | Lint kernel config fragments                              |
| `pin-toolchain FILE`                 | Download a toolchain and record its sha256                |

Build files can also set `config.base` to an existing `.config` file,
`config.localversion` for a per-build version suffix, `config.strict` to fail
on config mismatches, and `output.install_modules` to stage modules.

Where things go can be overridden, but the defaults are what you want:
`kernel.source.directory` names the source folder (default `kernel-<version>`),
`output.directory` the artifact folder (default `out/<name>/dist`), and the
`KBUILD_TOOLCHAIN_CACHE` environment variable moves the toolchain folder.

## Build definition example

```yaml
meta:
  name: rem-thinkpad

kernel:
  source:
    type: git
    repo: https://github.com/IsaacCodesStuff/linux
    branch: rem-dev

target:
  arch: x86_64

toolchain:
  descriptor: toolchains/kernel-org-llvm-23.yaml

config:
  base: defconfig
  fragments:
    - configs/thinkpad/base.config

build:
  jobs: auto
  ccache: true
  flags:
    - LLVM=1
    - LLVM_IAS=1

# Artifacts land in out/rem-thinkpad/dist by default.
```

## Toolchains

| Descriptor                          | Kind                | Use for                          |
| ----------------------------------- | ------------------- | -------------------------------- |
| `toolchains/kernel-org-llvm-23.yaml` | Stable, downloaded  | Baselines and benchmarks         |
| `toolchains/neutron-clang.yaml`      | Rolling, installed  | Trying new compiler features     |
| `toolchains/google-clang-23.yaml`    | Unverified          | Android/ACK work (later)         |
