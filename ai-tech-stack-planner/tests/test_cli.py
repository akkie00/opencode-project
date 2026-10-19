"""End to end tests for the command line (ai-tech-stack-planner/src/main.py).

The CLI is run in a real subprocess so that the tests also cover the exit
codes and make sure the program never hangs.  A timeout guards every run.
"""

import subprocess
import sys
import unittest
from pathlib import Path

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: Repository root, so the CLI can be started exactly as the README shows.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The script, relative to the repository root.
SCRIPT = "ai-tech-stack-planner/src/main.py"

#: Every run is killed after this many seconds.
TIMEOUT = 30


def run_cli(*args, stdin=None):
    """Run the CLI in a subprocess and return the completed process."""
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=stdin,
    )


class TestCli(unittest.TestCase):
    """The command line as the user sees it."""

    def test_flag_based_run_succeeds_and_prints_a_report(self):
        result = run_cli(
            "--goal",
            "chat",
            "--budget",
            "30",
            "--timeline",
            "4",
            "--constraints",
            "local_only",
        )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Ollama", result.stdout)
        self.assertTrue(result.stdout.strip(), "the report should not be empty")

    def test_readme_example_runs_successfully(self):
        result = run_cli(
            "--goal",
            "rag",
            "--budget",
            "100",
            "--timeline",
            "4",
            "--constraints",
            "needs_api",
        )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertTrue(result.stdout.strip(), "the report should not be empty")

    def test_unknown_constraint_exits_with_code_two_and_a_message(self):
        result = run_cli(
            "--goal",
            "chat",
            "--budget",
            "30",
            "--timeline",
            "4",
            "--constraints",
            "bogus",
        )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, f"output:\n{output}")
        self.assertNotIn("Traceback", output, "the CLI must not crash")
        self.assertTrue(output.strip(), "the CLI should explain the problem")
        self.assertTrue(
            "bogus" in output.lower() or "constraint" in output.lower(),
            f"the error message should mention the bad constraint:\n{output}",
        )

    def test_bare_invocation_does_not_hang(self):
        # Without a terminal the CLI cannot ask questions; it has to stop
        # cleanly instead of waiting for input forever.
        result = run_cli(stdin=subprocess.DEVNULL)

        self.assertIn(result.returncode, (0, 2), f"output:\n{result.stdout}{result.stderr}")
        self.assertNotIn("Traceback", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
