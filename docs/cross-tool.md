# Cross-tool compatibility

This project ships as a portable **AI agent skill** that works identically in
multiple tools. This document explains the format, supported tools, and how
to install the skill in each.

## What is a "skill"?

A skill is a directory containing a `SKILL.md` file with YAML frontmatter that
the AI tool reads to understand what the skill does and when to invoke it.

```markdown
---
name: 4kvm-downloader
description: Download videos from 4kvm.net by automating the full pipeline — extract m3u8 via Playwright, download segments, strip PNG wrappers, and convert to MP4. Trigger when user provides a 4kvm.net URL or asks to download from 4kvm.
---

# 4kvm.net Video Downloader

... (workflow instructions) ...
```

When a user makes a request that matches the `description`, the AI tool
auto-loads the skill content into context and follows the workflow.

## Why the same file works everywhere

The `SKILL.md` format is intentionally minimal — just YAML frontmatter
(`name` + `description`) plus markdown body. Multiple tools have adopted this
format:

| Tool | Skill location | Format spec |
|------|---------------|-------------|
| [Claude Code](https://claude.com/claude-code) | `~/.claude/skills/<name>/` or `./.claude/skills/<name>/` | YAML frontmatter + markdown |
| [Codex (OpenAI)](https://github.com/openai/codex) | `~/.codex/skills/<name>/` or `./.codex/skills/<name>/` | Same as Claude Code, optional `metadata.short-description` |
| [OpenCode](https://opencode.ai) | `~/.config/opencode/skills/<name>/` | Compatible |
| [Continue.dev](https://continue.dev) | `~/.continue/` | Manual config (`config.json`) |
| Cursor | `.cursor/rules/<name>.mdc` | Similar but uses `.mdc` extension |
| Aider | `.aider/skills/` (planned) | Compatible |

Because the format is so similar, the same `SKILL.md` works without
modification in all of them.

## Per-tool installation

### Claude Code

**User-global (available in all your projects):**
```bash
cd /path/to/4kvm-downloader
./install.sh
# This creates ~/.claude/skills/4kvm-downloader → repo/.claude/skills/4kvm-downloader
```

**Project-local (only this repo):**
```bash
cd /path/to/4kvm-downloader
./install.sh --project
# Creates ./.claude/skills/4kvm-downloader
```

**Manual:**
```bash
ln -s /path/to/4kvm-downloader/.claude/skills/4kvm-downloader \
      ~/.claude/skills/4kvm-downloader
```

Then restart Claude Code. The skill appears in the system prompt and is
auto-loaded when you ask about 4kvm URLs.

### Codex

```bash
cd /path/to/4kvm-downloader
./install.sh    # creates ~/.codex/skills/4kvm-downloader symlink
```

Restart Codex. Same as Claude Code, the skill is loaded on demand.

### OpenCode

```bash
./install.sh    # creates ~/.config/opencode/skills/4kvm-downloader symlink
```

### Continue.dev

Continue.dev uses a different config format. Add to `~/.continue/config.json`:

```json
{
  "experimental": {
    "skills": [
      {
        "name": "4kvm-downloader",
        "path": "/path/to/4kvm-downloader/.claude/skills/4kvm-downloader/SKILL.md"
      }
    ]
  }
}
```

### Cursor

Cursor uses `.mdc` files (Markdown with Configuration). To use this skill:

1. Copy `SKILL.md` to `.cursor/rules/4kvm-downloader.mdc`
2. Add YAML frontmatter describing when to apply:

```markdown
---
description: Download videos from 4kvm.net by automating the full pipeline
globs: ["**/*.mp4", "**/4kvm*"]
alwaysApply: false
---

(paste the rest of SKILL.md here)
```

(Or skip — the .mdc format is more complex and not directly compatible.)

## Verifying the install

After installation, the symlink tree should look like this:

```
~/.claude/skills/4kvm-downloader       → /path/to/4kvm-downloader/.claude/skills/4kvm-downloader
~/.codex/skills/4kvm-downloader         → /path/to/4kvm-downloader/.claude/skills/4kvm-downloader
~/.config/opencode/skills/4kvm-downloader → /path/to/4kvm-downloader/.claude/skills/4kvm-downloader
```

You can verify with:
```bash
./install.sh --list
```

## Triggering the skill

Once installed, the skill is auto-invoked based on the `description`
frontmatter. The description is a concise natural-language summary of what
triggers it. Ask the AI:

> "Download https://www.4kvm.net/play/ch46zvt3r"

> "Get me the video at this URL: https://www.4kvm.net/play/abc123"

The tool will detect the `4kvm.net` domain, load the skill, and follow the
6-step workflow defined in `SKILL.md`.

## Why symlinks, not copies?

`./install.sh` creates **symlinks**, not file copies. This means:

| | Symlink (our approach) | Copy |
|---|---|---|
| Updates via `git pull` | ✅ automatic | ❌ need to reinstall |
| Edit to skill, then test | ✅ immediate | ❌ need to reinstall |
| Disk usage | ✅ ~0 bytes | ❌ ~16 KB × N tools |
| Source of truth | ✅ one place | ❌ N copies can drift |

To uninstall: `./install.sh --uninstall` removes all the symlinks pointing
to this skill. The source files in the repo are untouched.

## Contributing a new tool

To add support for a new tool to `install.sh`:

1. Determine the skill location for the tool (e.g. `~/.config/<tool>/skills/`)
2. Add an entry to the `TOOLS` array in `install.sh`:
   ```bash
   TOOLS=(
       "claude-code|$HOME/.claude/skills/$SKILL_NAME|.claude/skills/$SKILL_NAME"
       "codex|$HOME/.codex/skills/$SKILL_NAME|.codex/skills/$SKILL_NAME"
       "new-tool|$HOME/.config/new-tool/skills/$SKILL_NAME|.new-tool/skills/$SKILL_NAME"
   )
   ```
3. Test with `./install.sh --list` then `./install.sh`
4. Update the table at the top of this file
5. Open a PR

## See also

- [SKILL.md](../.claude/skills/4kvm-downloader/SKILL.md) — the actual skill content
- [README.md](../README.md) — main project docs
- [install.sh](../install.sh) — the installer
