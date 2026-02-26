# Session Notes — 2026-02-25 (updated)

## What this project is
`bridge.py` — a Slack bot (Socket Mode) that forwards DMs to the local `claude` CLI and replies with the output. Lets you run Claude Code from your phone via Slack.

## Current status: BROKEN — can't run

### Problem 1: SSL (FIXED)
Python 3.12 (Python.org installer at `/Library/Frameworks/Python.framework/Versions/3.12/`) couldn't verify SSL certs.
Fixed by adding this to the top of `bridge.py`:
```python
import ssl
try:
    import certifi
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass
```

### Problem 2: Invalid Slack token (NOT FIXED)
`bridge.py` fails with:
```
slack_sdk.errors.SlackApiError: {'ok': False, 'error': 'account_inactive'}
```
The `SLACK_BOT_TOKEN` in `.env` is stale — the app was accidentally removed/deactivated from the Slack workspace.

## What needs to happen to fix it

The Slack app needs to be reinstalled to the workspace to get a fresh `xoxb-...` token.

### What we tried
- Looked for "Reinstall App" / "Install to Workspace" button on api.slack.com/apps → OAuth & Permissions
- Button is not visible despite Bot Token Scopes being configured
- Possible causes not yet ruled out:
  - Workspace admin has restricted app installs (non-admins see "Request to Install" instead)
  - App may have been fully deleted (not just removed from workspace)
  - Manage Distribution set to public — may need to use the shareable install link instead

### Next steps to try
1. Check api.slack.com/apps — does the app still appear?
2. Check workspace admin panel → Manage Apps — is the app listed as restricted/banned?
3. Under "Manage Distribution" in the app settings — is there a shareable install URL?
4. If the app is truly gone, create a new Slack app (can reuse the same scopes/config)
5. Once reinstalled, copy new `xoxb-...` token into `.env` as `SLACK_BOT_TOKEN`
6. Also check `SLACK_APP_TOKEN` (`xapp-...`) under Basic Information → App-Level Tokens — may need regenerating too

## Current status: WORKING but with limitations

The bridge is running and responding to Slack DMs. Two fixes were needed beyond SSL:
- `CLAUDECODE` env var must be stripped from subprocess env (done)
- `CLAUDE_CODE_ENTRYPOINT` env var must also be stripped (done)

### Known issue: working directory is locked to CLAUDE_WORKING_DIR
Claude responds to Slack messages but refuses to access directories outside of `CLAUDE_WORKING_DIR` (currently `/Users/davidrajcher/projects/claude-code`). When asked to work on `~/projects/mind-guard/platform`, it says it can't access that path and suggests starting a new session.

**Root cause:** `claude -p` (non-interactive/print mode) is scoped to the `cwd` passed to `subprocess.run`. It won't traverse outside that directory.

**Possible fixes to explore:**
1. Change `CLAUDE_WORKING_DIR` in `.env` to the project you want to work on before running the bridge
2. Support a command like `!cd ~/projects/mind-guard/platform` in Slack to switch the working dir at runtime
3. Allow the bot to detect a path in the message and set `cwd` accordingly

## Files
- `bridge.py` — main script
- `.env` — contains `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` (both currently stale)
- `requirements.txt` — deps (slack-bolt, python-dotenv, certifi)
