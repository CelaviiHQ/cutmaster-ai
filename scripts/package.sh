#!/usr/bin/env bash
# ============================================================================
# CutMaster AI — Package Creator
#
# Creates a distributable zip archive of the project for sharing.
# Excludes .venv, .env, __pycache__, .git, and other build artifacts.
#
# Usage:
#   bash scripts/package.sh                    # Creates cutmaster-ai-v0.1.0.zip
#   bash scripts/package.sh --output ~/Desktop # Creates zip on Desktop
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_NAME="cutmaster-ai"

# Read version from pyproject.toml
VERSION=$(grep '^version' "$PROJECT_DIR/pyproject.toml" | head -1 | sed 's/version = "\(.*\)"/\1/')
VERSION="${VERSION:-0.1.0}"

# Parse arguments
OUTPUT_DIR="$PROJECT_DIR"
for arg in "$@"; do
    case "$arg" in
        --output)   shift; OUTPUT_DIR="${1:-.}" ;;
        --output=*) OUTPUT_DIR="${arg#--output=}" ;;
    esac
done

ARCHIVE_NAME="cutmaster-ai-v${VERSION}.zip"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"

echo ""
echo "  CutMaster AI Package Creator"
echo "  Version: $VERSION"
echo "  Output:  $ARCHIVE_PATH"
echo ""

# Create temporary staging directory
STAGE_DIR=$(mktemp -d)
STAGE_PROJECT="$STAGE_DIR/$PROJECT_NAME"
trap 'rm -rf "$STAGE_DIR"' EXIT

echo "  Staging files..."

# Copy project files (excluding unwanted items)
rsync -a --quiet \
    --exclude='.venv/' \
    --exclude='.env' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='.ruff_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.pytest_cache/' \
    --exclude='*.egg-info/' \
    --exclude='dist/' \
    --exclude='build/' \
    --exclude='Implementations/' \
    --exclude='.DS_Store' \
    --exclude='node_modules/' \
    "$PROJECT_DIR/" "$STAGE_PROJECT/"

# Count what we're packaging
FILE_COUNT=$(find "$STAGE_PROJECT" -type f | wc -l | tr -d ' ')
SKILL_COUNT=$(find "$STAGE_PROJECT/skills" -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
AGENT_COUNT=$(find "$STAGE_PROJECT/agents" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')

echo "  Files:   $FILE_COUNT"
echo "  Skills:  $SKILL_COUNT"
echo "  Agents:  $AGENT_COUNT"
echo ""

# Create the zip
echo "  Creating archive..."
cd "$STAGE_DIR"
zip -r -q "$ARCHIVE_PATH" "$PROJECT_NAME/"

# Final size
SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1 | tr -d ' ')
echo ""
echo "  Package created: $ARCHIVE_PATH ($SIZE)"
echo ""
echo "  To install, the recipient should run:"
echo "    unzip $ARCHIVE_NAME"
echo "    cd $PROJECT_NAME"
echo "    bash scripts/setup.sh"
echo ""
