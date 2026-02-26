# Claude Slack Bridge

Control [Claude Code](https://claude.ai/code) from Slack — send prompts from your phone, review output in threads, and pick up where you left off whether you're at your desk or grabbing a coffee.

Each Slack thread is an isolated Claude session. Multiple threads run in parallel. When you're ready to switch back to your terminal, one command primes the session so you can resume without missing a beat.

---

## Features

- **Thread-based sessions** — each Slack thread is its own Claude Code session with independent context
- **Parallel sessions** — run multiple threads simultaneously without them interfering
- **Project picker** — on every new conversation, lists your projects directory so you can choose where to work
- **Create new projects** — spin up a new project directory directly from Slack
- **Seamless terminal handoff** — `!handoff` asks Claude to write a summary into the session history so `claude --resume` in your terminal picks up cleanly
- **Attach to existing sessions** — resume a session you already started in your terminal
- **Chunked output** — long responses are automatically split to fit Slack's message limit

---

## Commands

| Command | Description |
|---|---|
| `!help` | Show all available commands |
| `!project` | Show the active project for this thread |
| `!attach <session-id>` | Resume an existing Claude session by ID (printed in your terminal when you run `claude`) |
| `!handoff` | Ask Claude to summarize current state — run before switching back to your terminal |
| `reset` | Clear the current session and restart the project picker |

---

## One Session at a Time

> **Important:** avoid working on the same Claude session from both Slack and your terminal simultaneously.

Both clients write to the same conversation history. Concurrent writes can produce inconsistent state.

The recommended workflow is to treat it as a **handoff**:

1. Working in terminal → run `!attach <session-id>` in Slack → close or pause terminal
2. Working in Slack → run `!handoff` → then `claude --resume <session-id>` in terminal

`!handoff` appends a structured summary to the session history (what was done, what's pending, key context), so when you `claude --resume` you're immediately oriented.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token from **OAuth & Permissions** (starts with `xoxb-`) |
| `SLACK_APP_TOKEN` | Yes | App-Level Token from **Basic Information → App-Level Tokens** (starts with `xapp-`) |
| `CLAUDE_PROJECTS_DIR` | No | Root directory containing your projects. Defaults to `~/projects` |
| `CLAUDE_ALLOWED_TOOLS` | No | Comma-separated list of Claude tools to allow (e.g. `Read,Write,Bash`). Empty = all tools |

### 3. Run

```bash
python3 bridge.py
```

Keep the process running (e.g. in a `tmux` session or as a background service) while you work.

---

## Creating Your Own Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Select your workspace and paste the contents of [`slack-manifest.yaml`](./slack-manifest.yaml)
3. Click through to create the app

Once created:

**Get your Bot Token (`SLACK_BOT_TOKEN`)**
- Go to **OAuth & Permissions** → click **Install to Workspace**
- Copy the **Bot User OAuth Token** (`xoxb-...`)

**Get your App Token (`SLACK_APP_TOKEN`)**
- Go to **Basic Information** → scroll to **App-Level Tokens**
- Click **Generate Token and Scopes** → name it anything → add the `connections:write` scope → Generate
- Copy the token (`xapp-...`)

**Enable DMs**
- Go to **App Home** → scroll to **Show Tabs**
- Enable the **Messages Tab** and check **Allow users to send Slash commands and messages from the messages tab**

**Required OAuth scopes** (already included in the manifest):

| Scope | Purpose |
|---|---|
| `app_mentions:read` | Receive @mentions |
| `channels:history` | Read channel messages |
| `chat:write` | Send messages |
| `chat:write.customize` | Send with custom name/avatar |
| `im:history` | Read DMs |
| `im:read` | View DM info |
| `im:write` | Open DMs |

**Socket Mode** must be enabled (already set in the manifest). This means no public URL is needed — the bridge connects outbound from your machine.

---

## Using Webeet's App

If you're a Webeet employee, you can use the shared Slack app instead of creating your own.

Contact **support@webeet.io** to be invited to the Webeet Slack workspace and granted access to the bot. You'll receive the `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` values directly — no Slack app setup required.
