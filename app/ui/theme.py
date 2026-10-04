"""Single source of truth for chart colour in the dashboard.

Page modules must not hard-code hex. Before this existed, 86 colour literals
were spread across 10 page modules and every single-series bar chart picked a
different arbitrary hue, which is most of why the dashboard read as a set of
unrelated pages rather than one product.

Shell colour (background, text, primary) lives in `.streamlit/config.toml` and
`app/ui/static/dashboard.css`; this module covers what those cannot reach —
the colours Altair needs as Python values.

Palette provenance
------------------
SERIES was checked with the data-viz palette validator against the #ffffff
chart surface and passes every gate on the adjacent pairlist:

    Lightness band      all 6 inside L 0.43-0.77
    Chroma floor        all 6 >= 0.1
    CVD separation      worst adjacent #eda100 <-> #1baf7a dE 9.1 (protan)
    Normal-vision       worst adjacent #e87ba4 <-> #eda100 dE 19.6
    Contrast            #1baf7a / #eda100 / #e87ba4 below 3:1 — these three
                        require visible labels or a table view beside them

The palette it replaced FAILED: #f43f5e <-> #10b981 came out at dE 5.6 under
deuteranopia while sitting next to each other in series order, so a red-green
colourblind reader could not tell those two series apart.

Slot 1 is the brand sky already used by the app's primary button gradient.
"""
from __future__ import annotations

# Categorical series, assigned in this fixed order and never cycled. A 7th
# series folds into "其他" or becomes a separate chart — do not invent a hue.
SERIES: tuple[str, ...] = (
    "#0284c7",  # 1 sky (brand)
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#4a3aa7",  # 6 violet
)

#: Every single-series chart uses this, so one chart looks like the next.
PRIMARY = SERIES[0]
#: The second series in a two-way comparison.
SECONDARY = SERIES[1]

#: 老客户 / 新客户 split — slots 1 and 2, which clear contrast with no warning.
PALETTE = {"old": SERIES[0], "new": SERIES[1]}

# Non-data ink. Reference rules, de-emphasised context bars and gridlines are
# not series and must never take a series colour.
INK_MUTED = "#94a3b8"
INK_FAINT = "#cbd5e1"
INK_LABEL = "#475569"
INK_TITLE = "#334155"
INK_STRONG = "#1e293b"
GRID = "#e2e8f0"

# Status is reserved — never reused as "series N" — and fixed rather than
# themed. These are the data-viz reference steps, each distinct from every
# categorical slot above.
#
# Green-up / red-down matches st.metric's own delta colouring, so the two read
# the same way on one page. Red/green alone is unreadable under deuteranopia,
# so a status colour never carries the meaning by itself: the sign of the value
# is always on screen beside it.
POSITIVE = "#0ca30c"   # good
NEGATIVE = "#d03b3b"   # critical
WARNING = "#fab219"    # warning — sub-3:1 on white by design, always labelled

#: Single-hue ramp for ordinal magnitude (light -> dark), never a rainbow.
SEQUENTIAL = ("#e0f2fe", "#7dd3fc", "#0ea5e9", "#0369a1")

#: Chart label/title font stack. Latin first, then explicit CJK fallbacks —
#: the VM's users are on Chinese Windows and macOS, and leaving the CJK face to
#: the renderer's default is what produced mismatched glyphs between charts.
FONT = '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
