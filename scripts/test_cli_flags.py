#!/usr/bin/env python3
"""E2E tests for CLI flags using kimi model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Colors for output
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"

def _env() -> dict[str, str]:
    """Return environment with kimi model configuration."""
    env = os.environ.copy()
    env.setdefault("ANTHROPIC_BASE_URL", "https://api.moonshot.cn/anthropic")
    env.setdefault("ANTHROPIC_MODEL", "kimi-k2.5")
    return env


def _run_oh(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the oh CLI with the given args."""
    cmd = [sys.executable, "-m", "openharness", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_env(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_help_output() -> tuple[bool, str]:
    """Test that --help shows all flag groups."""
    result = _run_oh("--help")
    output = result.stdout + result.stderr
    checks = [
        "ClimWorkflow" in output,
        "Session" in output,
        "Model & Effort" in output,
        "Output" in output,
        "Permissions" in output,
        "System & Context" in output,
        "Advanced" in output,
        "--print" in output,
        "--model" in output,
        "--permission-mode" in output,
        "mcp" in output,
        "plugin" in output,
        "auth" in output,
    ]
    if all(checks):
        return True, "All flag groups and subcommands present in help"
    missing = [
        name for name, ok in zip(
            ["branding", "Session", "Model", "Output", "Permissions", "Context", "Advanced",
             "--print", "--model", "--permission-mode", "mcp", "plugin", "auth"],
            checks,
        ) if not ok
    ]
    return False, f"Missing in help output: {missing}"


def test_print_mode() -> tuple[bool, str]:
    """Test -p flag: non-interactive mode with real model call."""
    result = _run_oh("-p", "Say exactly: hello openharness", "--model", os.environ.get("ANTHROPIC_MODEL", "kimi-k2.5"))
    output = result.stdout.strip().lower()
    if result.returncode != 0:
        return False, f"Exit code {result.returncode}: {result.stderr[:200]}"
    if "hello" in output:
        return True, f"Print mode output: {output[:100]}"
    return False, f"Expected 'hello' in output, got: {output[:200]}"


def test_print_json() -> tuple[bool, str]:
    """Test --output-format json with real model call."""
    result = _run_oh(
        "-p", "Respond with exactly: test123",
        "--output-format", "json",
        "--model", os.environ.get("ANTHROPIC_MODEL", "kimi-k2.5"),
    )
    if result.returncode != 0:
        return False, f"Exit code {result.returncode}: {result.stderr[:200]}"
    try:
        data = json.loads(result.stdout.strip())
        if data.get("type") == "result" and "test123" in data.get("text", "").lower():
            return True, f"JSON output parsed: {data['text'][:80]}"
        return False, f"Unexpected JSON content: {data}"
    except json.JSONDecodeError:
        return False, f"Invalid JSON: {result.stdout[:200]}"


def test_subcommand_mcp_list() -> tuple[bool, str]:
    """Test oh mcp list subcommand."""
    result = _run_oh("mcp", "list")
    output = result.stdout + result.stderr
    if result.returncode == 0:
        return True, f"mcp list output: {output.strip()[:100]}"
    return False, f"mcp list failed: {output[:200]}"


def test_subcommand_plugin_list() -> tuple[bool, str]:
    """Test oh plugin list subcommand."""
    result = _run_oh("plugin", "list")
    output = result.stdout + result.stderr
    if result.returncode == 0:
        return True, f"plugin list output: {output.strip()[:100]}"
    return False, f"plugin list failed: {output[:200]}"


def test_subcommand_auth_status() -> tuple[bool, str]:
    """Test oh auth status subcommand."""
    result = _run_oh("auth", "status")
    output = result.stdout + result.stderr
    if result.returncode == 0 and "provider" in output.lower():
        return True, f"auth status output: {output.strip()[:100]}"
    return False, f"auth status failed: {output[:200]}"


def main() -> None:
    tests = [
        ("help_output", test_help_output),
        ("print_mode", test_print_mode),
        ("print_json", test_print_json),
        ("mcp_list", test_subcommand_mcp_list),
        ("plugin_list", test_subcommand_plugin_list),
        ("auth_status", test_subcommand_auth_status),
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
