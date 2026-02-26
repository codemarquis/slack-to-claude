#!/usr/bin/env python3
"""
Claude Code <-> Slack Bridge

Connects a Slack bot (via Socket Mode) to the local `claude` CLI,
letting you send prompts from your phone and see results in Slack.

Each Slack thread = one isolated Claude session.
Multiple threads can run in parallel.
"""

import os
import ssl
import subprocess
import threading
import logging

from dotenv import load_dotenv

load_dotenv()

# Fix SSL certificate verification on macOS with Python.org installer
try:
    import certifi
    _orig_create_default_context = ssl.create_default_context
    def _patched_create_default_context(*args, **kwargs):
        if not args and 'cafile' not in kwargs:
            kwargs['cafile'] = certifi.where()
        return _orig_create_default_context(*args, **kwargs)
    ssl.create_default_context = _patched_create_default_context
    ssl._create_default_https_context = _patched_create_default_context
except ImportError:
    pass

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Config ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
PROJECTS_DIR = os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR", "~/projects"))
ALLOWED_TOOLS = os.environ.get("CLAUDE_ALLOWED_TOOLS", "")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    raise SystemExit(
        "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables before running."
    )

app = App(token=SLACK_BOT_TOKEN)

# Per-thread state: thread_key -> {status, cwd, session_id, lock, pending_prompt}
# thread_key = ts of the root message (groups all replies in a thread)
thread_state: dict[str, dict] = {}

HELP_TEXT = """:wave: *Claude Slack Bridge — Commands*

*Session*
• `reset` — end current session and start a new one
• `!project` — show active project or restart project picker
• `!attach <session-id>` — resume an existing Claude session by ID
  _(session IDs are printed in your terminal when you run `claude`)_
• `!handoff` — ask Claude to summarize state so you can `claude --resume` in terminal and pick up seamlessly

*Info*
• `!help` — show this message

*Tips*
• Each Slack thread is a separate Claude session
• Start multiple threads to run parallel sessions
• The first message in a new thread triggers the project picker
• Use `!handoff` before switching back to terminal — it primes the session with a clean summary
"""

HANDOFF_PROMPT = (
    "I'm switching from Slack back to my terminal and will resume this session with "
    "`claude --resume`. Please write a concise handoff note covering: "
    "(1) what we were working on, "
    "(2) what was completed, "
    "(3) what's still pending or in progress, "
    "(4) any important decisions or context I should remember. "
    "Keep it short — this is just to orient me when I open the terminal."
)


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------

def list_projects() -> list[str]:
    """Return sorted list of directories in PROJECTS_DIR."""
    try:
        return sorted([
            d for d in os.listdir(PROJECTS_DIR)
            if os.path.isdir(os.path.join(PROJECTS_DIR, d)) and not d.startswith('.')
        ])
    except FileNotFoundError:
        return []


def project_picker_message() -> str:
    projects = list_projects()
    lines = ["*Which project do you want to work on?*\n"]
    for i, p in enumerate(projects, 1):
        lines.append(f"  {i}. `{p}`")
    lines.append(f"\nOr type a full path, or `new <name>` to create a new project.")
    return "\n".join(lines)


def parse_project_selection(text: str) -> str | None:
    """
    Parse a project selection. Returns:
      - full path string if valid selection
      - "NEW:<name>" if user typed "new <name>"
      - None if invalid
    """
    projects = list_projects()
    text = text.strip()

    # "new <name>" or "new" alone
    if text.lower().startswith("new"):
        parts = text.split(maxsplit=1)
        name = parts[1] if len(parts) > 1 else ""
        return f"NEW:{name}"

    # Numeric selection
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(projects):
            return os.path.join(PROJECTS_DIR, projects[idx])
        return None

    # Full / relative path that exists
    expanded = os.path.expanduser(text)
    if os.path.isdir(expanded):
        return expanded

    # Name match inside PROJECTS_DIR
    if text in projects:
        return os.path.join(PROJECTS_DIR, text)

    return None


# ---------------------------------------------------------------------------
# Claude runner
# ---------------------------------------------------------------------------

def run_claude(prompt: str, cwd: str, session_id: str | None) -> tuple[str, str | None]:
    """Run the claude CLI and return (output, updated_session_id)."""
    cmd = ["claude", "-p", prompt, "--output-format", "text"]

    if session_id:
        cmd += ["--resume", session_id]

    if ALLOWED_TOOLS:
        for tool in ALLOWED_TOOLS.split(","):
            cmd += ["--allowedTools", tool.strip()]

    logger.info(f"Running claude in {cwd} (session={session_id})")

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
            env=env,
        )
        output = result.stdout.strip()
        errors = result.stderr.strip()

        # Try to extract session id from stderr
        new_session_id = session_id
        for line in (errors or "").splitlines():
            if "session:" in line.lower() or "conversation" in line.lower():
                for part in line.split():
                    if len(part) > 10 and not part.startswith("-"):
                        new_session_id = part
                        logger.info(f"Captured session id: {part}")
                        break

        if not output and errors:
            return f"(stderr) {errors}", new_session_id
        return output or "(no output)", new_session_id

    except subprocess.TimeoutExpired:
        return "⏰ Command timed out after 5 minutes.", session_id
    except FileNotFoundError:
        return "❌ `claude` CLI not found. Is it installed and on your PATH?", session_id
    except Exception as e:
        return f"❌ Error: {e}", session_id


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def send_chunked(say, text: str, thread_ts: str):
    """Send text to Slack, splitting into chunks if needed."""
    MAX_LEN = 3900
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for chunk in chunks:
        say(text=f"```\n{chunk}\n```", thread_ts=thread_ts)


def run_and_reply(say, prompt: str, state: dict, thread_key: str):
    """Acquire per-thread lock, run claude, post result."""
    lock: threading.Lock = state["lock"]
    if not lock.acquire(blocking=False):
        say(text="🔒 A command is already running in this thread. Please wait.", thread_ts=thread_key)
        return

    say(text=f"⏳ Running on `{state['cwd']}`...", thread_ts=thread_key)
    try:
        output, new_session_id = run_claude(prompt, state["cwd"], state.get("session_id"))
        state["session_id"] = new_session_id
        send_chunked(say, output, thread_key)
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------

@app.event("message")
def handle_dm(event, say):
    """Handle direct messages to the bot."""
    if event.get("bot_id") or event.get("subtype"):
        return

    text = event.get("text", "").strip()
    if not text:
        return

    # thread_key = ts of root message (same for all replies in a thread)
    thread_key = event.get("thread_ts") or event["ts"]

    # ------------------------------------------------------------------
    # Global commands (work regardless of state)
    # ------------------------------------------------------------------
    if text.lower() == "!help":
        say(text=HELP_TEXT, thread_ts=thread_key)
        return

    if text.lower() == "reset":
        thread_state.pop(thread_key, None)
        say(text="🔄 Session cleared. Send a new message to start a fresh session.", thread_ts=thread_key)
        return

    if text.lower() == "!project":
        state = thread_state.get(thread_key)
        if state and state.get("cwd"):
            say(text=f"Current project: `{state['cwd']}`\nType `reset` to switch projects.", thread_ts=thread_key)
        else:
            say(text=project_picker_message(), thread_ts=thread_key)
        return

    if text.lower() == "!handoff":
        state = thread_state.get(thread_key)
        if not state or not state.get("cwd"):
            say(text="No active session to hand off.", thread_ts=thread_key)
            return
        threading.Thread(
            target=run_and_reply, args=(say, HANDOFF_PROMPT, state, thread_key), daemon=True
        ).start()
        return

    if text.lower().startswith("!attach"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            say(
                text="Usage: `!attach <session-id>`\nThe session ID is printed in your terminal when you run `claude`.",
                thread_ts=thread_key,
            )
            return
        session_id = parts[1].strip()
        state = thread_state.setdefault(thread_key, {
            "status": "active",
            "cwd": PROJECTS_DIR,
            "session_id": None,
            "lock": threading.Lock(),
        })
        state["session_id"] = session_id
        state["status"] = "active"
        say(text=f"✅ Attached to session `{session_id}`", thread_ts=thread_key)
        return

    # ------------------------------------------------------------------
    # New thread: show project picker, store first message as pending
    # ------------------------------------------------------------------
    if thread_key not in thread_state:
        thread_state[thread_key] = {
            "status": "picking_project",
            "cwd": None,
            "session_id": None,
            "lock": threading.Lock(),
            "pending_prompt": text,
        }
        say(text=project_picker_message(), thread_ts=event["ts"])
        return

    state = thread_state[thread_key]

    # ------------------------------------------------------------------
    # Project picking
    # ------------------------------------------------------------------
    if state["status"] == "picking_project":
        result = parse_project_selection(text)

        if result is None:
            say(text=f"Couldn't find that. {project_picker_message()}", thread_ts=thread_key)
            return

        if result.startswith("NEW:"):
            name = result[4:].strip()
            if not name:
                say(text="What should the new project be called? (type a name or full path)", thread_ts=thread_key)
                state["status"] = "creating_project"
                return
            path = os.path.join(PROJECTS_DIR, name) if not os.path.isabs(name) else os.path.expanduser(name)
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                say(text=f"❌ Couldn't create directory: {e}", thread_ts=thread_key)
                return
            state["cwd"] = path
            state["status"] = "active"
            say(text=f"✅ Created `{path}`", thread_ts=thread_key)
        else:
            state["cwd"] = result
            state["status"] = "active"
            say(text=f"✅ Working in `{result}`", thread_ts=thread_key)

        # Run the original prompt that triggered the picker
        pending = state.pop("pending_prompt", None)
        if pending:
            threading.Thread(
                target=run_and_reply, args=(say, pending, state, thread_key), daemon=True
            ).start()
        return

    # ------------------------------------------------------------------
    # Creating new project (name not given inline)
    # ------------------------------------------------------------------
    if state["status"] == "creating_project":
        name = text.strip()
        path = os.path.join(PROJECTS_DIR, name) if not os.path.isabs(os.path.expanduser(name)) else os.path.expanduser(name)
        try:
            os.makedirs(path, exist_ok=True)
            state["cwd"] = path
            state["status"] = "active"
            say(text=f"✅ Created and working in `{path}`", thread_ts=thread_key)
        except Exception as e:
            say(text=f"❌ Couldn't create directory: {e}", thread_ts=thread_key)
        return

    # ------------------------------------------------------------------
    # Active session: run claude in a background thread
    # ------------------------------------------------------------------
    threading.Thread(
        target=run_and_reply, args=(say, text, state, thread_key), daemon=True
    ).start()


if __name__ == "__main__":
    logger.info("Starting Claude Code <-> Slack bridge")
    logger.info(f"Projects directory: {PROJECTS_DIR}")
    logger.info("Listening for DMs...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
