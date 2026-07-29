"""
Data-Analyst Telegram Bot.

Long-polls Telegram for incoming messages (no inbound public URL needed for
the bot itself -- Telegram polling reaches OUT to api.telegram.org). For each
question, runs an agent loop: the model can call a python_exec tool to fetch
public datasets and compute exact figures, then must reply with EXACTLY the
JSON shape the question specifies (each question states its own shape
inline, so we don't hardcode one).

Every step is logged as one JSON object per line to run.jsonl, which gets
git-pushed after every reply so a fixed raw.githubusercontent.com URL always
serves the latest log -- no separate log-hosting server needed.

Env vars required:
  TELEGRAM_BOT_TOKEN     - from @BotFather
  RUN_LOG_PUBLIC_URL     - the URL you'll report as log_url, e.g.
                           https://raw.githubusercontent.com/you/repo/main/run.jsonl
  GROQ_API_KEY           - optional; falls back to a deterministic stub if unset
  GIT_PUSH_ENABLED       - "1" (default) or "0" to disable auto-push (e.g. for local testing)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
LOG_PATH = os.environ.get("RUN_LOG_PATH", "run.jsonl")
GIT_PUSH_ENABLED = os.environ.get("GIT_PUSH_ENABLED", "1") == "1"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

MAX_TOOL_ITERS = 8
CODE_TIMEOUT_SECONDS = 25

SYSTEM_PROMPT = """You are a data-analyst agent replying inside a Telegram bot.
You will receive a data-analysis question. It always specifies EXACTLY the
JSON shape your final reply must have, usually via a literal example object
in the question text. Follow that shape precisely -- exact key names, exact
nesting.

You have a python_exec tool: use it to fetch public datasets (via the
`requests` library), load/clean data (via pandas), and compute exact figures.
Do not guess a number you could instead compute. Iterate with the tool as
many times as needed before answering.

When you are done, your FINAL message (with no further tool calls) must be
ONLY the exact JSON object the question requested -- nothing else, no
markdown code fences, no explanation before or after it. Fill any "log_url"
field with exactly this URL: {log_url}
"""

# OpenAI-style (Groq-compatible) tool/function schema
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Execute a Python snippet in a fresh subprocess. Has requests, "
                "pandas as pd, numpy as np, json, re, io, math, datetime "
                "available. Use print() for anything you need to see -- only "
                "stdout/stderr is returned to you. Timeout: 25s per call."
            ),
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python code to execute"}},
                "required": ["code"],
            },
        },
    }
]


# ------------------------------------------------------------ code sandbox --

def run_python(code: str) -> str:
    """Execute code in a fresh subprocess, return combined stdout+stderr (truncated)."""
    preamble = (
        "import requests\n"
        "import pandas as pd\n"
        "import numpy as np\n"
        "import json, re, io, math, datetime\n"
    )
    full_code = preamble + "\n" + code
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(full_code)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=CODE_TIMEOUT_SECONDS,
        )
        combined = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    except subprocess.TimeoutExpired:
        combined = f"[error] execution timed out after {CODE_TIMEOUT_SECONDS}s"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return combined[:8000]


# -------------------------------------------------------------------- log --

def log_event(fh, obj):
    obj = dict(obj)
    obj.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fh.flush()


def git_push_log():
    if not GIT_PUSH_ENABLED:
        return
    try:
        subprocess.run(["git", "add", LOG_PATH], check=True)
        subprocess.run(["git", "commit", "-m", "update run log"], check=False)
        subprocess.run(["git", "push"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[warn] git push failed: {e}")


# ------------------------------------------------------------------- LLM --

def call_llm_real(question_text, log_url, log_fh):
    """Real Groq-backed agent loop with tool use. Requires GROQ_API_KEY.

    Groq's /chat/completions endpoint is OpenAI-compatible: system prompt is
    a normal message with role "system", tool calls come back on
    message["tool_calls"], and tool results are sent back as role "tool"
    messages keyed by tool_call_id.
    """
    system = SYSTEM_PROMPT.format(log_url=log_url)
    convo = [
        {"role": "system", "content": system},
        {"role": "user", "content": question_text},
    ]
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    for _ in range(MAX_TOOL_ITERS):
        resp = requests.post(
            GROQ_API_URL,
            headers=headers,
            json={
                "model": GROQ_MODEL,
                "messages": convo,
                "tools": TOOLS,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            final_text = (message.get("content") or "").strip()
            log_event(log_fh, {"step": "final_response", "text": final_text})
            return final_text

        # Append the assistant turn (including the raw tool_calls) so the
        # model sees its own request on the next round.
        convo.append({
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            code = args.get("code", "")
            log_event(log_fh, {"step": "tool_call", "tool": fn.get("name"), "code": code})
            output = run_python(code)
            log_event(log_fh, {"step": "tool_result", "tool": fn.get("name"), "output": output})
            convo.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": output,
            })

    log_event(log_fh, {"step": "max_iterations_reached"})
    return None


def call_llm_STUB(question_text, log_url, log_fh):
    """
    Deterministic stand-in for local testing without an API key. Actually
    executes a trivial python_exec round-trip so the whole plumbing --
    sandbox, logging, JSON extraction -- is provably exercised end to end.
    """
    log_event(log_fh, {"step": "stub_llm_invoked", "input": question_text})
    code = "print(2 + 2)"
    log_event(log_fh, {"step": "tool_call", "tool": "python_exec", "code": code})
    output = run_python(code)
    log_event(log_fh, {"step": "tool_result", "tool": "python_exec", "output": output})
    fake_answer = {"note": "stub response -- replace with real model", "computed_check": output.strip()}
    final = json.dumps({"answer": fake_answer, "log_url": log_url})
    log_event(log_fh, {"step": "final_response", "text": final})
    return final


def solve(question_text, log_url, log_fh):
    if GROQ_API_KEY:
        return call_llm_real(question_text, log_url, log_fh)
    return call_llm_STUB(question_text, log_url, log_fh)


# --------------------------------------------------------------- json out --

def extract_json_object(text):
    """Extract the first balanced {...} JSON object from text, tolerating stray wrapper text."""
    if not text:
        return None
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


# ------------------------------------------------------------- telegram --

def telegram_get_updates(offset):
    resp = requests.get(f"{TELEGRAM_API}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
    resp.raise_for_status()
    return resp.json()["result"]


def telegram_send_message(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})


# ------------------------------------------------------------------ main --

def process_one_message(text, log_url, log_fh):
    """Shared logic between the real bot loop and local testing."""
    log_event(log_fh, {"step": "message_received", "text": text})
    try:
        raw_reply = solve(text, log_url, log_fh)
        parsed = extract_json_object(raw_reply) if raw_reply else None
        if parsed is None:
            log_event(log_fh, {"step": "error", "reason": "could_not_extract_json", "raw": raw_reply})
            reply_text = raw_reply or json.dumps({"answer": None, "log_url": log_url})
        else:
            reply_text = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc()
        log_event(log_fh, {"step": "exception", "error": str(e), "traceback": tb})
        reply_text = json.dumps({"answer": None, "log_url": log_url, "error": str(e)})
    log_event(log_fh, {"step": "message_sent", "text": reply_text})
    return reply_text


def main():
    log_url = os.environ["RUN_LOG_PUBLIC_URL"]
    offset = None
    print("Bot polling started...")
    with open(LOG_PATH, "a", encoding="utf-8") as log_fh:
        while True:
            try:
                updates = telegram_get_updates(offset)
            except requests.RequestException as e:
                print(f"[warn] getUpdates failed: {e}")
                time.sleep(3)
                continue

            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg or "text" not in msg:
                    continue
                if msg.get("from", {}).get("is_bot", False):
                    continue

                chat_id = msg["chat"]["id"]
                reply_text = process_one_message(msg["text"], log_url, log_fh)
                telegram_send_message(chat_id, reply_text)
                git_push_log()


if __name__ == "__main__":
    main()