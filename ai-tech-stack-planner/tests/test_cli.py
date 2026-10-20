"""End to end tests for the command line (ai-tech-stack-planner/src/main.py).

The CLI is run in a real subprocess so that the tests also cover the exit
codes and make sure the program never hangs.  A timeout guards every run.

The Phase 2 tests add the optional LLM reasoning pass: a tiny local HTTP
server pretends to be an OpenAI compatible API so that no real tokens are
spent and no real network call is made.

The Phase 3 tests add persistence: a plan is saved as one JSON file in a
temporary folder (--plans-dir), loaded back, listed and deleted.  The
repository's own plans/ folder is never touched by the tests.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

#: The profile used by the LLM tests; it always produces an "Ollama" stack.
LLM_PROFILE_ARGS = (
    "--goal",
    "chat",
    "--budget",
    "30",
    "--timeline",
    "4",
    "--constraints",
    "local_only",
)

#: The profile used by the plan tests; the goal "rag" shows up in the list.
PLAN_PROFILE_ARGS = (
    "--goal",
    "rag",
    "--budget",
    "200",
    "--timeline",
    "4",
    "--constraints",
    "needs_api",
)

#: Matches a stack line of the report, for example
#: "  - Ollama [model] - run a model locally (~$0/mo)".
STACK_LINE_RE = re.compile(r"^  - (.+?) \[.+?\]", re.MULTILINE)

#: Matches the cost line of the report.
COST_LINE_RE = re.compile(r"^Estimated monthly cost: .*$", re.MULTILINE)


def stack_names(text):
    """The component names of a printed report."""
    return STACK_LINE_RE.findall(text)


def cost_line(text):
    """The cost line of a printed report ("" when there is none)."""
    found = COST_LINE_RE.findall(text)
    return found[0] if found else ""


def run_cli(*args, stdin=None, env=None):
    """Run the CLI in a subprocess and return the completed process.

    `env` is merged over the current environment, so the subprocess still has
    a normal environment (PATH and friends) and only the variables given in
    the test are changed.
    """
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        stdin=stdin,
        env={**os.environ, **(env or {})},
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


# --- A fake OpenAI compatible API -------------------------------------------

#: The chat completion the fake server answers with.
FAKE_COMPLETION = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {
                        "summary": "A local stack fits the budget.",
                        "risks": ["No redundancy."],
                        "suggestions": ["Add a backup."],
                        "confidence": "high",
                    }
                )
            }
        }
    ],
    "usage": {"prompt_tokens": 138, "completion_tokens": 96, "total_tokens": 234},
}


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Answers every POST with a canned chat completion and records it."""

    def do_POST(self):  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )

        payload = json.dumps(FAKE_COMPLETION).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """Keep the test output quiet."""


def start_fake_server():
    """Start the fake API on a free local port; return (server, thread)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_fake_server(server, thread):
    """Shut the fake API down again."""
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class TestCliLlmPass(unittest.TestCase):
    """The optional LLM reasoning pass (Phase 2)."""

    def test_default_run_does_not_use_the_llm(self):
        # The fake API records every request it gets: without --llm the tool
        # must not talk to it at all, even when the environment points at it.
        server, thread = start_fake_server()
        try:
            port = server.server_address[1]
            result = run_cli(
                *LLM_PROFILE_ARGS,
                env={
                    "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
                    "LLM_API_KEY": "test-key",
                    "LLM_MODEL": "test-model",
                },
            )
            requests = list(server.requests)
        finally:
            stop_fake_server(server, thread)

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Ollama", result.stdout, "the rule based report should be printed")
        self.assertNotIn(
            "LLM reasoning",
            result.stdout,
            f"without --llm there should be no LLM block:\n{result.stdout}",
        )
        self.assertNotIn(
            "LLM tokens",
            result.stdout,
            f"without --llm there should be no token line:\n{result.stdout}",
        )
        self.assertEqual(
            requests,
            [],
            "without --llm the tool must not call the API",
        )

    def test_llm_without_api_key_warns_and_still_succeeds(self):
        result = run_cli(
            *LLM_PROFILE_ARGS,
            "--llm",
            env={"LLM_API_KEY": ""},
        )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn(
            "Ollama",
            result.stdout,
            "the rule based report should still be printed",
        )
        self.assertIn(
            "LLM pass skipped",
            result.stderr,
            f"the warning should be on stderr:\n{result.stderr}",
        )
        self.assertIn(
            "LLM_API_KEY",
            result.stderr,
            f"the warning should name the API key variable:\n{result.stderr}",
        )
        self.assertNotIn(
            "LLM reasoning",
            result.stdout,
            f"no LLM block should be printed without a key:\n{result.stdout}",
        )

    def test_llm_pass_prints_advice_and_token_line(self):
        server, thread = start_fake_server()
        try:
            port = server.server_address[1]
            result = run_cli(
                *LLM_PROFILE_ARGS,
                "--llm",
                env={
                    "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
                    "LLM_API_KEY": "test-key",
                    "LLM_MODEL": "test-model",
                },
            )
            requests = list(server.requests)
        finally:
            stop_fake_server(server, thread)

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn(
            "LLM reasoning",
            result.stdout,
            f"the LLM block should be printed:\n{result.stdout}",
        )
        self.assertIn(
            "A local stack fits the budget.",
            result.stdout,
            f"the summary should be printed:\n{result.stdout}",
        )
        self.assertIn(
            "No redundancy.",
            result.stdout,
            f"the risks should be printed:\n{result.stdout}",
        )
        self.assertIn(
            "Add a backup.",
            result.stdout,
            f"the suggestions should be printed:\n{result.stdout}",
        )

        token_lines = [
            line for line in result.stdout.splitlines() if line.startswith("LLM tokens:")
        ]
        self.assertEqual(
            len(token_lines),
            1,
            f"expected exactly one token line, got {token_lines}",
        )
        self.assertIn(
            "138",
            token_lines[0],
            f"the token line should show the prompt tokens: {token_lines[0]}",
        )
        self.assertIn(
            "234",
            token_lines[0],
            f"the token line should show the total tokens: {token_lines[0]}",
        )

        self.assertEqual(
            len(requests),
            1,
            f"the API should be called exactly once, got {len(requests)} requests",
        )
        request = requests[0]
        self.assertEqual(
            request["path"],
            "/v1/chat/completions",
            "the request should go to the chat completions endpoint",
        )
        self.assertEqual(
            request["authorization"],
            "Bearer test-key",
            "the API key should be sent as a bearer token",
        )
        body = request["body"]
        self.assertIsInstance(body, dict, f"the request body should be JSON: {body!r}")
        for key in ("model", "messages", "response_format"):
            self.assertIn(key, body, f"the request body should have a {key!r} key: {body!r}")
        self.assertIn(
            "test-model",
            str(body["model"]),
            f"the model should come from LLM_MODEL: {body!r}",
        )

    def test_llm_failure_falls_back_to_the_rules(self):
        # Port 1 is closed, so the connection is refused right away.
        result = run_cli(
            *LLM_PROFILE_ARGS,
            "--llm",
            env={
                "LLM_BASE_URL": "http://127.0.0.1:1/v1",
                "LLM_API_KEY": "test-key",
                "LLM_TIMEOUT": "2",
            },
        )

        self.assertEqual(
            result.returncode,
            0,
            f"a failing LLM must not break the tool:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn(
            "Ollama",
            result.stdout,
            "the rule based report should still be printed",
        )
        self.assertIn(
            "LLM pass failed",
            result.stderr,
            f"the warning should be on stderr:\n{result.stderr}",
        )
        self.assertNotIn(
            "LLM reasoning",
            result.stdout,
            f"no LLM block should be printed after a failure:\n{result.stdout}",
        )


class TestCliPlans(unittest.TestCase):
    """Saving, loading, listing and deleting plans (Phase 3).

    Every test writes into a temporary folder (--plans-dir), so the
    repository's own plans/ folder is never touched.
    """

    def test_save_then_load_prints_the_same_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = run_cli(
                *PLAN_PROFILE_ARGS,
                "--save",
                "my-chat",
                "--plans-dir",
                tmp,
            )
            self.assertEqual(
                saved.returncode,
                0,
                f"stdout:\n{saved.stdout}\nstderr:\n{saved.stderr}",
            )

            # A loaded plan needs no input at all.
            loaded = run_cli(
                "--load",
                "my-chat",
                "--plans-dir",
                tmp,
                stdin=subprocess.DEVNULL,
            )
            self.assertEqual(
                loaded.returncode,
                0,
                f"stdout:\n{loaded.stdout}\nstderr:\n{loaded.stderr}",
            )

            self.assertTrue(
                stack_names(saved.stdout),
                f"the saved run should print a stack:\n{saved.stdout}",
            )
            self.assertEqual(
                stack_names(saved.stdout),
                stack_names(loaded.stdout),
                "the loaded plan should print the same components:\n"
                f"saved:\n{saved.stdout}\nloaded:\n{loaded.stdout}",
            )
            self.assertEqual(
                cost_line(saved.stdout),
                cost_line(loaded.stdout),
                "the loaded plan should print the same cost:\n"
                f"saved:\n{saved.stdout}\nloaded:\n{loaded.stdout}",
            )
            self.assertNotIn(
                "Choose a project goal",
                loaded.stdout,
                f"a loaded plan must not ask anything:\n{loaded.stdout}",
            )

    def test_save_writes_one_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                *PLAN_PROFILE_ARGS,
                "--save",
                "my-chat",
                "--plans-dir",
                tmp,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )

            files = sorted(Path(tmp).iterdir())
            self.assertEqual(
                len(files),
                1,
                f"expected exactly one file in {tmp}, got {files}",
            )
            self.assertEqual(
                files[0].stem,
                "my-chat",
                f"the file should be named after the plan: {files[0].name}",
            )

            data = json.loads(files[0].read_text(encoding="utf-8"))
            for key in (
                "schema_version",
                "name",
                "saved_at",
                "profile",
                "recommendation",
                "llm",
            ):
                self.assertIn(
                    key,
                    data,
                    f"the saved plan should have a {key!r} key: {sorted(data)}",
                )
            self.assertEqual(data["name"], "my-chat", "the plan should keep its name")
            self.assertEqual(
                data["schema_version"],
                1,
                f"the file should carry the schema version: {data['schema_version']!r}",
            )
            self.assertIsNone(
                data["llm"],
                "a plan saved without --llm has no advice attached",
            )

    def test_list_plans_shows_the_saved_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = run_cli(
                *PLAN_PROFILE_ARGS,
                "--save",
                "my-chat",
                "--plans-dir",
                tmp,
            )
            self.assertEqual(
                saved.returncode,
                0,
                f"stdout:\n{saved.stdout}\nstderr:\n{saved.stderr}",
            )

            result = run_cli("--list-plans", "--plans-dir", tmp)
            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )

            rows = [line for line in result.stdout.splitlines() if "my-chat" in line]
            self.assertEqual(
                len(rows),
                1,
                f"the plan should be listed exactly once:\n{result.stdout}",
            )
            self.assertIn(
                "rag",
                rows[0],
                f"the row should show the goal of the plan: {rows[0]!r}",
            )

    def test_list_plans_is_empty_when_there_are_no_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--list-plans", "--plans-dir", tmp)

            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertIn(
                "No saved plans",
                result.stdout,
                f"an empty folder should say so:\n{result.stdout}",
            )

    def test_delete_removes_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = run_cli(
                *PLAN_PROFILE_ARGS,
                "--save",
                "my-chat",
                "--plans-dir",
                tmp,
            )
            self.assertEqual(
                saved.returncode,
                0,
                f"stdout:\n{saved.stdout}\nstderr:\n{saved.stderr}",
            )

            deleted = run_cli("--delete", "my-chat", "--plans-dir", tmp)
            self.assertEqual(
                deleted.returncode,
                0,
                f"stdout:\n{deleted.stdout}\nstderr:\n{deleted.stderr}",
            )
            self.assertIn(
                "Deleted plan 'my-chat'.",
                deleted.stdout,
                f"the deletion should be confirmed:\n{deleted.stdout}",
            )

            listed = run_cli("--list-plans", "--plans-dir", tmp)
            self.assertNotIn(
                "my-chat",
                listed.stdout,
                f"the deleted plan should be gone:\n{listed.stdout}",
            )
            self.assertEqual(
                list(Path(tmp).iterdir()),
                [],
                "the plan file should be gone from the folder",
            )

    def test_load_of_a_missing_plan_exits_with_code_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                "--load",
                "no-such-plan",
                "--plans-dir",
                tmp,
                stdin=subprocess.DEVNULL,
            )
            output = result.stdout + result.stderr

            self.assertEqual(result.returncode, 2, f"output:\n{output}")
            self.assertIn(
                "Error:",
                result.stderr,
                f"the error should be on stderr:\n{result.stderr}",
            )
            self.assertNotIn("Traceback", output, "the CLI must not crash")

    def test_save_with_an_invalid_name_exits_with_code_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                *PLAN_PROFILE_ARGS,
                "--save",
                "bad/name",
                "--plans-dir",
                tmp,
            )
            output = result.stdout + result.stderr

            self.assertEqual(result.returncode, 2, f"output:\n{output}")
            self.assertIn(
                "Error:",
                result.stderr,
                f"the error should be on stderr:\n{result.stderr}",
            )
            self.assertNotIn("Traceback", output, "the CLI must not crash")
            self.assertEqual(
                list(Path(tmp).iterdir()),
                [],
                "a plan with a bad name must not be written",
            )

    def test_loading_a_plan_prints_the_saved_llm_advice_without_calling_the_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, thread = start_fake_server()
            try:
                port = server.server_address[1]
                env = {
                    "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
                    "LLM_API_KEY": "test-key",
                    "LLM_MODEL": "test-model",
                }
                saved = run_cli(
                    *LLM_PROFILE_ARGS,
                    "--llm",
                    "--save",
                    "my-chat",
                    "--plans-dir",
                    tmp,
                    env=env,
                )
                requests_after_save = list(server.requests)

                loaded = run_cli(
                    "--load",
                    "my-chat",
                    "--llm",
                    "--plans-dir",
                    tmp,
                    env=env,
                )
                requests_after_load = list(server.requests)
            finally:
                stop_fake_server(server, thread)

            self.assertEqual(
                saved.returncode,
                0,
                f"stdout:\n{saved.stdout}\nstderr:\n{saved.stderr}",
            )
            self.assertEqual(
                len(requests_after_save),
                1,
                "saving with --llm should call the API exactly once, got "
                f"{len(requests_after_save)} requests",
            )
            self.assertEqual(
                loaded.returncode,
                0,
                f"stdout:\n{loaded.stdout}\nstderr:\n{loaded.stderr}",
            )
            self.assertEqual(
                len(requests_after_load),
                len(requests_after_save),
                "loading must not call the API again - the advice was already paid for",
            )
            self.assertIn(
                "A local stack fits the budget.",
                loaded.stdout,
                f"the saved summary should be printed:\n{loaded.stdout}",
            )
            self.assertIn(
                "no new tokens were spent",
                loaded.stdout,
                f"the note about the saved advice should be printed:\n{loaded.stdout}",
            )

    def test_llm_refresh_calls_the_api_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, thread = start_fake_server()
            try:
                port = server.server_address[1]
                env = {
                    "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
                    "LLM_API_KEY": "test-key",
                    "LLM_MODEL": "test-model",
                }
                saved = run_cli(
                    *LLM_PROFILE_ARGS,
                    "--llm",
                    "--save",
                    "my-chat",
                    "--plans-dir",
                    tmp,
                    env=env,
                )
                requests_after_save = len(server.requests)

                refreshed = run_cli(
                    "--load",
                    "my-chat",
                    "--llm",
                    "--llm-refresh",
                    "--plans-dir",
                    tmp,
                    env=env,
                )
                requests_after_refresh = len(server.requests)
            finally:
                stop_fake_server(server, thread)

            self.assertEqual(
                saved.returncode,
                0,
                f"stdout:\n{saved.stdout}\nstderr:\n{saved.stderr}",
            )
            self.assertEqual(
                requests_after_save,
                1,
                "saving with --llm should call the API exactly once, got "
                f"{requests_after_save} requests",
            )
            self.assertEqual(
                refreshed.returncode,
                0,
                f"stdout:\n{refreshed.stdout}\nstderr:\n{refreshed.stderr}",
            )
            self.assertEqual(
                requests_after_refresh,
                requests_after_save + 1,
                "--llm-refresh should make exactly one new call, got "
                f"{requests_after_refresh} requests in total",
            )
            self.assertIn(
                "A local stack fits the budget.",
                refreshed.stdout,
                f"the fresh advice should be printed:\n{refreshed.stdout}",
            )
            self.assertNotIn(
                "no new tokens were spent",
                refreshed.stdout,
                "a fresh pass spends tokens, so the saved-advice note must not "
                f"be printed:\n{refreshed.stdout}",
            )

    def test_llm_refresh_without_load_exits_with_code_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(
                *PLAN_PROFILE_ARGS,
                "--llm-refresh",
                "--plans-dir",
                tmp,
            )
            output = result.stdout + result.stderr

            self.assertEqual(result.returncode, 2, f"output:\n{output}")
            self.assertIn(
                "--llm-refresh",
                result.stderr,
                f"the error should name the flag:\n{result.stderr}",
            )
            self.assertNotIn("Traceback", output, "the CLI must not crash")


if __name__ == "__main__":
    unittest.main()
