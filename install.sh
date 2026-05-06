#!/usr/bin/env bash
# install.sh — installs the Personal Health Record skill for Claude Code
# Does not require git. Downloads a ZIP archive from GitHub.

set -e

GITHUB_USER="romahakov"
GITHUB_REPO="personal-health-record"
BRANCH="main"
INSTALL_DIR="$HOME/.claude/skills/personal-health-record"
ZIP_URL="https://github.com/$GITHUB_USER/$GITHUB_REPO/archive/refs/heads/$BRANCH.zip"
TMP_DIR="$(mktemp -d)"

# ── helpers ──────────────────────────────────────────────────────────────────

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
die()    { red "Error: $*"; exit 1; }

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# ── checks ───────────────────────────────────────────────────────────────────

echo ""
echo "Personal Health Record — Claude Code Skill Installer"
echo "─────────────────────────────────────────────────────"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    die "Python 3 is not installed. Install it from https://python.org and re-run this script."
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 10 ]; then
    die "Python 3.10 or newer is required. You have $(python3 --version)."
fi

# Check ~/.claude/skills exists
if [ ! -d "$HOME/.claude/skills" ]; then
    die "Claude Code skills directory not found at ~/.claude/skills.\nMake sure Claude Code is installed: https://claude.ai/download"
fi

# Handle existing installation
if [ -d "$INSTALL_DIR" ]; then
    yellow "Skill already installed at $INSTALL_DIR"
    read -r -p "Reinstall / update? [y/N] " answer
    case "$answer" in
        [yY]*) rm -rf "$INSTALL_DIR" ;;
        *) echo "Cancelled."; exit 0 ;;
    esac
fi

# ── download ─────────────────────────────────────────────────────────────────

echo "Downloading..."

if command -v curl &>/dev/null; then
    curl -fsSL "$ZIP_URL" -o "$TMP_DIR/skill.zip"
elif command -v wget &>/dev/null; then
    wget -q "$ZIP_URL" -O "$TMP_DIR/skill.zip"
else
    die "Neither curl nor wget found. Install one and re-run."
fi

# ── extract ──────────────────────────────────────────────────────────────────

echo "Installing..."

unzip -q "$TMP_DIR/skill.zip" -d "$TMP_DIR"

# GitHub adds -main suffix to the extracted folder
EXTRACTED="$TMP_DIR/$GITHUB_REPO-$BRANCH"
if [ ! -d "$EXTRACTED" ]; then
    die "Unexpected archive structure. Please report this issue on GitHub."
fi

mv "$EXTRACTED" "$INSTALL_DIR"

# ── done ─────────────────────────────────────────────────────────────────────

echo ""
green "Skill installed successfully at $INSTALL_DIR"
echo ""
echo "Next steps:"
echo "  1. Open Claude Code"
echo "  2. Start a new conversation and say:"
echo "     \"Set up my medical records archive\""
echo "  3. Claude will guide you through the first-run setup"
echo ""
