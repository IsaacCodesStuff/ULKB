# universal-linux-builder

A declarative, YAML-driven Linux kernel build system designed for
iterative scheduler research and development.

Built to support the upcoming REM and ICS scheduler projects, but
general enough to build any upstream or downstream Linux kernel tree.

## Features

- YAML build definitions — describe what to build, not how
- Toolchain management — Neutron Clang, Google Clang, GCC, kernel.org LLVM
- Config fragment merging — compose kernel configs from reusable fragments
- Patch series management — apply ordered patch sets with conflict detection
- Incremental builds with ccache — iterate fast without full rebuilds
- Build metadata — every artifact ships with a build-info.json
- Matrix builds — scheduler × compiler × kernel version comparison builds
- GitHub Actions integration — CI/CD out of the box

## Supported Targets

| Arch   | Use Case                   |
| ------ | -------------------------- |
| x86_64 | ThinkPad (REM/ICS dev)     |
| arm64  | Android / ACK (Rem kernel) |

## Quick Start

\`\`\`bash
pip install -r requirements.txt
python -m builder build builds/vanilla-eevdf.yaml
\`\`\`

## Build Definition Example

\`\`\`yaml
kernel:
source:
type: git
repo: https://github.com/IsaacCodesStuff/linux
branch: rem-dev

target:
arch: x86_64

toolchain:
provider: neutron
version: "23.0.1"

config:
base: defconfig
fragments: - configs/rem/scheduler.config - configs/thinkpad/base.config

build:
jobs: auto
ccache: true
flags: - LLVM=1 - LLVM_IAS=1

output:
directory: dist/
\`\`\`
