"""Tests for config parsing, the post-build audit, and the configure/build flow."""
import pytest
from pathlib import Path
from rich.console import Console

from builder import build, kconfig
from builder.config import ConfigError, load_build_config, validate_build_config
from builder.kconfig import audit_fragments, is_config_file, parse_config
from builder.toolchain import Toolchain


def write(path, text):
    path.write_text(text)
    return str(path)


def test_parse_config_handles_all_forms(tmp_path):
    f = write(tmp_path / "c", 'CONFIG_A=y\nCONFIG_B=m\n# CONFIG_C is not set\nCONFIG_D="x y"\n# comment\n')
    assert parse_config(f) == {"CONFIG_A": "y", "CONFIG_B": "m", "CONFIG_C": "n", "CONFIG_D": '"x y"'}


def test_audit_clean(tmp_path):
    frag = write(tmp_path / "f", "CONFIG_A=y\nCONFIG_B=m\n# CONFIG_C is not set\n")
    final = write(tmp_path / ".config", "CONFIG_A=y\nCONFIG_B=m\n# CONFIG_C is not set\n")
    assert audit_fragments([frag], final) == []


def test_audit_module_built_in_is_fine(tmp_path):
    frag = write(tmp_path / "f", "CONFIG_B=m\n")
    final = write(tmp_path / ".config", "CONFIG_B=y\n")
    assert audit_fragments([frag], final) == []


def test_audit_flags_unknown_dropped_and_forced(tmp_path):
    frag = write(tmp_path / "f", "CONFIG_TYPO=y\nCONFIG_NEEDS_DEP=y\n# CONFIG_FORCED is not set\nCONFIG_LOW=y\n")
    final = write(tmp_path / ".config",
                  "# CONFIG_NEEDS_DEP is not set\nCONFIG_FORCED=y\nCONFIG_LOW=m\n")
    got = {p[0]: p for p in audit_fragments([frag], final)}
    assert set(got) == {"CONFIG_TYPO", "CONFIG_NEEDS_DEP", "CONFIG_FORCED", "CONFIG_LOW"}
    assert "not in the final config" in got["CONFIG_TYPO"][3]
    assert "dependency" in got["CONFIG_NEEDS_DEP"][3]
    assert "selects" in got["CONFIG_FORCED"][3]


def test_audit_later_fragment_wins(tmp_path):
    a = write(tmp_path / "a", "CONFIG_A=m\n")
    b = write(tmp_path / "b", "CONFIG_A=y\n")
    final = write(tmp_path / ".config", "CONFIG_A=y\n")
    assert audit_fragments([a, b], final) == []


def test_is_config_file():
    assert is_config_file("configs/local/arch.config")
    assert is_config_file("arch.config")
    assert not is_config_file("defconfig")
    assert not is_config_file("localmodconfig")


def test_localversion_validation(tmp_path):
    base = {"meta": {"name": "x"}, "kernel": {"source": {"type": "tarball", "url": "u"}},
            "target": {"arch": "x86_64"}, "toolchain": {"descriptor": __file__},
            "build": {}, "output": {"directory": "d"}, "config": {"localversion": "-ok_1.2"}}
    validate_build_config(base)
    base["config"]["localversion"] = "bad version"
    with pytest.raises(ConfigError):
        validate_build_config(base)


# ── A fake kernel tree lets us exercise configure + build without a real kernel ──

MAKEFILE = r"""
defconfig:
	@echo "CONFIG_FROM_DEFCONFIG=y" > .config
olddefconfig:
	@touch .config
localmodconfig:
	@echo "# trimmed" >> .config
bzImage:
	@mkdir -p arch/x86/boot include/config && echo img > arch/x86/boot/bzImage && echo 9.9.9-test > include/config/kernel.release
modules:
	@true
modules_install:
	@mkdir -p $(INSTALL_MOD_PATH)/lib/modules/9.9.9-test && echo "strip=$(INSTALL_MOD_STRIP)" > $(INSTALL_MOD_PATH)/lib/modules/9.9.9-test/note
"""

MERGE = """#!/bin/bash
# stand-in for merge_config.sh: append every fragment line to .config
shift 2
cat "$@" >> .config
"""


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "linux"
    (src / "scripts" / "kconfig").mkdir(parents=True)
    (src / "Makefile").write_text(MAKEFILE)
    (src / "scripts" / "kconfig" / "merge_config.sh").write_text(MERGE)
    tcdir = tmp_path / "tc"
    (tcdir / "bin").mkdir(parents=True)
    return src, Toolchain("t", "1", tcdir, {})


def cfg(base, out, **config_extra):
    return {
        "target": {"arch": "x86_64"},
        "config": {"base": base, "mode": "release", "fragments": [], **config_extra},
        "build": {"flags": [], "ccache": False, "targets": ["bzImage"], "jobs": 1},
        "output": {"directory": str(out), "install_modules": True},
        "kernel": {"source": {"type": "git"}},
        "meta": {"name": "t"}, "toolchain": {"descriptor": "d"},
    }


def test_base_file_is_copied_and_localversion_merged(tree, tmp_path):
    src, tc = tree
    base = tmp_path / "arch.config"
    base.write_text("CONFIG_FROM_ARCH=y\n")
    frag = tmp_path / "frag.config"
    frag.write_text("CONFIG_X=y\n")
    c = cfg(str(base), tmp_path / "dist", localversion="-ulkb-test", fragments=[str(frag)])
    kconfig.configure_kernel(src, c, tc, Console())
    text = (src / ".config").read_text()
    assert "CONFIG_FROM_ARCH=y" in text and "CONFIG_FROM_DEFCONFIG" not in text
    assert "CONFIG_X=y" in text
    assert 'CONFIG_LOCALVERSION="-ulkb-test"' in text


def test_missing_base_file_gives_helpful_error(tree, tmp_path):
    src, tc = tree
    c = cfg(str(tmp_path / "nope.config"), tmp_path / "dist")
    with pytest.raises(FileNotFoundError, match="/proc/config.gz"):
        kconfig.configure_kernel(src, c, tc, Console())


def test_strict_mode_fails_on_unhonoured_symbol(tree, tmp_path):
    src, tc = tree
    frag = tmp_path / "frag.config"
    frag.write_text("CONFIG_X=y\n")
    # the fake olddefconfig keeps .config as is, so make the final config disagree
    (src / "Makefile").write_text(MAKEFILE.replace(
        "olddefconfig:\n\t@touch .config",
        "olddefconfig:\n\t@sed -i 's/CONFIG_X=y/# CONFIG_X is not set/' .config"))
    c = cfg("defconfig", tmp_path / "dist", strict=True, fragments=[str(frag)])
    with pytest.raises(RuntimeError, match="not honoured"):
        kconfig.configure_kernel(src, c, tc, Console())


def test_build_stages_modules_and_reports_release(tree, tmp_path):
    src, tc = tree
    out = tmp_path / "dist"
    c = cfg("defconfig", out)
    build.build_kernel(src, c, tc, Console())
    note = out / "rootfs" / "lib" / "modules" / "9.9.9-test" / "note"
    assert note.read_text().strip() == "strip=1"

    from builder.artifact import _kernel_release
    assert _kernel_release(src) == "9.9.9-test"


def test_make_output_with_brackets_does_not_crash_rich(tmp_path):
    """make error lines look like '*** [file:96: obj] Error 1' — not Rich markup."""
    import io
    from builder.build import _stream_make
    console = Console(file=io.StringIO())
    with pytest.raises(RuntimeError, match="make failed"):
        _stream_make(["bash", "-c", "echo 'make[3]: *** [Makefile:96: foo.o] Error 1'; exit 2"],
                     tmp_path, {}, console)
