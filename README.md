# Claude Code Status Line

A cross-platform Python status line for [Claude Code](https://claude.com/claude-code) that displays model info, token usage, rate limits, and reset times. Two display modes: **minimal** (numbers only) and **visual** (progress bars with pacing markers). Runs as an external command — no extra tokens consumed.

> Forked from [daniel3303/ClaudeCodeStatusLine](https://github.com/daniel3303/ClaudeCodeStatusLine) by [Daniel Oliveira](https://danielapoliveira.com/). Rewritten from dual bash/PowerShell into a single Python script.

## What it shows

| Segment | Minimal | Visual |
|---------|---------|--------|
| **Model** | Colour-coded by family (Opus=blue, Sonnet=orange, Haiku=purple) | Same |
| **CWD@Branch** | `folder@branch +N -M` | Same |
| **Context** | `50k/200k (25%)` colour-coded % | `[##--------] 25% 50k/200k` with RAG thresholds |
| **Effort** | `effort: high` colour-coded | Same |
| **5h usage** | `5h 37% @11pm` | `5h [####|-----] 37% @11pm` with pacing marker |
| **7d usage** | `7d 26% @thu 10am` | `7d [###|------] 26% @thu 10am` with pacing marker |
| **Extra** | `extra $1.50/$5.00` colour-coded | Same |

### Colour thresholds

| Element | Green | Yellow | Orange | Red |
|---------|-------|--------|--------|-----|
| Context % (minimal) | <50% | >=50% | >=70% | >=90% |
| Context bar (visual) | tok <100k | -- | tok 100-128k | tok >=128k |
| Usage % (5h/7d) | <50% | >=50% | >=70% | >=90% |
| Effort level | high | -- | med | low (dim) |
| Model name | Per-family: Opus=blue, Sonnet=orange, Haiku=purple, other=cyan |

### Pacing markers

In visual mode, the 5h and 7d bars include a `|` marker showing where you *should* be based on elapsed time in the window. If your filled bar is ahead of the marker, you're using faster than the steady-state pace.

## Requirements

- Python 3.7+
- `git` in PATH (for branch/diff info)
- Claude Code with OAuth authentication (Pro/Max subscription)

No external dependencies — uses only the Python standard library.

## Installation

### Quick setup

Copy `statusline.py` and paste it into Claude Code with the prompt:

> Use this script as my status bar

Claude Code will save the script and configure `settings.json` for you.

### Manual setup (all platforms)

1. Copy the script:

   ```bash
   # macOS / Linux
   cp statusline.py ~/.claude/statusline.py

   # Windows (PowerShell)
   Copy-Item statusline.py "$env:USERPROFILE\.claude\statusline.py"
   ```

2. Add to `~/.claude/settings.json`:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python ~/.claude/statusline.py"
     }
   }
   ```

   For minimal mode:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python ~/.claude/statusline.py --mode minimal"
     }
   }
   ```

   On Windows, use the full path:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python \"%USERPROFILE%\\.claude\\statusline.py\""
     }
   }
   ```

3. Restart Claude Code.

## Credential resolution

The script looks for your OAuth token in this order:

1. `CLAUDE_CODE_OAUTH_TOKEN` environment variable
2. macOS Keychain (`security find-generic-password`)
3. Credentials file (`~/.claude/.credentials.json` or `%LOCALAPPDATA%/Claude Code/credentials.json`)
4. GNOME Keyring (`secret-tool lookup`)

## Caching

Usage API data is cached for 60 seconds in a platform-appropriate temp directory. Falls back to stale cache if the API call fails.

## License

MIT

## Original author

Daniel Oliveira

[![Website](https://img.shields.io/badge/Website-FF6B6B?style=for-the-badge&logo=safari&logoColor=white)](https://danielapoliveira.com/)
[![X](https://img.shields.io/badge/X-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/daniel_not_nerd)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/daniel-ap-oliveira/)
