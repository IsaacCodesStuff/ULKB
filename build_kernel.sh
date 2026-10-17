#!/usr/bin/env bash
# ULKB entry point: the equivalent of the Samsung tree's build_kernel.sh.
#
#   ./build_kernel.sh t440-dev            build builds/t440-dev.yaml
#   ./build_kernel.sh builds/foo.yaml     build that file
#   ./build_kernel.sh t440-dev -j4        extra options go to `build` (see --help)
#   ./build_kernel.sh --list              list the build files
#   ./build_kernel.sh validate builds/    run any other ULKB command
#
# Everything is relative to the folder this script is in, so it works from anywhere.

set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import click, rich, yaml' 2>/dev/null; then
    echo "ULKB needs a few Python packages. Install them with:" >&2
    echo "    $PYTHON -m pip install -r requirements.txt" >&2
    exit 1
fi

COMMANDS=" build validate validate-configs pin-toolchain list-builds "

if [ $# -eq 0 ]; then
    exec "$PYTHON" -m builder --help
fi

case "$1" in
    --list)
        shift
        exec "$PYTHON" -m builder list-builds "$@" ;;
    -h|--help)
        exec "$PYTHON" -m builder --help ;;
esac

# A known command (validate, pin-toolchain, ...) is passed straight through.
if [[ "$COMMANDS" == *" $1 "* ]]; then
    exec "$PYTHON" -m builder "$@"
fi

# Otherwise the first argument names a build: "t440-dev" or "builds/t440-dev.yaml".
name="$1"; shift
if [ -f "$name" ]; then
    file="$name"
elif [ -f "builds/${name%.yaml}.yaml" ]; then
    file="builds/${name%.yaml}.yaml"
else
    echo "No build called '$name'. Available builds:" >&2
    "$PYTHON" -m builder list-builds >&2 || true
    exit 1
fi

exec "$PYTHON" -m builder build "$file" "$@"
