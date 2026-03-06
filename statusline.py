#!/usr/bin/env python3
"""Claude Code status line — single cross-platform script.

Reads JSON from stdin (piped by Claude Code) and outputs a colour-coded
status line.  Two display modes:

  --mode minimal   numbers only (like the original bash/PS1 scripts)
  --mode visual    bars, pacing markers, conditional formatting (default)
"""

import argparse
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ── ANSI colours ────────────────────────────────────────────────────────────

BLUE = "\033[38;2;0;153;255m"
ORANGE = "\033[38;2;255;176;85m"
GREEN = "\033[38;2;0;160;0m"
CYAN = "\033[38;2;46;149;153m"
RED = "\033[38;2;255;85;85m"
YELLOW = "\033[38;2;230;200;0m"
WHITE = "\033[38;2;220;220;220m"
PURPLE = "\033[38;2;180;100;255m"
DIM = "\033[2m"
RESET = "\033[0m"

SEP = f" {DIM}|{RESET} "

# ── Helpers ─────────────────────────────────────────────────────────────────


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def model_colour(name: str) -> str:
    low = name.lower()
    if "opus" in low:
        return BLUE
    if "sonnet" in low:
        return ORANGE
    if "haiku" in low:
        return PURPLE
    return CYAN


def usage_colour(pct: float) -> str:
    if pct >= 90:
        return RED
    if pct >= 70:
        return ORANGE
    if pct >= 50:
        return YELLOW
    return GREEN


def context_colour(tokens: int) -> str:
    """RAG-style thresholds on absolute token count (for visual mode)."""
    if tokens >= 128_000:
        return RED
    if tokens >= 100_000:
        return ORANGE
    return GREEN


def effort_segment(level: str) -> str:
    if level == "low":
        return f"{DIM}low{RESET}"
    if level in ("medium", "med"):
        return f"{ORANGE}med{RESET}"
    return f"{GREEN}high{RESET}"


def make_bar(pct: float, width: int = 10, target_pct: float | None = None) -> str:
    """Render a progress bar with optional pacing marker."""
    filled_char = "\u2593"  # ▓ dark shade
    empty_char = "\u2591"   # ░ light shade
    marker_char = "\u2502"  # │ thin vertical line

    clamped = max(0.0, min(100.0, pct))
    filled = round(clamped / 100 * width)
    bar = list(filled_char * filled + empty_char * (width - filled))

    if target_pct is not None:
        marker_pos = round(max(0.0, min(100.0, target_pct)) / 100 * width)
        marker_pos = min(marker_pos, width - 1)
        bar[marker_pos] = marker_char

    colour = usage_colour(pct)
    return f"{colour}{''.join(bar)}{RESET}"


# ── Time formatting ─────────────────────────────────────────────────────────


def format_reset_time(iso_str: str | None, style: str = "time") -> str | None:
    if not iso_str or iso_str == "null":
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        if style == "time":
            return local.strftime("%-I:%M%p").lower() if platform.system() != "Windows" \
                else local.strftime("%#I:%M%p").lower()
        if style == "datetime":
            fmt = "%-d" if platform.system() != "Windows" else "%#d"
            day = local.strftime(fmt)
            return f"{local.strftime('%a')} {day}, {local.strftime('%I:%M%p').lstrip('0').lower()}"
        return local.strftime("%b %d").lower()
    except Exception:
        return None


def _pacing_target(iso_str: str | None, window_hours: float) -> float | None:
    """Calculate the ideal pacing percentage based on elapsed time in window."""
    if not iso_str or iso_str == "null":
        return None
    try:
        resets_at = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        window_start = resets_at - __import__("datetime").timedelta(hours=window_hours)
        elapsed = (now - window_start).total_seconds()
        total = window_hours * 3600
        if total <= 0:
            return None
        return max(0.0, min(100.0, elapsed / total * 100))
    except Exception:
        return None


# ── Git info ────────────────────────────────────────────────────────────────


def get_git_info(cwd: str) -> tuple[str | None, str | None]:
    """Return (branch, diff_stats) for the given directory."""
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:
        branch = None

    diff_stats = None
    if branch:
        try:
            numstat = subprocess.run(
                ["git", "-C", cwd, "--no-optional-locks", "diff", "--numstat"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if numstat:
                added = deleted = 0
                for line in numstat.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            added += int(parts[0])
                        except ValueError:
                            pass
                        try:
                            deleted += int(parts[1])
                        except ValueError:
                            pass
                if added + deleted > 0:
                    diff_stats = f"+{added} -{deleted}"
        except Exception:
            pass

    return branch, diff_stats


# ── Effort level ────────────────────────────────────────────────────────────


def get_effort_level() -> str:
    env = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    if env:
        return env

    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            val = data.get("effortLevel")
            if val:
                return val
        except Exception:
            pass
    return "medium"


# ── OAuth token ─────────────────────────────────────────────────────────────


def get_oauth_token() -> str | None:
    # 1. Env var
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token

    # 2. macOS Keychain
    if platform.system() == "Darwin":
        try:
            blob = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if blob:
                data = json.loads(blob)
                t = data.get("claudeAiOauth", {}).get("accessToken")
                if t and t != "null":
                    return t
        except Exception:
            pass

    # 3. Credentials file (~/.claude/.credentials.json or LOCALAPPDATA on Windows)
    for creds_path in [
        Path.home() / ".claude" / ".credentials.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Claude Code" / "credentials.json",
    ]:
        if creds_path.exists():
            try:
                data = json.loads(creds_path.read_text())
                t = data.get("claudeAiOauth", {}).get("accessToken")
                if t and t != "null":
                    return t
            except Exception:
                pass

    # 4. GNOME Keyring
    if platform.system() == "Linux":
        try:
            blob = subprocess.run(
                ["secret-tool", "lookup", "service", "Claude Code-credentials"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if blob:
                data = json.loads(blob)
                t = data.get("claudeAiOauth", {}).get("accessToken")
                if t and t != "null":
                    return t
        except Exception:
            pass

    return None


# ── Usage API with caching ──────────────────────────────────────────────────


def _cache_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    else:
        base = Path("/tmp")
    d = base / "claude"
    d.mkdir(parents=True, exist_ok=True)
    return d / "statusline-usage-cache.json"


def load_usage() -> dict | None:
    cache = _cache_path()
    cache_ttl = 60  # seconds

    # Try cache first
    usage_data = None
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < cache_ttl:
            try:
                return json.loads(cache.read_text())
            except Exception:
                pass

    # Fetch fresh
    token = get_oauth_token()
    if token:
        try:
            import urllib.request

            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "User-Agent": "claude-code/2.1.34",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                usage_data = json.loads(body)
                cache.write_text(body)
        except Exception:
            pass

    # Stale cache fallback
    if usage_data is None and cache.exists():
        try:
            usage_data = json.loads(cache.read_text())
        except Exception:
            pass

    return usage_data


# ── Output builders ─────────────────────────────────────────────────────────


def build_minimal(data: dict) -> str:
    parts: list[str] = []

    # Model + Effort
    model_name = (data.get("model") or {}).get("display_name", "Claude")
    effort = get_effort_level()
    parts.append(f"{model_colour(model_name)}{model_name}{RESET}{DIM},{RESET} {effort_segment(effort)}")

    # CWD@Branch
    cwd = data.get("cwd")
    if cwd:
        display_dir = Path(cwd).name
        branch, diff = get_git_info(cwd)
        seg = f"{CYAN}{display_dir}{RESET}"
        if branch:
            seg += f"{DIM}@{RESET}{GREEN}{branch}{RESET}"
            if diff:
                add_part, del_part = diff.split(" ")
                seg += f" {DIM}({RESET}{GREEN}{add_part}{RESET} {RED}{del_part}{RESET}{DIM}){RESET}"
        parts.append(seg)

    # Context
    cw = data.get("context_window") or {}
    size = cw.get("context_window_size") or 200_000
    if size == 0:
        size = 200_000
    usage = cw.get("current_usage") or {}
    current = (usage.get("input_tokens") or 0) + \
              (usage.get("cache_creation_input_tokens") or 0) + \
              (usage.get("cache_read_input_tokens") or 0)
    pct = int(current * 100 / size) if size > 0 else 0
    pct_colour = usage_colour(pct)
    parts.append(
        f"{WHITE}{format_tokens(current)}/{format_tokens(size)}{RESET}"
        f" {DIM}({RESET}{pct_colour}{pct}%{RESET}{DIM}){RESET}"
    )

    # Usage (5h / 7d / extra)
    ud = load_usage()
    if ud:
        # 5h
        fh = ud.get("five_hour") or {}
        fh_pct = int(fh.get("utilization") or 0)
        fh_reset = format_reset_time(fh.get("resets_at"), "time")
        seg = f"{WHITE}5h{RESET} {usage_colour(fh_pct)}{fh_pct}%{RESET}"
        if fh_reset:
            seg += f" {DIM}@{fh_reset}{RESET}"
        parts.append(seg)

        # 7d
        sd = ud.get("seven_day") or {}
        sd_pct = int(sd.get("utilization") or 0)
        sd_reset = format_reset_time(sd.get("resets_at"), "datetime")
        seg = f"{WHITE}7d{RESET} {usage_colour(sd_pct)}{sd_pct}%{RESET}"
        if sd_reset:
            seg += f" {DIM}@{sd_reset}{RESET}"
        parts.append(seg)

        # Extra
        extra = ud.get("extra_usage") or {}
        if extra.get("is_enabled"):
            used_raw = extra.get("used_credits")
            limit_raw = extra.get("monthly_limit")
            if used_raw is not None and limit_raw is not None:
                used_d = used_raw / 100
                limit_d = limit_raw / 100
                ep = int(extra.get("utilization") or 0)
                parts.append(
                    f"{WHITE}extra{RESET} {usage_colour(ep)}${used_d:.2f}/${limit_d:.2f}{RESET}"
                )
            else:
                parts.append(f"{WHITE}extra{RESET} {GREEN}enabled{RESET}")

    return SEP.join(parts)


def build_visual(data: dict) -> str:
    parts: list[str] = []

    # Model
    # Model + Effort
    model_name = (data.get("model") or {}).get("display_name", "Claude")
    effort = get_effort_level()
    parts.append(f"{model_colour(model_name)}{model_name}{RESET}{DIM},{RESET} {effort_segment(effort)}")

    # CWD@Branch (same as minimal)
    cwd = data.get("cwd")
    if cwd:
        display_dir = Path(cwd).name
        branch, diff = get_git_info(cwd)
        seg = f"{CYAN}{display_dir}{RESET}"
        if branch:
            seg += f"{DIM}@{RESET}{GREEN}{branch}{RESET}"
            if diff:
                add_part, del_part = diff.split(" ")
                seg += f" {DIM}({RESET}{GREEN}{add_part}{RESET} {RED}{del_part}{RESET}{DIM}){RESET}"
        parts.append(seg)

    # Context — bar + pct + tokens, colour by absolute token count
    cw = data.get("context_window") or {}
    size = cw.get("context_window_size") or 200_000
    if size == 0:
        size = 200_000
    usage = cw.get("current_usage") or {}
    current = (usage.get("input_tokens") or 0) + \
              (usage.get("cache_creation_input_tokens") or 0) + \
              (usage.get("cache_read_input_tokens") or 0)
    pct = int(current * 100 / size) if size > 0 else 0
    ctx_col = context_colour(current)
    bar = make_bar(pct, width=10)
    parts.append(
        f"{bar} {ctx_col}{pct}%{RESET} {WHITE}{format_tokens(current)}/{format_tokens(size)}{RESET}"
    )

    # Usage (5h / 7d / extra)
    ud = load_usage()
    if ud:
        # 5h with pacing marker
        fh = ud.get("five_hour") or {}
        fh_pct = int(fh.get("utilization") or 0)
        fh_reset_iso = fh.get("resets_at")
        fh_reset = format_reset_time(fh_reset_iso, "time")
        target = _pacing_target(fh_reset_iso, 5.0)
        bar = make_bar(fh_pct, width=10, target_pct=target)
        seg = f"{WHITE}5h{RESET} {bar} {usage_colour(fh_pct)}{fh_pct}%{RESET}"
        if fh_reset:
            seg += f" {DIM}@{fh_reset}{RESET}"
        parts.append(seg)

        # 7d with pacing marker
        sd = ud.get("seven_day") or {}
        sd_pct = int(sd.get("utilization") or 0)
        sd_reset_iso = sd.get("resets_at")
        sd_reset = format_reset_time(sd_reset_iso, "datetime")
        target = _pacing_target(sd_reset_iso, 168.0)
        bar = make_bar(sd_pct, width=10, target_pct=target)
        seg = f"{WHITE}7d{RESET} {bar} {usage_colour(sd_pct)}{sd_pct}%{RESET}"
        if sd_reset:
            seg += f" {DIM}@{sd_reset}{RESET}"
        parts.append(seg)

        # Extra (no bar — dollar amounts)
        extra = ud.get("extra_usage") or {}
        if extra.get("is_enabled"):
            used_raw = extra.get("used_credits")
            limit_raw = extra.get("monthly_limit")
            if used_raw is not None and limit_raw is not None:
                used_d = used_raw / 100
                limit_d = limit_raw / 100
                ep = int(extra.get("utilization") or 0)
                parts.append(
                    f"{WHITE}extra{RESET} {usage_colour(ep)}${used_d:.2f}/${limit_d:.2f}{RESET}"
                )
            else:
                parts.append(f"{WHITE}extra{RESET} {GREEN}enabled{RESET}")

    return SEP.join(parts)


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    # Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Claude Code status line")
    parser.add_argument("--mode", choices=["minimal", "visual"], default="visual")
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        print("Claude", end="")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("Claude", end="")
        return

    if args.mode == "minimal":
        print(build_minimal(data), end="")
    else:
        print(build_visual(data), end="")


if __name__ == "__main__":
    main()
