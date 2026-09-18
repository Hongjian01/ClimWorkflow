#!/usr/bin/env python3
"""E2E tests for React TUI redesign with kimi model.

Tests the new conversational layout, welcome banner, and tool display.
Uses pexpect to drive the React TUI frontend.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "terminal"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("ANTHROPIC_BASE_URL", "https://api.moonshot.cn/anthropic")
    env.setdefault("ANTHROPIC_MODEL", "kimi-k2.5")
    return env


def test_welcome_banner() -> tuple[bool, str]:
    """Test that the React TUI shows ClimWorkflow on startup."""
    try:
        import pexpect
    except ImportError:
        return False, "pexpect not installed (pip install pexpect)"

    env = _env()
    env["OPENHARNESS_FRONTEND_RAW_RETURN"] = "1"
    # Use scripted steps to send a quick exit
    env["OPENHARNESS_FRONTEND_SCRIPT"] = json.dumps(["/exit"])

    backend_cmd = [sys.executable, "-m", "openharness", "--backend-only"]
    frontend_config = json.dumps({
        "backend_command": backend_cmd,
        "initial_prompt": None,
    })
    env["OPENHARNESS_FRONTEND_CONFIG"] = frontend_config

    child = pexpect.spawn(
        "npm", ["exec", "--", "tsx", "src/index.tsx"],
        cwd=str(FRONTEND_DIR),
        env=env,
        timeout=30,
        encoding="utf-8",
    )
    try:
        # Wait for welcome banner
        child.expect("ClimWorkflow", timeout=15)
        child.expect(pexpect.EOF, timeout=15)
        return True, "Welcome banner displayed with ClimWorkflow"
    except pexpect.TIMEOUT:
        output = child.before or ""
        return False, f"Timeout waiting for welcome banner. Output: {output[:300]}"
    except pexpect.EOF:
        output = child.before or ""
        if "ClimWorkflow" in output:
            return True, "Welcome banner found in output"
        return False, f"EOF before banner. Output: {output[:300]}"
    finally:
        child.close()


def test_conversation_flow() -> tuple[bool, str]:
    """Test that conversation uses vertical layout (no SidePanel)."""
    try:
        import pexpect
    except ImportError:
        return False, "pexpect not installed"

    env = _env()
    env["OPENHARNESS_FRONTEND_RAW_RETURN"] = "1"
    env["OPENHARNESS_FRONTEND_SCRIPT"] = json.dumps(["Say exactly: hello world", "/exit"])

    backend_cmd = [sys.executable, "-m", "openharness", "--backend-only",
                   "--model", env.get("ANTHROPIC_MODEL", "kimi-k2.5")]
    frontend_config = json.dumps({
        "backend_command": backend_cmd,
        "initial_prompt": None,
    })
    env["OPENHARNESS_FRONTEND_CONFIG"] = frontend_config

    child = pexpect.spawn(
        "npm", ["exec", "--", "tsx", "src/index.tsx"],
        cwd=str(FRONTEND_DIR),
        env=env,
        timeout=60,
        encoding="utf-8",
    )
    try:
        # Should see the prompt indicator
        child.expect(">", timeout=15)
        # Should eventually see assistant output
        child.expect(pexpect.EOF, timeout=45)
        output = child.before or ""
        # Verify NO side panel elements (StatusPanel, TaskPanel text)
        has_side_panel = "Tasks" in output and "Bridge" in output and "Commands" in output
        if has_side_panel:
            return False, "Old side panel layout detected"
        return True, f"Conversation layout verified, output length: {len(output)}"
    except pexpect.TIMEOUT:
        return False, f"Timeout. Output: {(child.before or '')[:300]}"
    except pexpect.EOF:
        output = child.before or ""
        return True, f"Conversation completed. Output length: {len(output)}"
    finally:
        child.close()


def test_status_bar() -> tuple[bool, str]:
    """Test that the status bar shows model info."""
    try:
        import pexpect
    except ImportError:
        return False, "pexpect not installed"

    env = _env()
    env["OPENHARNESS_FRONTEND_RAW_RETURN"] = "1"
    env["OPENHARNESS_FRONTEND_SCRIPT"] = json.dumps(["Say hi", "/exit"])

    model_name = env.get("ANTHROPIC_MODEL", "kimi-k2.5")
    backend_cmd = [sys.executable, "-m", "openharness", "--backend-only",
                   "--model", model_name]
    frontend_config = json.dumps({
        "backend_command": backend_cmd,
        "initial_prompt": None,
    })
    env["OPENHARNESS_FRONTEND_CONFIG"] = frontend_config

    child = pexpect.spawn(
        "npm", ["exec", "--", "tsx", "src/index.tsx"],
        cwd=str(FRONTEND_DIR),
        env=env,
        timeout=60,
        encoding="utf-8",
    )
    try:
        child.expect(pexpect.EOF, timeout=45)
        output = child.before or ""
        if "model:" in output.lower():
            return True, "Status bar with model info detected"
        return False, f"No model info in status bar. Output: {output[:300]}"
    except pexpect.TIMEOUT:
        return False, f"Timeout. Output: {(child.before or '')[:300]}"
    finally:
        child.close()


def main() -> None:
    tests = [
        ("welcome_banner", test_welcome_banner),
        ("conversation_flow", test_conversation_flow),
        ("status_bar", test_status_bar),
    ]

    passed = 0
    failed = 0
    for name, func in tests:
        try:
            ok, msg = func()
            if ok:
                print(f"  {GREEN}PASS{RESET} {name}: {msg}")
                passed += 1
            else:
                print(f"  {RED}FAIL{RESET} {name}: {msg}")
                failed += 1
        except Exception as exc:
            print(f"  {RED}ERROR{RESET} {name}: {exc}")
            failed += 1

    print()
    print(f"{BOLD}Results: {passed} passed, {failed} failed{RESET}")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
