"""Stylesheet und Diagramm robust für ältere Browser (Safari < 16.2, Chrome < 111).

Kein color-mix(), neuere Selektoren nie in einer Selektorliste (ein unbekannter Selektor verwirft die
ganze Regel), feste Mischfarben passend zu den Grundfarben, SVG-Flächen mit fill-Attribut als Rückfall.
Das Verhalten im Browser prüft tests/e2e/old_browsers.js.
"""
import re
from pathlib import Path

import pytest

CSS = (Path(__file__).parent.parent / "tatilvakti" / "static" / "css" / "app.css").read_text(encoding="utf-8")
CODE = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)  # ohne Kommentare
# Selektoren, die ältere Browser nicht kennen: nie zusammen mit anderen in einer Liste
NEWER = (":has(", ":focus-visible", ":is(", ":where(")


def rules(css):
    """(Selektorliste, Deklarationen) aller Regeln, auch in @media."""
    out = []
    for m in re.finditer(r"([^{}@;]+)\{([^{}]*)\}", css):
        selector = " ".join(m.group(1).split())
        if selector and not selector.startswith(("from", "to")) and not re.match(r"^\d", selector):
            out.append((selector, m.group(2)))
    return out


def split_list(selector):
    """Selektorliste an Kommas der obersten Ebene trennen (nicht in Klammern)."""
    parts, depth, cur = [], 0, ""
    for ch in selector:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return parts + [cur.strip()]


def tokens(block):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def light_and_dark():
    light = re.search(r"^:root \{(.*?)^\}", CODE, flags=re.S | re.M).group(1)
    dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}", CODE, flags=re.S).group(1)
    return tokens(light), tokens(dark)


def rgb(value):
    value = value.strip()
    if value.startswith("#"):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)), 1.0
    m = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", value)
    assert m, value
    return tuple(int(m.group(i)) for i in (1, 2, 3)), float(m.group(4))


def test_no_color_mix():
    assert "color-mix(" not in CODE


def test_newer_selectors_never_share_a_rule():
    for selector, _ in rules(CODE):
        parts = split_list(selector)
        if len(parts) > 1:
            assert not any(n in selector for n in NEWER), selector


def test_selection_state_does_not_depend_on_has():
    # Auswahl und Fokus der Radio-Felder kommen über Geschwister-Selektoren, :has() höchstens als Zugabe
    selectors = [s for s, _ in rules(CODE)]
    assert ".seg__opt input:checked ~ span::after" in selectors
    assert ".bucket input:checked + span::after" in selectors
    assert ".seg__btn.is-active" in selectors
    assert any(".seg__opt input:focus ~ span::after" in split_list(s) for s in selectors)
    assert not any(":has(" in s and ("checked" in s or "is-active" in s) for s in selectors)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_mixed_tokens_match_their_base_colors(scheme):
    light, dark = light_and_dark()
    t = light if scheme == "light" else {**light, **dark}
    mixed = {k: v for k, v in (light if scheme == "light" else dark).items() if re.fullmatch(r"--[\w-]+-a\d\d", k)}
    assert {"--bg-a88", "--accent-a15", "--accent-a45", "--ok-a25", "--mid-a25", "--bad-a25", "--none-a25"} <= set(mixed)
    for name, value in mixed.items():
        base, pct = re.fullmatch(r"(--[\w-]+)-a(\d\d)", name).groups()
        color, alpha = rgb(value)
        assert (color, alpha) == (rgb(t[base])[0], int(pct) / 100), name
    # 45 % --brand + 55 % --line (deckend), auf ganze Kanäle gerundet
    brand, line = rgb(t["--brand"])[0], rgb(t["--line"])[0]
    want = tuple(.45 * b + .55 * c for b, c in zip(brand, line))
    got = rgb(t["--brand-line"])[0]
    assert all(abs(g - w) <= .51 for g, w in zip(got, want)), (got, want)


def test_dark_block_redefines_every_mixed_token():
    light, dark = light_and_dark()
    mixed = {k for k in light if re.fullmatch(r"--[\w-]+-a\d\d", k)} | {"--brand-line"}
    assert mixed <= set(dark)


def test_holiday_chart_shapes_carry_fill_attributes(client):
    # Ohne CSS bzw. ohne var() blieben SVG-Flächen sonst schwarz
    html = client.get("/de/ferien").get_data(as_text=True)
    periods = sorted(set(re.findall(r'href="/de/ferien\?zeitraum=([\w-]+)"', html)))
    assert len(periods) > 3
    seen = set()
    for pid in periods:
        page = client.get(f"/de/ferien?zeitraum={pid}").get_data(as_text=True)
        for tag in re.findall(r"<(?:rect|path) class=\"tl__(?:fill|all16|bayram|bar|rowbg)\"[^>]*>", page):
            kind = re.search(r'class="tl__(\w+)"', tag).group(1)
            seen.add(kind)
            assert re.search(r' fill="(#[0-9a-f]{6}|none)"', tag), tag
            if kind in ("fill", "all16", "bayram"):
                assert " fill-opacity=" in tag, tag
    assert seen == {"fill", "all16", "bayram", "bar", "rowbg"}
