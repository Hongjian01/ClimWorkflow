# 离线 Demo 产物

仓库根目录执行：

```powershell
uv run climworkflow demo --workspace climworkflow-demo
uv run climworkflow resume --workspace climworkflow-demo
```

本目录保存一次 `sample_pipeline` 的脱敏产物，便于 README 展示：

- `artifacts/plot.png` — 示例温度图
- `artifacts/report.md` — 相对路径报告
- `preview.png` — 产物与 `.climate/` 目录一览

运行时的完整工作区（含 `context.json`）不会提交；请本地用上面的命令生成。
