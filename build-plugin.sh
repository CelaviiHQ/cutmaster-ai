#!/usr/bin/env bash
# ============================================================================
# Celavii-Resolve — Plugin Builder
#
# Builds a Claude Code plugin ZIP containing:
#   .claude-plugin/plugin.json   — plugin manifest
#   .mcp.json                    — MCP server config
#   skills/                      — 9 Claude Code skills
#
# Usage:
#   bash build-plugin.sh                         # Interactive — prompts for venv path
#   bash build-plugin.sh --venv /path/to/.venv   # Explicit venv path
#   bash build-plugin.sh --skills-only           # Skills only, no MCP server
#   bash build-plugin.sh --output ~/Desktop      # Output to specific directory
#
# The recipient installs with:
#   claude plugin install ./celavii-resolve-plugin.zip
#
# Or test locally:
#   claude --plugin-dir ./celavii-davinci-resolve-mcp
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# If script is in scripts/, go up one level
if [[ "$(basename "$SCRIPT_DIR")" == "scripts" ]]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

# Read version from pyproject.toml
VERSION=$(grep '^version' "$PROJECT_DIR/pyproject.toml" | head -1 | sed 's/version = "\(.*\)"/\1/')
VERSION="${VERSION:-0.1.0}"

# Defaults
VENV_PATH=""
SKILLS_ONLY=false
OUTPUT_DIR="$PROJECT_DIR"
SERVER_MODULE="celavii_resolve"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv)         shift; VENV_PATH="$1" ;;
        --venv=*)       VENV_PATH="${1#--venv=}" ;;
        --skills-only)  SKILLS_ONLY=true ;;
        --output)       shift; OUTPUT_DIR="$1" ;;
        --output=*)     OUTPUT_DIR="${1#--output=}" ;;
        --help|-h)
            echo "Usage: bash build-plugin.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --venv PATH       Path to Python venv (default: auto-detect .venv)"
            echo "  --skills-only     Include skills only, no MCP server config"
            echo "  --output DIR      Output directory (default: project root)"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

echo ""
echo -e "${BOLD}${CYAN}  Celavii-Resolve Plugin Builder${NC}"
echo -e "  Version: ${VERSION}"
echo ""

# Check required files
if [[ ! -f "$PROJECT_DIR/.claude-plugin/plugin.json" ]]; then
    error ".claude-plugin/plugin.json not found"
    exit 1
fi

if [[ ! -d "$PROJECT_DIR/skills" ]]; then
    error "skills/ directory not found"
    exit 1
fi

# Find venv
if [[ -z "$VENV_PATH" ]]; then
    if [[ -f "$PROJECT_DIR/.venv/bin/python3" ]]; then
        VENV_PATH="$PROJECT_DIR/.venv"
    else
        warn "No .venv found. Use --venv to specify or --skills-only to skip MCP config."
        if ! $SKILLS_ONLY; then
            echo ""
            read -rp "  Enter path to Python venv (or press Enter for skills-only): " VENV_PATH
            if [[ -z "$VENV_PATH" ]]; then
                SKILLS_ONLY=true
                info "Building skills-only plugin (no MCP server)"
            fi
        fi
    fi
fi

if [[ -n "$VENV_PATH" ]] && [[ ! -f "$VENV_PATH/bin/python3" ]]; then
    error "Python not found at $VENV_PATH/bin/python3"
    exit 1
fi

# ---------------------------------------------------------------------------
# Stage files
# ---------------------------------------------------------------------------

STAGE_DIR=$(mktemp -d)
trap 'rm -rf "$STAGE_DIR"' EXIT

info "Staging plugin files..."

# 1. Plugin manifest
mkdir -p "$STAGE_DIR/.claude-plugin"
cp "$PROJECT_DIR/.claude-plugin/plugin.json" "$STAGE_DIR/.claude-plugin/"

# 2. Skills
cp -r "$PROJECT_DIR/skills" "$STAGE_DIR/skills"

# 3. MCP config
if $SKILLS_ONLY; then
    # Empty mcpServers — skills only
    cat > "$STAGE_DIR/.mcp.json" <<'EOJSON'
{
  "mcpServers": {}
}
EOJSON
    info "MCP config: skills-only (empty mcpServers)"
else
    VENV_PYTHON="$VENV_PATH/bin/python3"
    cat > "$STAGE_DIR/.mcp.json" <<EOJSON
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
    info "MCP config: stdio via $VENV_PYTHON"
fi

# ---------------------------------------------------------------------------
# Count contents
# ---------------------------------------------------------------------------

SKILL_COUNT=$(find "$STAGE_DIR/skills" -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
FILE_COUNT=$(find "$STAGE_DIR" -type f | wc -l | tr -d ' ')

echo ""
echo -e "  ${BOLD}Plugin contents:${NC}"
echo -e "    Skills:  ${SKILL_COUNT}"
echo -e "    Files:   ${FILE_COUNT}"
echo -e "    Mode:    $(if $SKILLS_ONLY; then echo "skills-only"; else echo "full (MCP + skills)"; fi)"
echo ""

# ---------------------------------------------------------------------------
# Build ZIP
# ---------------------------------------------------------------------------

ARCHIVE_NAME="celavii-resolve-plugin-v${VERSION}.zip"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"

info "Creating plugin ZIP..."
cd "$STAGE_DIR"
zip -r -q "$ARCHIVE_PATH" .claude-plugin/ skills/ .mcp.json

SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1 | tr -d ' ')

echo ""
echo -e "  ${GREEN}${BOLD}Plugin built successfully!${NC}"
echo ""
echo -e "  ${BOLD}File:${NC}    $ARCHIVE_PATH ($SIZE)"
echo -e "  ${BOLD}Skills:${NC}  $SKILL_COUNT"
echo -e "  ${BOLD}Version:${NC} $VERSION"
echo ""
echo -e "  ${BOLD}Install:${NC}"
echo -e "    claude plugin install $ARCHIVE_PATH"
echo ""
echo -e "  ${BOLD}Test locally:${NC}"
echo -e "    claude --plugin-dir $PROJECT_DIR"
echo ""
