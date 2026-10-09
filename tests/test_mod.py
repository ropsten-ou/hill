"""hill's mod, which palace's Claude sessions load (`claude --plugin-dir`):
its own tests, in TypeScript, run under `claude plugin test`, which runs no
model. Skipped where `claude` isn't installed (a runner's fence)."""

import shutil
import subprocess

import pytest

from hill import claude


def test_the_mod_is_a_plugin_palace_can_give_claude():
    assert (claude.MOD / ".claude-plugin" / "plugin.json").is_file()
    assert (claude.MOD / "hooks" / "hooks.json").is_file()


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude isn't installed")
def test_the_mods_own_tests_pass():
    for command in (["plugin", "validate"], ["plugin", "test"]):
        done = subprocess.run(["claude", *command, str(claude.MOD)], capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
