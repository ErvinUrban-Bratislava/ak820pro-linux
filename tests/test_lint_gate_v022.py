"""v0.22.0 regression tests — Static Analysis Gate (REVIEW.md §14.2).

Locks the gate's contract, offline and hardware-free:

1. `scripts/lint.sh` exists, is executable, and runs ruff + ruff format --check
   + mypy end-to-end (skipped when the tools are not installed).
2. The pyproject baseline (line-length, target-version, mypy scope) is committed
   so local and CI read the same config — and `[project.scripts]` survived the
   config insertion intact (a real trap hit during this iteration).
3. The CI workflow runs the same script (no local/CI drift).
4. transport.py's `__all__` pins the v0.19.0 re-export contract — the lint
   gate's F401 rule must never delete backward-compat identity re-exports.
"""

import os
import shutil
import subprocess
import sys
import unittest

try:
    import tomllib
except ImportError:  # Python 3.10 CI leg
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tools_available():
    def present(cmd):
        if shutil.which(cmd):
            return True
        mod = "ruff" if cmd == "ruff" else "mypy"
        import importlib.util

        return importlib.util.find_spec(mod) is not None

    return present("ruff") and present("mypy")


class TestLintScript(unittest.TestCase):
    def test_lint_sh_exists_and_executable(self):
        path = os.path.join(ROOT, "scripts", "lint.sh")
        self.assertTrue(os.path.isfile(path), "scripts/lint.sh missing")
        self.assertTrue(os.access(path, os.X_OK), "scripts/lint.sh must be executable")

    def test_lint_sh_covers_all_three_gates(self):
        with open(os.path.join(ROOT, "scripts", "lint.sh"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("check ak820/", body)
        self.assertIn("format --check ak820/", body)
        self.assertIn("mypy", body)
        self.assertIn("set -euo pipefail", body)

    @unittest.skipUnless(_tools_available(), "ruff/mypy not installed")
    def test_gate_end_to_end_green(self):
        """The whole gate must pass right now — this is the lock."""
        env = dict(os.environ)
        local_bin = os.path.expanduser("~/.local/bin")
        env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
        res = subprocess.run(
            ["bash", os.path.join(ROOT, "scripts", "lint.sh")],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(res.returncode, 0, msg=res.stdout + res.stderr)
        self.assertIn("GREEN", res.stdout)


@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
class TestPyprojectBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            cls.cfg = tomllib.load(f)

    def test_ruff_baseline_committed(self):
        ruff = self.cfg["tool"]["ruff"]
        self.assertEqual(ruff["line-length"], 100)
        self.assertEqual(ruff["target-version"], "py310")

    def test_ruff_lint_select_pinned(self):
        select = self.cfg["tool"]["ruff"]["lint"]["select"]
        # pyflakes + isort + pyupgrade are the core of the gate; a missing
        # family here means someone silently shrank the gate.
        for family in ("F", "I", "UP", "RUF"):
            self.assertIn(family, select)

    def test_mypy_scoped_to_package(self):
        mypy = self.cfg["tool"]["mypy"]
        self.assertEqual(mypy["files"], ["ak820"])
        self.assertEqual(mypy["python_version"], "3.10")

    def test_project_scripts_intact(self):
        """Regression: the [tool.mypy] block must not swallow [project.scripts]."""
        scripts = self.cfg["project"]["scripts"]
        self.assertEqual(scripts["ak820"], "ak820.cli:main")
        self.assertEqual(scripts["ak820-ctrl"], "ak820.cli:main")
        # mypy would have warned "Unrecognized option" if these leaked into it
        self.assertNotIn("ak820", self.cfg["tool"]["mypy"])


class TestCiRunsSameGate(unittest.TestCase):
    def test_ci_lint_job_invokes_lint_sh(self):
        path = os.path.join(ROOT, ".github", "workflows", "ci.yml")
        with open(path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("scripts/lint.sh", body)
        self.assertIn("pip install ruff mypy", body.replace('"pillow>=9.0"', "").replace("'", '"'))


class TestTransportReexportContract(unittest.TestCase):
    """§18 backward-compat: identity helpers stay importable (and patchable)
    through ak820.transport; __all__ pins them against the gate's F401."""

    def test_all_pins_discovery_reexports(self):
        from ak820 import transport

        for name in ("_parse_usage_page", "_read_hid_identity", "find_hidraw_devices"):
            self.assertIn(name, transport.__all__)
            self.assertTrue(callable(getattr(transport, name)))

    def test_reexports_are_identity_not_copies(self):
        from ak820 import discovery, transport

        self.assertIs(transport.find_hidraw_devices, discovery.find_hidraw_devices)
        self.assertIs(transport.find_keyboard_hidraw, discovery.find_keyboard_hidraw)


class TestBehaviorNeutralGuards(unittest.TestCase):
    """The gate fixes must not have changed runtime semantics at the two
    riskiest rewrite sites (m4 align retry, run child exit codes)."""

    def test_m4_align_retry_still_two_attempts_then_protocol_error(self):
        from unittest import mock

        from ak820.device import AK820Pro
        from ak820.errors import ProtocolError

        dev = AK820Pro.__new__(AK820Pro)
        with mock.patch.object(dev, "align_data_pipe", return_value=(0, False)) as m:
            with self.assertRaises(ProtocolError):
                dev.upload_frames([b"\x00" * 256], align=True)
        self.assertEqual(m.call_count, 2, "m4 contract: exactly one retry, no more")

    def test_run_child_check_false_keeps_exit_code_contract(self):
        # check=False is explicit now; a failing child must NOT raise CalledProcessError
        import ak820.handlers as handlers

        with open(os.path.join(ROOT, "ak820", "handlers.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("subprocess.run(cmd, check=False)", src)
        self.assertTrue(hasattr(handlers, "cmd_run"))


if __name__ == "__main__":
    unittest.main()
