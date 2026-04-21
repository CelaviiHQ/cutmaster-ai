# Celavii-Resolve — Complete Setup Guide

This guide covers the **MCP server** — the stdio server that exposes 233 DaVinci Resolve tools to AI clients (Claude Code, Claude Desktop, Cursor, etc.).

> **Looking for CutMaster AI?** The UI panel for editors is a separate artifact with its own install path. See [CUTMASTER_SETUP.md](CUTMASTER_SETUP.md).

This guide covers everything from first clone to a fully working MCP server with skills, agents, hooks, and auto-start.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [MCP Server Configuration](#3-mcp-server-configuration)
   - [Claude Desktop](#31-claude-desktop)
   - [Claude Code (CLI)](#32-claude-code-cli)
   - [Cursor / VS Code / Other Editors](#33-cursor--vs-code--other-editors)
4. [Skills, Agents & Hooks (Claude Code)](#4-skills-agents--hooks-claude-code)
   - [How Skills Work](#41-how-skills-work)
   - [How Agents Work](#42-how-agents-work)
   - [How Hooks Work](#43-how-hooks-work)
   - [Installing for Global Access](#44-installing-for-global-access)
5. [Claude Code Plugin](#5-claude-code-plugin)
   - [Building the Plugin](#51-building-the-plugin)
   - [Installing the Plugin](#52-installing-the-plugin)
   - [Testing Locally](#53-testing-locally)
6. [Auto-Start with LaunchAgent (macOS)](#6-auto-start-with-launchagent-macos)
7. [Creating a Distributable Package](#7-creating-a-distributable-package)
8. [Verification & Testing](#8-verification--testing)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

| Requirement | Details |
|-------------|---------|
| **DaVinci Resolve Studio** | Version 18.5+ (the **paid** version, $295). The free edition does NOT support scripting. |
| **Python** | 3.11 or 3.12 (3.13+ has ABI issues with Resolve's fusionscript) |
| **External Scripting** | Must be enabled: Resolve > Preferences > System > General > External scripting using: **Local** |
| **Gemini API Key** (optional) | For AI tools only. Get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **uv** (**required**) | Fast Python package manager. Committed `.mcp.json` uses `uv run` to invoke the server — Claude Code / Desktop won't start the MCP without it. Install: `brew install uv` (macOS) / `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Linux) |

### Verify Resolve Studio

Open DaVinci Resolve > **DaVinci Resolve** menu > **About DaVinci Resolve**.
It must say **"DaVinci Resolve Studio"** (not just "DaVinci Resolve").

### Enable External Scripting

1. Open DaVinci Resolve Studio
2. Go to **DaVinci Resolve** > **Preferences** > **System** > **General**
3. Set **"External scripting using"** to **Local**
4. Click **Save** and restart Resolve

---

## 2. Installation

### Quick Install (recommended)

```bash
# Clone the repo
git clone https://github.com/CelaviiHQ/cutmaster-ai.git
cd cutmaster-ai

# Run the universal installer (interactive)
python3 scripts/install.py

# Or non-interactive for specific clients
python3 scripts/install.py --clients claude-desktop
python3 scripts/install.py --clients claude-desktop,claude-code,cursor
python3 scripts/install.py --clients all
```

### One-Command Setup (macOS)

```bash
git clone https://github.com/CelaviiHQ/cutmaster-ai.git
cd cutmaster-ai
bash scripts/setup.sh
```

This single script will:
- Create a Python virtual environment
- Install all dependencies
- Configure Claude Desktop
- Install skills/agents/hooks for Claude Code
- Set up the LaunchAgent for auto-start
- Verify the Resolve connection

### Manual Install

```bash
git clone https://github.com/CelaviiHQ/cutmaster-ai.git
cd cutmaster-ai

# Create venv and install
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Optional: install AI dependencies
pip install -e ".[ai]"

# Set up your Gemini API key (optional, for AI tools only)
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

---

## 3. MCP Server Configuration

The MCP server is what connects Celavii-Resolve's 233 tools to your AI client. Each client has its own configuration file.

### 3.1 Claude Desktop

Claude Desktop reads its MCP server config from a JSON file. This gives you all 233 tools directly in Claude Desktop conversations.

**Config file location:**

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

**Option A — Automatic (via installer):**

```bash
python3 scripts/install.py --clients claude-desktop
```

**Option B — Manual configuration:**

Open the config file (create it if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

Replace `/path/to/cutmaster-ai` with the actual path where you cloned the repo.

**Option C — Using uv (no venv needed):**

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/cutmaster-ai",
        "python", "-m", "cutmaster_ai"
      ]
    }
  }
}
```

**After configuring:**

1. Restart Claude Desktop completely (Cmd+Q on macOS, not just close the window)
2. Open a new conversation
3. You should see the MCP tools icon in the input bar
4. Test with: *"Use cutmaster_get_version to check the Resolve connection"*

### 3.2 Claude Code (CLI)

Claude Code uses a project-level `.mcp.json` file. This is **already included** in the repository.

```bash
# Navigate to the project directory
cd cutmaster-ai

# The .mcp.json is already configured — just start Claude Code
claude
```

The included `.mcp.json`:

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "${CLAUDE_PROJECT_ROOT}",
        "python", "-m", "cutmaster_ai"
      ]
    }
  }
}
```

**For global access** (use Celavii-Resolve from any directory):

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

### 3.3 Cursor / VS Code / Other Editors

**Cursor** (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

**VS Code Copilot** (`.vscode/mcp.json` in the project, or workspace settings):

```json
{
  "servers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

**Windsurf** (`~/.codeium/windsurf/mcp_config.json`):

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

**Zed** (`~/.config/zed/settings.json`):

```json
{
  "context_servers": {
    "cutmaster-ai": {
      "command": {
        "path": "/path/to/cutmaster-ai/.venv/bin/python3",
        "args": ["-m", "cutmaster_ai"],
        "env": {
          "PYTHONPATH": "/path/to/cutmaster-ai/src"
        }
      },
      "settings": {}
    }
  }
}
```

---

## 4. Skills, Agents & Hooks (Claude Code)

Skills, agents, and hooks are **Claude Code features** (not available in Claude Desktop). They extend Claude Code's behavior with domain-specific knowledge, slash commands, and safety guardrails.

### 4.1 How Skills Work

Skills are Claude Code slash commands defined in `skills/*/SKILL.md` files. When you're in the Celavii-Resolve project directory, Claude Code auto-discovers them.

**Available skills:**

| Command | What it does |
|---------|-------------|
| `/deliver` | Quick render with preset shorthands |
| `/preflight` | Pre-render verification checklist |
| `/color-assist` | AI color grading assistant |
| `/grade-log` | 6-node log footage grading workflow |
| `/conform` | Timeline conform from EDL/XML |
| `/ingest` | Media import with organisation |
| `/assembly` | Build rough cut from clips |
| `/review` | AI editorial feedback |
| `/export-stills` | Gallery still batch export |

**Usage in Claude Code:**

```
> /grade-log camera=sony-slog3
> /deliver h264 1080p
> /preflight
```

**Skills are project-level by default.** They work when Claude Code is running inside the `cutmaster-ai` directory. To use them from any directory, see [Installing for Global Access](#44-installing-for-global-access).

### 4.2 How Agents Work

Agents are domain-specific roles defined in `agents/*.md` files. Each agent has a YAML frontmatter header defining its name, tools, and behavior. Claude Code can adopt these roles for specialized tasks.

**Available agents:**

| Agent | Role |
|-------|------|
| `editor` | Timeline assembly, clip arrangement, track management |
| `colorist` | Color grading, node trees, LUTs, CDL, gallery stills |
| `vfx` | Fusion compositions, node graphs, visual effects |
| `sound` | Audio tracks, voice isolation, audio insertion |
| `conform` | EDL/XML/AAF import, relinking, round-trip workflows |
| `delivery` | Render configuration, format selection, quality control |
| `producer` | Project overview, status reporting, database management |

### 4.3 How Hooks Work

Hooks are safety guardrails defined in `hooks/hooks.json`. They run automatically before/after tool calls to prevent destructive operations and enforce best practices.

**Current hooks:**

| Hook | Type | What it does |
|------|------|-------------|
| Block .env editing | PreToolUse | Prevents accidental exposure of API keys |
| Destructive op warning | PreToolUse | Warns before delete project/clips/bins operations |
| Script execution caution | PreToolUse | Warns before running arbitrary Python/Lua scripts |
| Auto-lint Python | PostToolUse | Runs ruff format/check after editing .py files |

### 4.4 Installing for Global Access

To use Celavii-Resolve skills/agents/hooks from **any directory** in Claude Code (not just when inside the project folder), run the install script:

```bash
bash scripts/setup.sh --global
```

Or manually:

**Global Skills** — Copy to `~/.claude/commands/`:

```bash
# Create the global commands directory
mkdir -p ~/.claude/commands

# Copy each skill as a global slash command
for skill_dir in skills/*/; do
  skill_name=$(basename "$skill_dir")
  cp "$skill_dir/SKILL.md" "$HOME/.claude/commands/${skill_name}.md"
done
```

**Global Hooks** — Merge into `~/.claude/settings.json`:

```bash
# If you don't have settings.json yet:
mkdir -p ~/.claude
cp hooks/hooks.json ~/.claude/settings.json

# If you already have settings.json, manually merge the hooks arrays
```

**Global MCP Server** — Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "cutmaster-ai": {
      "command": "/path/to/cutmaster-ai/.venv/bin/python3",
      "args": ["-m", "cutmaster_ai"],
      "cwd": "/path/to/cutmaster-ai"
    }
  }
}
```

---

## 5. Claude Code Plugin

Celavii-Resolve can be packaged as a **Claude Code plugin** — a self-contained ZIP that bundles skills and MCP server config for easy distribution and installation.

### 5.1 Building the Plugin

```bash
# Full plugin with MCP server + skills
bash scripts/build-plugin.sh

# Skills-only (no MCP server — useful for sharing with team)
bash scripts/build-plugin.sh --skills-only

# Specify venv path and output directory
bash scripts/build-plugin.sh --venv /path/to/.venv --output ~/Desktop
```

This creates `cutmaster-ai-plugin-v0.1.0.zip` containing:

```
cutmaster-ai-plugin-v0.1.0.zip
├── .claude-plugin/
│   └── plugin.json        # Plugin manifest (name, version, author)
├── .mcp.json              # MCP server config (stdio)
└── skills/                # 9 Claude Code skills
    ├── assembly/SKILL.md
    ├── color-assist/SKILL.md
    ├── conform/SKILL.md
    ├── deliver/SKILL.md
    ├── export-stills/SKILL.md
    ├── grade-log/SKILL.md
    ├── ingest/SKILL.md
    ├── preflight/SKILL.md
    └── review/SKILL.md
```

### 5.2 Installing the Plugin

The recipient installs the plugin with a single command:

```bash
claude plugin install ./cutmaster-ai-plugin-v0.1.0.zip
```

Once installed, all 9 skills become available as namespaced slash commands:

```
/cutmaster-ai:deliver
/cutmaster-ai:grade-log
/cutmaster-ai:color-assist
...
```

### 5.3 Testing Locally

During development, test the plugin without building a ZIP:

```bash
claude --plugin-dir ./cutmaster-ai
```

This loads the plugin directly from the project directory, using the `.claude-plugin/plugin.json` manifest and the `skills/` directory.

---

## 6. Auto-Start with LaunchAgent (macOS)

The LaunchAgent starts the Celavii-Resolve MCP server automatically when you log in, so it's always available when DaVinci Resolve is open.

### Automatic Setup

```bash
bash scripts/setup.sh --launchagent
```

### Manual Setup

1. **Copy the plist template:**

```bash
cp scripts/launchd/ai.cutmaster.mcp.plist ~/Library/LaunchAgents/
```

2. **Edit the plist** to set your actual paths:

```bash
nano ~/Library/LaunchAgents/ai.cutmaster.mcp.plist
```

Replace the placeholder paths with your actual install location. The template uses `__VENV_PYTHON__` and `__PROJECT_DIR__` as placeholders.

3. **Load the LaunchAgent:**

```bash
launchctl load ~/Library/LaunchAgents/ai.cutmaster.mcp.plist
```

4. **Verify it's running:**

```bash
launchctl list | grep cutmaster
```

### Managing the LaunchAgent

```bash
# Start
launchctl load ~/Library/LaunchAgents/ai.cutmaster.mcp.plist

# Stop
launchctl unload ~/Library/LaunchAgents/ai.cutmaster.mcp.plist

# Check status
launchctl list | grep cutmaster

# View logs
cat /tmp/cutmaster-ai-mcp.out.log
cat /tmp/cutmaster-ai-mcp.err.log

# Remove completely
launchctl unload ~/Library/LaunchAgents/ai.cutmaster.mcp.plist
rm ~/Library/LaunchAgents/ai.cutmaster.mcp.plist
```

---

## 7. Creating a Distributable Package

To share Celavii-Resolve with others (e.g. team members, clients), create a distributable archive:

```bash
bash scripts/package.sh
```

This creates `cutmaster-ai-v0.1.0.zip` containing:
- All source code
- Skills, agents, hooks
- Install script
- Setup script
- LaunchAgent template
- Documentation

The recipient can then:

```bash
unzip cutmaster-ai-v0.1.0.zip
cd cutmaster-ai
bash scripts/setup.sh
```

### What's included in the package

```
cutmaster-ai-v0.1.0.zip
├── src/                    # All MCP tools (233 tools)
├── skills/                 # 9 Claude Code skills
├── agents/                 # 7 domain agents
├── hooks/                  # Safety hooks
├── scripts/
│   ├── install.py         # Universal installer
│   ├── build-plugin.sh    # Build Claude Code plugin ZIP
│   ├── setup.sh           # One-command setup
│   ├── package.sh         # Creates this zip
│   └── launchd/
│       └── ai.cutmaster.mcp.plist
├── docs/
│   └── SETUP.md           # This guide
├── pyproject.toml         # Package config
├── .mcp.json              # Claude Code config
├── .env.example           # Environment template
└── README.md              # Overview
```

### What's NOT included (and shouldn't be)

- `.env` (contains your API keys)
- `.venv/` (virtual environment — recreated on install)
- `__pycache__/` (Python cache)
- `.git/` (git history)

---

## 8. Verification & Testing

After setup, verify everything works:

### Check Resolve Connection

```bash
cd cutmaster-ai
source .venv/bin/activate
python -c "
import sys; sys.path.insert(0, 'src')
from cutmaster_ai.resolve import get_resolve
r = get_resolve()
print(f'Connected: {r.GetProductName()} {r.GetVersionString()}')
"
```

Expected output: `Connected: DaVinci Resolve Studio 20.x.x.x`

### Check MCP Server Starts

```bash
source .venv/bin/activate
python -m cutmaster_ai
# Should start without errors. Ctrl+C to stop.
```

### Run the Test Suite

```bash
source .venv/bin/activate
pytest tests/ -v
# Should show 197 passed
```

### Test in Claude Desktop

Open Claude Desktop, start a new conversation, and try:

> "Use the cutmaster_get_version tool to check the Resolve connection"

You should see the tool being called and returning the Resolve version.

### Test in Claude Code

```bash
cd cutmaster-ai
claude
```

Then type:

```
> /grade-log
> Use cutmaster_get_version to check the connection
```

---

## 9. Troubleshooting

### "MCP tools not showing up in Claude Desktop"

1. Verify the config file exists and is valid JSON:
   ```bash
   cat ~/Library/Application\ Support/Claude/claude_desktop_config.json | python3 -m json.tool
   ```
2. Check the Python path is correct — it must be the **absolute** path to the venv Python:
   ```bash
   ls -la /path/to/cutmaster-ai/.venv/bin/python3
   ```
3. Restart Claude Desktop completely (Cmd+Q, not just close window)
4. Check Claude Desktop's developer console for errors

### "scriptapp returned None"

- You're running the free edition of DaVinci Resolve. You need **DaVinci Resolve Studio**.
- External scripting is not enabled. Go to Preferences > System > General > External scripting: **Local**
- Resolve is not running. The MCP server needs Resolve open to connect.

### "Module not found" errors

```bash
cd cutmaster-ai
source .venv/bin/activate
pip install -e ".[dev]"
```

### "Permission denied" for LaunchAgent

```bash
chmod 644 ~/Library/LaunchAgents/ai.cutmaster.mcp.plist
```

### Skills not appearing in Claude Code

- Make sure you're running Claude Code **from inside the project directory**:
  ```bash
  cd cutmaster-ai
  claude
  ```
- Or install globally: `bash scripts/setup.sh --global`

### AI tools not working

- Set your Gemini API key:
  ```bash
  cp .env.example .env
  # Edit .env and add: GEMINI_API_KEY=your-key-here
  ```
- Install AI dependencies:
  ```bash
  pip install -e ".[ai]"
  ```
