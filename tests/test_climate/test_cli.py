"""ClimWorkflow CLI：离线 demo 与磁盘 resume。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from openharness.climate.cli import app

runner = CliRunner()


def test_demo_and_resume_from_empty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    demo = runner.invoke(app, ["demo", "--workspace", str(workspace)])
    assert demo.exit_code == 0, demo.output
    assert "status=completed" in demo.output
    assert "report=" in demo.output
    report_line = next(line for line in demo.output.splitlines() if line.startswith("report="))
    report = Path(report_line.split("=", 1)[1])
    assert report.is_file()
    assert "](.climate/" in report.read_text(encoding="utf-8")

    resume = runner.invoke(app, ["resume", "--workspace", str(workspace)])
    assert resume.exit_code == 0, resume.output
    assert "ok=True" in resume.output
    assert "status=completed" in resume.output
    assert "active_run_id=" in resume.output
