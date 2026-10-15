"""
matrix.py — Matrix build expansion.

Takes a matrix section from a build definition and expands it
into a list of individual build configs, one per combination.
"""

import copy
import itertools
from typing import Any


def expand_matrix(base_config: dict) -> list[dict]:
    """Expand the 'matrix' section of a build config into individual configs.

    Each combination of matrix dimensions produces one config dict.
    The matrix values are deep-merged into copies of the base config.

    Example matrix section:
        matrix:
          scheduler: [rem, ics, vanilla-eevdf]
          compiler:  [neutron-23, google-clang-23]

    Produces 6 configs: 3 schedulers × 2 compilers.

    Returns:
        List of expanded config dicts. If no matrix section, returns [base_config].
    """
    matrix = base_config.get("matrix")
    if not matrix:
        return [base_config]

    # Build list of (dimension_name, [values]) pairs
    dimensions = [(k, v) for k, v in matrix.items()]
    keys   = [d[0] for d in dimensions]
    values = [d[1] for d in dimensions]

    configs = []
    for combo in itertools.product(*values):
        c = copy.deepcopy(base_config)
        # Remove matrix key — expanded configs don't carry it
        c.pop("matrix", None)

        for key, val in zip(keys, combo):
            _apply_matrix_dimension(c, key, val)

        # Update name to reflect the combination
        suffix = "-".join(str(v) for v in combo)
        c["meta"]["name"] = f"{c['meta']['name']}-{suffix}"

        configs.append(c)

    return configs


def _apply_matrix_dimension(config: dict, dimension: str, value: Any):
    """Apply a single matrix dimension value to a config dict."""

    if dimension == "scheduler":
        # Scheduler matrix: swap the patch series
        config["patches"] = [f"patches/{value}/"]
        config["config"]["fragments"] = [
            f"configs/{value}/scheduler.config",
            *[
                f for f in config["config"].get("fragments", [])
                if "scheduler.config" not in f
            ],
        ]

    elif dimension == "compiler":
        # Compiler matrix: swap the toolchain descriptor
        config["toolchain"]["descriptor"] = f"toolchains/{value}.yaml"

    elif dimension == "kernel":
        # Kernel version matrix: treat value as a branch name or tag
        config["kernel"]["source"]["branch"] = value

    else:
        # Generic: try to set as a top-level or nested key
        # e.g. dimension "build.jobs" with value 4
        parts = dimension.split(".")
        target = config
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
