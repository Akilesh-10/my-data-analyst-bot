"""
Local test harness -- exercises run_python(), extract_json_object(), and the
logging pipeline without needing a Telegram token or a Groq API key.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import bot  # noqa: E402


def test_sandbox_basic_arithmetic():
    out = bot.run_python("print(2 + 2)")
    assert out.strip() == "4", f"expected '4', got {out!r}"
    print("PASS: sandbox basic arithmetic")


def test_sandbox_pandas_available():
    out = bot.run_python("import pandas as pd\nprint(pd.__version__)")
    assert out.strip() and "Error" not in out and "Traceback" not in out, f"pandas import failed: {out!r}"
    print(f"PASS: pandas available (version {out.strip()})")


def test_sandbox_requests_available():
    out = bot.run_python("import requests\nprint('requests ok')")
    assert out.strip() == "requests ok", f"requests import failed: {out!r}"
    print("PASS: requests available")


def test_sandbox_timeout():
    out = bot.run_python("import time\ntime.sleep(60)")
    assert "timed out" in out, f"expected timeout message, got {out!r}"
    print("PASS: sandbox enforces timeout")


def test_sandbox_captures_error():
    out = bot.run_python("1/0")
    assert "ZeroDivisionError" in out, f"expected ZeroDivisionError in output, got {out!r}"
    print("PASS: sandbox captures exceptions")


def test_json_extraction_clean():
    text = '{"answer": {"state": "Assam"}, "log_url": "https://x/run.jsonl"}'
    parsed = bot.extract_json_object(text)
    assert parsed == {"answer": {"state": "Assam"}, "log_url": "https://x/run.jsonl"}
    print("PASS: clean JSON extraction")


def test_json_extraction_with_stray_prose():
    text = 'Sure! Here is the answer:\n```json\n{"answer": {"value": 42}, "log_url": "u"}\n```\nHope that helps!'
    parsed = bot.extract_json_object(text)
    assert parsed == {"answer": {"value": 42}, "log_url": "u"}
    print("PASS: JSON extraction survives markdown fences + surrounding prose")


def test_json_extraction_nested_braces():
    text = 'noise {"answer": {"a": {"b": 1}, "c": [1, 2, 3]}, "log_url": "u"} trailing noise'
    parsed = bot.extract_json_object(text)
    assert parsed == {"answer": {"a": {"b": 1}, "c": [1, 2, 3]}, "log_url": "u"}
    print("PASS: JSON extraction handles nested braces correctly")


def test_json_extraction_no_json():
    parsed = bot.extract_json_object("no json here at all")
    assert parsed is None
    print("PASS: JSON extraction returns None when absent")


def test_end_to_end_stub_pipeline(tmp_log_path):
    bot.GIT_PUSH_ENABLED = False  # don't try to push during a local test

    # Force the stub LLM path regardless of what's exported in the shell.
    # Without this, a real GROQ_API_KEY sitting in the environment (e.g. from
    # `export $(grep -v '^#' .env | xargs)`) would silently switch solve()
    # over to call_llm_real(), and this test's expected step sequence -- which
    # is specific to the deterministic stub -- would no longer match.
    original_key = bot.GROQ_API_KEY
    bot.GROQ_API_KEY = None
    try:
        with open(tmp_log_path, "w", encoding="utf-8") as fh:
            reply = bot.process_one_message(
                "What is 2+2? Reply with ONLY this JSON: {\"answer\": <int>, \"log_url\": \"<url>\"}",
                "https://example.com/run.jsonl",
                fh,
            )
    finally:
        bot.GROQ_API_KEY = original_key

    parsed = json.loads(reply)
    assert "answer" in parsed and "log_url" in parsed
    assert parsed["log_url"] == "https://example.com/run.jsonl"

    with open(tmp_log_path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    steps = [l["step"] for l in lines]
    assert steps == [
        "message_received",
        "stub_llm_invoked",
        "tool_call",
        "tool_result",
        "final_response",
        "message_sent",
    ], f"unexpected step sequence: {steps}"
    for line in lines:
        assert "timestamp" in line
    print("PASS: end-to-end stub pipeline produces valid JSON reply + well-formed JSONL log")


if __name__ == "__main__":
    test_sandbox_basic_arithmetic()
    test_sandbox_pandas_available()
    test_sandbox_requests_available()
    test_sandbox_timeout()
    test_sandbox_captures_error()
    test_json_extraction_clean()
    test_json_extraction_with_stray_prose()
    test_json_extraction_nested_braces()
    test_json_extraction_no_json()
    test_end_to_end_stub_pipeline("/tmp/test_run.jsonl")
    print("\nAll tests passed.")