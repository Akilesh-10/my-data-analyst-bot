# Data-Analyst Telegram Bot — deployment guide

## What's already built and tested (in this sandbox, without real credentials)

- `bot.py` — the full bot: Telegram long-polling, an agent loop with a
  `python_exec` tool (sandboxed subprocess, pandas/numpy/requests available),
  JSON-shape extraction that survives markdown fences and stray prose, JSONL
  logging of every step, and auto git-push of the log after each reply.
- `test_bot.py` — 10 tests, all passing: sandbox arithmetic, pandas/requests
  availability inside the sandbox, timeout enforcement, exception capture,
  JSON extraction (clean / fenced / nested-braces / absent), and a full
  end-to-end stub run producing a valid reply + well-formed log.

What I could **not** test here: the actual Telegram connection and the real
Anthropic-backed reasoning, since this sandbox has no bot token, no API key,
and no outbound network access to Telegram's API or arbitrary data sources
like MOSPI. That's on you to verify once deployed (steps below).

## 1. Create your Telegram bot

1. Open Telegram, message **@BotFather**
2. `/newbot` → give it a name → give it a **username ending in `bot`**
   (required by the assignment)
3. BotFather gives you a token like `123456789:AAF...` — save it

## 2. Get an Anthropic API key (or use your own free/local model)

Go to console.anthropic.com → API Keys → create one. If you'd rather use a
free/local model instead, edit `call_llm_real` in `bot.py` to call whatever
you're using — the surrounding plumbing (logging, JSON extraction, sandbox
tool) stays the same regardless of which model provider you pick.

## 3. Create your public GitHub repo

This repo serves two purposes: it's your submitted "GitHub repo URL" for
grading, AND it hosts your log file for free via
`raw.githubusercontent.com` — no separate log server needed.

```bash
mkdir my-data-analyst-bot && cd my-data-analyst-bot
git init
git branch -M main
# copy bot.py, requirements.txt, .env.example (NOT your real .env) in here
touch run.jsonl
echo ".env" >> .gitignore
git add .
git commit -m "initial bot"
```

Create the repo on github.com (public!), then:

```bash
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Your log URL will be:
```
https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main/run.jsonl
```

### Set up non-interactive git push (the bot needs to push after every reply)

Easiest: a GitHub Personal Access Token embedded in the remote URL.

1. GitHub → Settings → Developer settings → Personal access tokens → generate
   one with `repo` scope
2. Set your remote to use it:
   ```bash
   git remote set-url origin https://YOUR_USER:YOUR_TOKEN@github.com/YOUR_USER/YOUR_REPO.git
   ```
3. Configure a commit identity if you haven't already:
   ```bash
   git config user.email "you@example.com"
   git config user.name "Your Name"
   ```

## 4. Configure environment variables

```bash
cp .env.example .env
# edit .env with your real token, API key, and log URL
```

Load them before running (or use a process manager that reads `.env`):

```bash
export $(grep -v '^#' .env | xargs)
```

## 5. Run it

```bash
pip install -r requirements.txt --break-system-packages
python3 bot.py
```

You should see `Bot polling started...`. Message your bot from a **real
Telegram user account** (not another bot — Telegram blocks bot-to-bot
messages, which is why the assignment specifically notes the grader uses a
real account) and confirm you get back a single JSON object.

## 6. Keep it running through grading

Long-polling means no inbound tunnel is needed, but the process itself must
stay alive continuously. Options, easiest first:

- **tmux/screen** on your own machine:
  ```bash
  tmux new -s telegrambot
  export $(grep -v '^#' .env | xargs)
  python3 bot.py
  # Ctrl+B then D to detach
  ```
- **A free always-on host** (more reliable than a laptop that might sleep):
  Render, Railway, Fly.io, or PythonAnywhere all support running a long-lived
  Python worker process for free at this scale. Push this repo there and set
  the same env vars in their dashboard.

## 7. Test against the official grading harness before submitting

The assignment links a public test harness:
```
github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
```
Clone it, point it at your bot's username, add a few of your own questions to
`evals/questions.json`, and run it locally to see how your bot actually
performs on realistic MOSPI-style questions before the real grading run.

## 8. Submit

In the assignment box:
```
https://github.com/YOUR_USER/YOUR_REPO, your_bot_username_bot
```

## Known limitations / things to sanity-check yourself

- **`call_llm_STUB` is a placeholder.** It runs a trivial `2+2` sandbox
  round-trip just to prove the plumbing works end-to-end without an API key.
  Once you set `ANTHROPIC_API_KEY`, `call_llm_real` takes over — but you
  should test it against a few real MOSPI-style questions yourself, since I
  couldn't reach the real Anthropic API or a real dataset from this sandbox.
- **Multi-turn questions**: the current loop treats each incoming message
  independently (no persisted chat history across messages). The assignment
  says "answer the last one" for multi-turn sequences — if a later message
  depends on earlier context in the same chat, you may want to extend
  `process_one_message` to include recent chat history from `msg["chat"]["id"]`
  in the prompt. Flagging this as a gap rather than guessing at behavior you
  haven't specified.
- **Sandbox network access**: `python_exec` can call `requests.get(...)`
  freely on your real deployment (unlike this restricted dev sandbox), so
  MOSPI-style public dataset fetches should work once actually deployed.
