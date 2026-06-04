#!/usr/bin/env bash
# install.sh — Install the 4kvm-downloader skill to multiple AI tool locations.
#
# Supports:
#   - Claude Code (~/.claude/skills/ or project-local .claude/skills/)
#   - Codex     (~/.codex/skills/   or project-local .codex/skills/)
#   - Other tools with compatible SKILL.md format
#
# Usage:
#   ./install.sh              # install to all detected user-global locations
#   ./install.sh --project    # install to current project (./.claude, ./.codex)
#   ./install.sh --uninstall  # remove all symlinks
#   ./install.sh --list       # show what would be installed where
#
# This script creates symlinks — it does NOT copy. Updates to the source
# SKILL.md are immediately picked up by the tools.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/4kvm-downloader"
SKILL_NAME="4kvm-downloader"

# Tool name → (user-global path, project-relative path)
TOOLS=(
    "claude-code|$HOME/.claude/skills/$SKILL_NAME|.claude/skills/$SKILL_NAME"
    "codex|$HOME/.codex/skills/$SKILL_NAME|.codex/skills/$SKILL_NAME"
    "opencode|$HOME/.config/opencode/skills/$SKILL_NAME|.opencode/skills/$SKILL_NAME"
)

usage() {
    cat <<EOF
install.sh — Install 4kvm-downloader skill to multiple AI tool locations

Usage:
  $0                  Install to all detected user-global locations
  $0 --project        Install to current project (./.claude, ./.codex)
  $0 --uninstall      Remove all symlinks pointing to this skill
  $0 --list           Show what would be installed where
  $0 -h | --help      Show this help

Source: $SKILL_SRC
EOF
}

list_paths() {
    echo "Source: $SKILL_SRC"
    echo ""
    echo "Would create symlinks at:"
    for entry in "${TOOLS[@]}"; do
        IFS='|' read -r tool user_path project_path <<< "$entry"
        echo "  $tool:"
        echo "    user:   $user_path"
        echo "    project:$project_path"
    done
}

install_one() {
    local target="$1"
    local label="$2"
    if [ -L "$target" ]; then
        local existing
        existing=$(readlink "$target")
        if [ "$existing" = "$SKILL_SRC" ]; then
            echo "  ✓ $label: already linked"
            return 0
        else
            # Existing symlink points elsewhere (likely the old location).
            # Auto-update it to the new canonical path.
            rm "$target"
            ln -s "$SKILL_SRC" "$target"
            echo "  ↻ $label: updated symlink ($existing → $SKILL_SRC)"
            return 0
        fi
    elif [ -e "$target" ]; then
        echo "  ✗ $label: target exists and is not a symlink. Skipping."
        echo "    Remove manually first: rm -rf '$target'"
        return 1
    else
        mkdir -p "$(dirname "$target")"
        ln -s "$SKILL_SRC" "$target"
        echo "  ✓ $label: created symlink"
    fi
}

uninstall_one() {
    local target="$1"
    local label="$2"
    if [ -L "$target" ]; then
        local existing
        existing=$(readlink "$target")
        if [ "$existing" = "$SKILL_SRC" ]; then
            rm "$target"
            echo "  ✓ $label: removed"
        else
            echo "  - $label: symlink points elsewhere, not removing"
        fi
    elif [ -e "$target" ]; then
        echo "  ! $label: not a symlink, skipping"
    else
        echo "  - $label: not present"
    fi
}

install_user() {
    echo "Installing to user-global locations..."
    for entry in "${TOOLS[@]}"; do
        IFS='|' read -r tool user_path project_path <<< "$entry"
        install_one "$user_path" "$tool (user)"
    done
}

install_project() {
    echo "Installing to current project..."
    cd "$SCRIPT_DIR"
    for entry in "${TOOLS[@]}"; do
        IFS='|' read -r tool user_path project_path <<< "$entry"
        install_one "$SCRIPT_DIR/$project_path" "$tool (project)"
    done
}

uninstall_all() {
    echo "Removing user-global symlinks..."
    for entry in "${TOOLS[@]}"; do
        IFS='|' read -r tool user_path project_path <<< "$entry"
        uninstall_one "$user_path" "$tool (user)"
    done
    echo ""
    echo "Removing project symlinks..."
    cd "$SCRIPT_DIR"
    for entry in "${TOOLS[@]}"; do
        IFS='|' read -r tool user_path project_path <<< "$entry"
        uninstall_one "$SCRIPT_DIR/$project_path" "$tool (project)"
    done
}

# Check source exists
if [ ! -d "$SKILL_SRC" ]; then
    echo "Error: skill source not found: $SKILL_SRC" >&2
    exit 1
fi

case "${1:-}" in
    -h|--help)
        usage
        ;;
    --list)
        list_paths
        ;;
    --project)
        install_project
        ;;
    --uninstall)
        uninstall_all
        ;;
    "")
        install_user
        ;;
    *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
esac

echo ""
echo "Done. Restart your AI tool (Claude Code, Codex, etc.) to pick up the skill."
