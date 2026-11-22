"""One lean, headless Claude call through the `claude` CLI (Claude Code), using its existing login:
no tools, no MCP servers, no settings or CLAUDE.md, no slash commands, run from a neutral folder."""
import json
import os
import shutil
import subprocess


def claude_bin():
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def available():
    return os.path.exists(claude_bin())


def call(system, prompt, model, timeout, cwd):
    cmd = [claude_bin(), "-p", "--model", model, "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--setting-sources", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands", "--system-prompt", system]
    os.makedirs(cwd, exist_ok=True)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    try:
        env = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("claude gave no JSON (exit %s): %s" % (r.returncode, (r.stderr or r.stdout or "")[-300:]))
    if env.get("is_error"):
        raise RuntimeError("claude error: %s" % str(env.get("result", ""))[:300])
    return env.get("result", "") or ""


def first_json(text, opener="{"):
    """Parse the first JSON object (or array) in a reply; Claude sometimes adds a sentence around it."""
    start = text.find(opener)
    if start < 0:
        raise ValueError("no JSON %s in reply" % opener)
    return json.JSONDecoder().raw_decode(text[start:])[0]
