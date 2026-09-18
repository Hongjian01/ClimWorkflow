"""独立进程渲染 PNG，避免 TUI 线程里 import matplotlib/TkAgg 死锁。"""

from __future__ import annotations

import json
import os
import sys
import warnings
from io import BytesIO
from pathlib import Path

_HISTOGRAM_BINS = 10


def render_png(spec: dict[str, object]) -> bytes:
    os.environ["MPLBACKEND"] = "Agg"
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    chart_type = str(spec.get("chart_type") or "histogram")
    x_values = spec.get("x_values")
    y_values = [float(item) for item in (spec.get("y_values") or [])]
    x_name = spec.get("x_name")
    y_name = spec.get("y_name")
    title = spec.get("title") or ""
    labels = [str(item) for item in x_values] if isinstance(x_values, list) else None

    fig = Figure(figsize=(6.4, 4.8), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    try:
        if chart_type == "line":
            xs = list(range(len(y_values)))
            ax.plot(xs, y_values, color="#2563eb")
            if labels:
                step = max(len(xs) // 8, 1)
                ticks = xs[::step]
                ax.set_xticks(ticks)
                ax.set_xticklabels([labels[i] for i in ticks], rotation=45, ha="right")
        elif chart_type == "bar":
            bar_labels = labels or [str(index) for index in range(len(y_values))]
            ax.bar(bar_labels, y_values, color="#2563eb")
            if len(bar_labels) > 8:
                ax.tick_params(axis="x", labelrotation=45)
        else:
            ax.hist(y_values, bins=_HISTOGRAM_BINS, color="#2563eb")
        if title:
            ax.set_title(str(title))
        if isinstance(y_name, str) and y_name:
            ax.set_ylabel(y_name)
        if isinstance(x_name, str) and x_name and chart_type != "histogram":
            ax.set_xlabel(x_name)
        buf = BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fig.tight_layout()
            fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        fig.clear()


def main(argv: list[str] | None = None) -> int:
    os.environ["MPLBACKEND"] = "Agg"
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: plot_worker <spec.json> <out.png>", file=sys.stderr)
        return 2
    spec_path = Path(args[0])
    out_path = Path(args[1])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        return 1
    out_path.write_bytes(render_png(spec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
