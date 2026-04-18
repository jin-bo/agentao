---
name: matplotlib-charts
description: Produce PNG charts with matplotlib. Save to chart-<slug>.png in the working directory.
---

# Matplotlib Charts

## Format

- One chart per question. No subplots unless the user asks.
- `figsize=(10, 6)`; keep labels readable.
- Save as PNG with `plt.savefig(path, dpi=120, bbox_inches="tight")`.
- Do not call `plt.show()` — the environment is headless (`MPLBACKEND=Agg`).

## Style

- Default matplotlib style is fine; avoid exotic third-party themes.
- Set a clear title, x/y labels, and rotate x ticks if labels overlap.

## Return contract

After saving the chart, your FINAL assistant message must include **exactly one** line:

`[CHART] chart-<slug>.png`

The host UI parses this marker to render the image. Do not wrap it in code fences, backticks, or quotes.

## Example

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(10, 6))
ax.bar(products, revenues)
ax.set_title("Revenue by product")
ax.set_ylabel("Revenue ($)")
plt.savefig("chart-revenue.png", dpi=120, bbox_inches="tight")
```

Then end your reply with:

`[CHART] chart-revenue.png`
