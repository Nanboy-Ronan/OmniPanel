"""Contracts that keep the dashboard's styling actually applied.

dashboard.css targets Streamlit's internal ``data-testid`` attributes. Those are
not a public API: a Streamlit upgrade can rename one, and the matching rule then
stops applying with no error anywhere — the page just silently loses its design.
That is how the metric-card styling died (``metric-container`` → ``stMetric``),
leaving every one of the ~100 ``st.metric`` calls rendering as a bare default.

These tests fail loudly instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = Path("app/ui/static/dashboard.css")
PAGES_DIR = Path("app/ui/pages")


def _streamlit_bundle_sources() -> list[str]:
    """Return the contents of Streamlit's compiled frontend bundles."""
    import streamlit

    js_dir = Path(streamlit.__file__).parent / "static" / "static" / "js"
    if not js_dir.is_dir():
        pytest.skip(f"Streamlit frontend bundle not found at {js_dir}")
    sources = [p.read_text(encoding="utf-8", errors="ignore") for p in js_dir.glob("*.js")]
    if not sources:
        pytest.skip(f"No JS bundles under {js_dir}")
    return sources


def test_css_testids_still_exist_in_streamlit_bundle():
    """Every data-testid dashboard.css targets must be one Streamlit emits.

    Matched strictly against the literal ``"data-testid":"<id>"`` the compiled
    frontend renders, not a loose substring search — a bare ``"column"`` occurs
    79 times in the bundle for unrelated reasons while being dead as a testid.
    """
    css = CSS_PATH.read_text(encoding="utf-8")
    used = sorted(set(re.findall(r'data-testid="([^"]+)"', css)))
    assert used, "no data-testid selectors found — did the CSS move?"

    sources = _streamlit_bundle_sources()
    dead = [tid for tid in used if not any(f'"data-testid":"{tid}"' in s for s in sources)]

    assert not dead, (
        f"dashboard.css targets data-testid values Streamlit no longer emits: {dead}. "
        "These rules are silently dead — find the current testid in the frontend "
        "bundle and update the selector."
    )


def _registered_pages() -> list[str]:
    """Page labels wired into the navigation, from the registry itself."""
    from app.ui.registry import ADMIN_PAGES, ECOMMERCE_PAGES, MEDIA_PAGES

    return list({**ECOMMERCE_PAGES, **MEDIA_PAGES, **ADMIN_PAGES})


def test_every_registered_page_has_hero_metadata():
    """No page may fall back to the generic '▸' hero with a blank subtitle."""
    from app.ui._helpers import _PAGE_META

    pages = _registered_pages()
    assert len(pages) >= 20, f"page discovery looks broken, found only {pages}"

    missing = [p for p in pages if p not in _PAGE_META]
    assert not missing, (
        f"pages render the fallback hero icon with no subtitle: {missing}. "
        "Add an (icon, subtitle) entry to _PAGE_META."
    )

    blank = [p for p in pages if not _PAGE_META[p][1].strip()]
    assert not blank, f"pages have an empty hero subtitle: {blank}"


def test_page_meta_has_no_stale_entries():
    """_PAGE_META must not describe pages that no longer exist."""
    from app.ui._helpers import _PAGE_META

    stale = [p for p in _PAGE_META if p not in _registered_pages()]
    assert not stale, f"_PAGE_META describes unregistered pages: {stale}"


# Ratchet: colour literals belong in dashboard.css / a shared palette, not
# scattered through page modules. Lower this ceiling as pages are migrated;
# never raise it.
MAX_PAGE_HEX_LITERALS = 0


def test_page_modules_do_not_add_more_hex_colour_literals():
    counts = {
        path.name: len(re.findall(r"#[0-9a-fA-F]{6}\b", path.read_text(encoding="utf-8")))
        for path in sorted(PAGES_DIR.glob("*.py"))
    }
    total = sum(counts.values())
    assert total <= MAX_PAGE_HEX_LITERALS, (
        f"page modules now hold {total} hard-coded colours (ceiling "
        f"{MAX_PAGE_HEX_LITERALS}): {{k: v for k, v in counts.items() if v}}. "
        "Pull the colour into the shared palette instead of inlining it."
    )


def test_markdown_html_never_relies_on_inline_style_attributes():
    """Streamlit strips `style` from markdown HTML — the rule must be a class.

    Verified against the rendered DOM: a div emitted as
    `<div class='section-label' style='color:#38bdf8 !important;'>` arrives with
    the class intact and no style attribute at all, so every such rule was
    silently doing nothing. Styling has to live in dashboard.css.
    """
    offenders = []
    for path in [Path("app/ui/dashboard.py"), *sorted(PAGES_DIR.glob("*.py"))]:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""style\s*=\s*['"\\]""", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "inline style attributes are stripped by Streamlit; add a class to "
        "dashboard.css instead:\n" + "\n".join(offenders)
    )


def test_css_uses_the_radius_tokens_not_ad_hoc_pixel_values():
    """Nine different corner radii is most of what reads as template-stitched."""
    css = CSS_PATH.read_text(encoding="utf-8")
    body = css.split("/* ── Base ──", 1)[1]
    literals = sorted({int(px) for px in re.findall(r"border-radius: (\d+)px", body)})
    # 2px is the hero's underline rule, not a surface corner.
    assert literals in ([], [2]), (
        f"border-radius should come from var(--radius-sm|md|lg); found {literals}px"
    )


def test_no_webfont_is_requested():
    """The VM has no route to Google Fonts — a webfont silently falls back."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "@import" not in css
    assert "fonts.googleapis" not in css and "fonts.gstatic" not in css


def test_body_font_stack_names_cjk_faces():
    css = CSS_PATH.read_text(encoding="utf-8")
    stack = css.split('html, body, [class*="css"] {', 1)[1].split("}", 1)[0]
    assert "PingFang SC" in stack and "Microsoft YaHei" in stack, (
        "the UI is Chinese; leaving the CJK face to the browser default makes "
        "glyphs differ between machines"
    )


def test_theme_series_palette_is_the_validated_one():
    """Guards the palette the data-viz validator signed off on.

    The set it replaced failed CVD separation: #f43f5e next to #10b981 came out
    at deltaE 5.6 under deuteranopia while adjacent in series order.
    """
    from app.ui import theme

    assert theme.SERIES == (
        "#0284c7", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7",
    ), "re-run scripts/validate_palette.js before changing the series order"
    assert theme.PALETTE == {"old": theme.SERIES[0], "new": theme.SERIES[1]}
    assert len(set(theme.SERIES)) == len(theme.SERIES), "duplicate series slot"


def test_status_colours_are_not_reused_as_series():
    from app.ui import theme

    assert theme.NEGATIVE not in theme.SERIES
    assert theme.WARNING not in theme.SERIES


# Emoji render full-colour and at inconsistent metrics across platforms, which
# is why the dashboard carried three different icon systems at once. They are
# banned from chrome — titles, tabs, expanders, hero tiles, buttons — but left
# alone in generated narrative text, which is a product decision, not a style one.
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-⛿✀-➿⬀-⯿️]")

_CHROME_CALL = re.compile(
    r"st\.tabs\(|\.tabs\(|st\.expander\(|\.expander\(|st\.markdown\(\"#|_PAGE_META|"
    r"st\.subheader\(|st\.header\(|st\.title\(|page_hero\("
)

_SEMANTIC_ALLOWED = {"⚠", "️", "●", "○", "✕", "⇗", "▣", "✿", "♡"}


def test_no_emoji_in_ui_chrome():
    targets = [Path("app/ui/dashboard.py"), Path("app/ui/_helpers.py"), *sorted(PAGES_DIR.glob("*.py"))]
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        in_meta = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if "_PAGE_META" in line:
                in_meta = True
            elif in_meta and line.startswith("}"):
                in_meta = False
            if not (in_meta or _CHROME_CALL.search(line)):
                continue
            found = {c for c in _EMOJI.findall(line) if c not in _SEMANTIC_ALLOWED}
            if found:
                offenders.append(f"{path.name}:{lineno}: {''.join(sorted(found))} in {line.strip()[:70]}")
    assert not offenders, "emoji in UI chrome:\n" + "\n".join(offenders)


def test_page_hero_icons_are_one_system():
    """Hero tiles render a glyph on a gradient; a colour emoji clashes with it."""
    from app.ui._helpers import _PAGE_META

    bad = {
        page: icon for page, (icon, _) in _PAGE_META.items()
        if any(_EMOJI.match(c) and c not in _SEMANTIC_ALLOWED for c in icon)
    }
    assert not bad, f"hero icons must not be colour emoji: {bad}"
