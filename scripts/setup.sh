#!/usr/bin/env bash
# ============================================================================
# Celavii-Resolve — One-Command Setup Script (macOS / Linux)
#
# Usage:
#   bash scripts/setup.sh              # Full interactive setup
#   bash scripts/setup.sh --global     # Also install skills/hooks globally
#   bash scripts/setup.sh --launchagent # Also install macOS LaunchAgent
#   bash scripts/setup.sh --all        # Everything: venv + claude desktop +
#                                      #   global skills + launchagent
#   bash scripts/setup.sh --uninstall  # Remove LaunchAgent and global config
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SRC_DIR="$PROJECT_DIR/src"
SERVER_MODULE="celavii_resolve"
PLIST_TEMPLATE="$PROJECT_DIR/scripts/launchd/com.celavii.resolve-mcp.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.celavii.resolve-mcp.plist"

# Claude paths
CLAUDE_DESKTOP_CONFIG_MAC="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CLAUDE_DESKTOP_CONFIG_LINUX="$HOME/.config/Claude/claude_desktop_config.json"
CLAUDE_CODE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_CODE_COMMANDS="$HOME/.claude/commands"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*"; }
header()  { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}\n"; }

check_command() {
    command -v "$1" &>/dev/null
}

get_claude_desktop_config() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "$CLAUDE_DESKTOP_CONFIG_MAC"
    else
        echo "$CLAUDE_DESKTOP_CONFIG_LINUX"
    fi
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

DO_GLOBAL=false
DO_LAUNCHAGENT=false
DO_UNINSTALL=false

for arg in "$@"; do
    case "$arg" in
        --global)       DO_GLOBAL=true ;;
        --launchagent)  DO_LAUNCHAGENT=true ;;
        --all)          DO_GLOBAL=true; DO_LAUNCHAGENT=true ;;
        --uninstall)    DO_UNINSTALL=true ;;
        --help|-h)
            echo "Usage: bash scripts/setup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --global        Install skills/hooks globally for Claude Code"
            echo "  --launchagent   Install macOS LaunchAgent for auto-start"
            echo "  --all           All of the above"
            echo "  --uninstall     Remove LaunchAgent and global config"
            echo "  --help, -h      Show this help"
            exit 0
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if $DO_UNINSTALL; then
    header "Uninstalling Celavii-Resolve"

    # LaunchAgent
    if [[ -f "$PLIST_DEST" ]]; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
        rm -f "$PLIST_DEST"
        success "LaunchAgent removed"
    else
        info "No LaunchAgent found"
    fi

    # Global skills
    if [[ -d "$CLAUDE_CODE_COMMANDS" ]]; then
        for skill_dir in "$PROJECT_DIR"/skills/*/; do
            skill_name=$(basename "$skill_dir")
            cmd_file="$CLAUDE_CODE_COMMANDS/${skill_name}.md"
            if [[ -f "$cmd_file" ]]; then
                rm -f "$cmd_file"
                info "Removed global skill: $skill_name"
            fi
        done
        success "Global skills removed"
    fi

    success "Uninstall complete"
    exit 0
fi

# ---------------------------------------------------------------------------
# Main setup
# ---------------------------------------------------------------------------

echo ""
echo -e "${BOLD}${CYAN}"
echo "   ____     _            _ _       ____                 _           "
echo "  / ___|___| | __ ___   _(_|_)    |  _ \ ___  ___  ___ | |_   _____ "
echo " | |   / _ \ |/ _\` \\ \\ / / | |____| |_) / _ \\/ __|/ _ \\| \\ \\ / / _ \\"
echo " | |__|  __/ | (_| |\\ V /| | |____|  _ <  __/\\__ \\ (_) | |\\ V /  __/"
echo "  \\____\\___|_|\\__,_| \\_/ |_|_|    |_| \\_\\___||___/\\___/|_| \\_/ \\___|"
echo ""
echo -e "${NC}${BOLD}  MCP Server Setup${NC}"
echo ""

# ---- Step 1: Find Python ----
header "Step 1: Python Environment"

PYTHON=""
for candidate in python3.12 python3.11 python3 python; do
    if check_command "$candidate"; then
        version=$($candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" == "3" && "$minor" -ge 11 && "$minor" -le 12 ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "Python 3.11 or 3.12 required but not found."
    error "Install from https://python.org or via: brew install python@3.12"
    exit 1
fi

success "Python: $($PYTHON --version) at $(which $PYTHON)"

# ---- Step 2: Virtual Environment ----
header "Step 2: Virtual Environment"

if [[ -f "$VENV_DIR/bin/python3" ]]; then
    success "Virtual environment already exists at $VENV_DIR"
else
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    success "Virtual environment created"
fi

VENV_PYTHON="$VENV_DIR/bin/python3"

info "Installing dependencies..."
"$VENV_PYTHON" -m pip install -q -e "$PROJECT_DIR[dev]" 2>/dev/null
success "Core dependencies installed"

# Install AI dependencies if not present
if ! "$VENV_PYTHON" -c "import google.genai" 2>/dev/null; then
    info "Installing AI dependencies (Gemini)..."
    "$VENV_PYTHON" -m pip install -q -e "$PROJECT_DIR[ai]" 2>/dev/null
    success "AI dependencies installed"
fi

# ---- Step 3: Resolve Connection ----
header "Step 3: DaVinci Resolve Connection"

RESOLVE_API=""
for api_path in \
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules" \
    "/opt/resolve/Developer/Scripting/Modules"; do
    if [[ -d "$api_path" ]]; then
        RESOLVE_API="$api_path"
        break
    fi
done

if [[ -n "$RESOLVE_API" ]]; then
    success "Resolve API found: $RESOLVE_API"

    # Try to connect
    RESOLVE_RESULT=$("$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$RESOLVE_API')
try:
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp('Resolve')
    if r:
        print(f'CONNECTED:{r.GetProductName()} {r.GetVersionString()}')
    else:
        print('NOT_RUNNING')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null || echo "ERROR:script failed")

    if [[ "$RESOLVE_RESULT" == CONNECTED:* ]]; then
        success "Connected to ${RESOLVE_RESULT#CONNECTED:}"
    elif [[ "$RESOLVE_RESULT" == "NOT_RUNNING" ]]; then
        warn "Resolve API available but Resolve is not running"
        info "Start DaVinci Resolve Studio and the MCP server will connect automatically"
    else
        warn "Resolve API found but could not connect: ${RESOLVE_RESULT#ERROR:}"
    fi
else
    warn "Resolve API not found — install DaVinci Resolve Studio"
fi

# ---- Step 4: Claude Desktop Configuration ----
header "Step 4: Claude Desktop MCP Configuration"

CLAUDE_CONFIG=$(get_claude_desktop_config)

if [[ -d "$(dirname "$CLAUDE_CONFIG")" ]] || [[ "$(uname)" == "Darwin" ]]; then
    info "Config: $CLAUDE_CONFIG"

    # Build the server entry
    SERVER_JSON=$(cat <<EOJSON
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "$VENV_PYTHON",
      "args": ["-m", "$SERVER_MODULE"],
      "cwd": "$PROJECT_DIR"
    }
  }
}
EOJSON
)

    if [[ -f "$CLAUDE_CONFIG" ]]; then
        # Merge with existing config using Python
        "$VENV_PYTHON" -c "
import json, shutil
from pathlib import Path

config_path = Path('$CLAUDE_CONFIG')
backup_path = config_path.with_suffix('.json.backup')
shutil.copy2(config_path, backup_path)

existing = json.loads(config_path.read_text())
if 'mcpServers' not in existing:
    existing['mcpServers'] = {}

existing['mcpServers']['celavii-resolve'] = {
    'command': '$VENV_PYTHON',
    'args': ['-m', '$SERVER_MODULE'],
    'cwd': '$PROJECT_DIR',
}

config_path.write_text(json.dumps(existing, indent=2) + '\n')
print('MERGED')
" 2>/dev/null && success "Claude Desktop config updated (backup saved)" \
            || warn "Could not update Claude Desktop config automatically"
    else
        # Create new config file
        mkdir -p "$(dirname "$CLAUDE_CONFIG")"
        echo "$SERVER_JSON" > "$CLAUDE_CONFIG"
        success "Claude Desktop config created"
    fi

    info "Restart Claude Desktop (Cmd+Q) for changes to take effect"
else
    warn "Claude Desktop config directory not found"
    info "Install Claude Desktop from https://claude.ai/download"
fi

# ---- Step 5: Claude Code Configuration ----
header "Step 5: Claude Code Configuration"

if [[ -f "$PROJECT_DIR/.mcp.json" ]]; then
    success "Project-level .mcp.json already exists (Claude Code will auto-detect)"
else
    cat > "$PROJECT_DIR/.mcp.json" <<EOJSON
{
  "mcpServers": {
    "celavii-resolve": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "\${CLAUDE_PROJECT_ROOT}",
        "python", "-m", "celavii_resolve"
      ]
    }
  }
}
EOJSON
    success "Created .mcp.json for Claude Code"
fi

# ---- Step 6: Global Skills/Hooks (optional) ----
if $DO_GLOBAL; then
    header "Step 6: Global Skills & Hooks"

    # Install skills as global commands
    mkdir -p "$CLAUDE_CODE_COMMANDS"
    skill_count=0
    for skill_dir in "$PROJECT_DIR"/skills/*/; do
        if [[ -f "$skill_dir/SKILL.md" ]]; then
            skill_name=$(basename "$skill_dir")
            cp "$skill_dir/SKILL.md" "$CLAUDE_CODE_COMMANDS/${skill_name}.md"
            ((skill_count++))
        fi
    done
    success "Installed $skill_count skills to $CLAUDE_CODE_COMMANDS"

    # Install hooks to global settings
    if [[ -f "$CLAUDE_CODE_SETTINGS" ]]; then
        # Merge hooks into existing settings
        "$VENV_PYTHON" -c "
import json
from pathlib import Path

settings_path = Path('$CLAUDE_CODE_SETTINGS')
hooks_path = Path('$PROJECT_DIR/hooks/hooks.json')

settings = json.loads(settings_path.read_text())
hooks = json.loads(hooks_path.read_text())

# Merge hook arrays
for hook_type in ('hooks',):
    if hook_type in hooks:
        if hook_type not in settings:
            settings[hook_type] = hooks[hook_type]
        else:
            # Add hooks that don't already exist (by matcher pattern)
            existing_matchers = {
                json.dumps(h.get('matcher', {}))
                for h in settings[hook_type]
            }
            for hook in hooks[hook_type]:
                if json.dumps(hook.get('matcher', {})) not in existing_matchers:
                    settings[hook_type].append(hook)

settings_path.write_text(json.dumps(settings, indent=2) + '\n')
print('MERGED')
" 2>/dev/null && success "Hooks merged into global settings" \
            || warn "Could not merge hooks — copy hooks/hooks.json to ~/.claude/settings.json manually"
    else
        mkdir -p "$HOME/.claude"
        cp "$PROJECT_DIR/hooks/hooks.json" "$CLAUDE_CODE_SETTINGS"
        success "Created global settings with hooks"
    fi

    # Add global MCP server
    if [[ -f "$CLAUDE_CODE_SETTINGS" ]]; then
        "$VENV_PYTHON" -c "
import json
from pathlib import Path

settings_path = Path('$CLAUDE_CODE_SETTINGS')
settings = json.loads(settings_path.read_text())

if 'mcpServers' not in settings:
    settings['mcpServers'] = {}

settings['mcpServers']['celavii-resolve'] = {
    'command': '$VENV_PYTHON',
    'args': ['-m', '$SERVER_MODULE'],
    'cwd': '$PROJECT_DIR',
}

settings_path.write_text(json.dumps(settings, indent=2) + '\n')
" 2>/dev/null && success "Global MCP server config added to Claude Code settings" \
            || warn "Could not add global MCP config"
    fi
else
    info "Skipping global skills install (use --global to enable)"
fi

# ---- Step 7: LaunchAgent (optional, macOS only) ----
if $DO_LAUNCHAGENT; then
    header "Step 7: macOS LaunchAgent (Auto-Start)"

    if [[ "$(uname)" != "Darwin" ]]; then
        warn "LaunchAgent is macOS-only — skipping on $(uname)"
    elif [[ -f "$PLIST_TEMPLATE" ]]; then
        # Fill in the template with actual paths
        sed \
            -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
            -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
            -e "s|__SERVER_MODULE__|$SERVER_MODULE|g" \
            "$PLIST_TEMPLATE" > "$PLIST_DEST"

        chmod 644 "$PLIST_DEST"

        # Unload if already loaded
        launchctl unload "$PLIST_DEST" 2>/dev/null || true

        # Load the new plist
        launchctl load "$PLIST_DEST"
        success "LaunchAgent installed and loaded"
        info "The MCP server will now start automatically on login"
        info "Logs: /tmp/celavii-resolve-mcp.out.log"
    else
        warn "LaunchAgent template not found at $PLIST_TEMPLATE"
    fi
else
    if [[ "$(uname)" == "Darwin" ]]; then
        info "Skipping LaunchAgent (use --launchagent to enable)"
    fi
fi

# ---- Summary ----
header "Setup Complete"

echo -e "  ${BOLD}Project:${NC}         $PROJECT_DIR"
echo -e "  ${BOLD}Python:${NC}          $VENV_PYTHON"
echo -e "  ${BOLD}Resolve API:${NC}     ${RESOLVE_API:-not found}"
echo ""
echo -e "  ${BOLD}What's configured:${NC}"
echo -e "    ${GREEN}+${NC} Virtual environment with all dependencies"
echo -e "    ${GREEN}+${NC} Claude Desktop MCP server"
echo -e "    ${GREEN}+${NC} Claude Code project-level config (.mcp.json)"
if $DO_GLOBAL; then
    echo -e "    ${GREEN}+${NC} Global skills, hooks, and MCP server for Claude Code"
fi
if $DO_LAUNCHAGENT && [[ "$(uname)" == "Darwin" ]]; then
    echo -e "    ${GREEN}+${NC} macOS LaunchAgent for auto-start"
fi
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "    1. Restart Claude Desktop (Cmd+Q)"
echo -e "    2. Open DaVinci Resolve Studio"
echo -e "    3. Ask Claude: \"Use celavii_get_version to check the connection\""
echo ""
echo -e "  ${BOLD}Documentation:${NC}   $PROJECT_DIR/docs/SETUP.md"
echo ""
