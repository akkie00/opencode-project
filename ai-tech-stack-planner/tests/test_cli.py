"""End to end tests for the command line (ai-tech-stack-planner/src/main.py).

The CLI is run in a real subprocess so that the tests also cover the exit
codes and make sure the program never hangs.  A timeout guards every run.

The Phase 2 tests add the optional LLM reasoning pass: a tiny local HTTP
server pretends to be an OpenAI compatible API so that no real tokens are
spent and no real network call is made.
"""

import json
import os
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
