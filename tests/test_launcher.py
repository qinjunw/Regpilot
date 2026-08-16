from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_cmd_launcher_runs_service_in_same_terminal(self) -> None:
        launcher = ROOT / "run_regulation_agent.cmd"

        text = launcher.read_text(encoding="utf-8")
        normalized = text.lower()

        self.assertIn('cd /d "%~dp0"', normalized)
        self.assertIn('set "pythonpath=%cd%\\src', normalized)
        self.assertNotIn('repo_src', normalized)
        self.assertNotIn('%cd%\\..\\src', normalized)
        self.assertIn("python -m regulation_agent", normalized)
        self.assertNotIn("start ", normalized)


if __name__ == "__main__":
    unittest.main()
