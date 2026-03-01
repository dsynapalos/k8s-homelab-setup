#!/usr/bin/env python3
"""Export .drawio diagrams to self-contained SVG files.

Each SVG contains three representations of the diagram:
  1. Rendered SVG elements      — for GitHub/GitLab inline display
  2. Readable mxGraph XML       — in <metadata> for AI agent editing
  3. Base64-encoded content     — in the content= attribute for draw.io

After initial export, the .drawio source files can be removed.
The SVG becomes the single source of truth, editable by both
draw.io and AI agents, and renderable by GitHub.

Usage:
    python3 export-diagrams.py                            # Export .drawio → SVG
    python3 export-diagrams.py docs/diagrams/one.drawio   # Export single file
    python3 export-diagrams.py --rebuild                  # Rebuild SVGs from embedded XML
    python3 export-diagrams.py --rebuild one.svg           # Rebuild single SVG
    python3 export-diagrams.py --clean                    # Remove exported SVGs
"""

import argparse
import base64
import html
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

DIAGRAMS_DIR = Path(__file__).parent.parent.parent / "docs" / "diagrams"
PADDING = 20
ARROW_SIZE = 8
FONT_FAMILY = "Inter, Segoe UI, Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Geometry:
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


@dataclass
class Point:
    x: float = 0
    y: float = 0


@dataclass
class Cell:
    id: str = ""
    value: str = ""
    style: dict = field(default_factory=dict)
    geometry: Geometry = field(default_factory=Geometry)
    parent: str = "1"
    is_vertex: bool = False
    is_edge: bool = False
    source: str = ""
    target: str = ""
    waypoints: list = field(default_factory=list)
    exit_x: float | None = None
    exit_y: float | None = None
    entry_x: float | None = None
    entry_y: float | None = None
    # Resolved absolute position
    abs_x: float = 0
    abs_y: float = 0


# ---------------------------------------------------------------------------
# Style parsing
# ---------------------------------------------------------------------------

def parse_style(raw: str) -> dict:
    """Parse a draw.io style string into a dict."""
    style = {}
    if not raw:
        return style
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            style[key] = val
        else:
            style[part] = True
    return style


def decode_value(raw: str | None) -> str:
    """Decode draw.io value string (HTML entities + &#xa; newlines)."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = text.replace("\n", "\n")
    return text


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def parse_drawio_xml(xml_string: str, source: str = "<string>") -> tuple[list[Cell], float, float]:
    """Parse mxGraph XML from a string. Returns (cells, page_w, page_h)."""
    root = ET.fromstring(xml_string)

    # Handle both <mxfile><diagram><mxGraphModel> and bare <mxGraphModel>
    if root.tag == "mxfile":
        diagram = root.find("diagram")
        if diagram is None:
            raise ValueError(f"No <diagram> element in {source}")
        model = diagram.find("mxGraphModel")
    elif root.tag == "mxGraphModel":
        model = root
    else:
        raise ValueError(f"Unexpected root element <{root.tag}> in {source}")

    if model is None:
        raise ValueError(f"No <mxGraphModel> element in {source}")

    page_w = float(model.get("pageWidth", 1200))
    page_h = float(model.get("pageHeight", 800))

    cells = []
    root_elem = model.find("root")
    if root_elem is None:
        raise ValueError(f"No <root> element in {source}")

    for elem in root_elem.findall("mxCell"):
        cell = Cell(
            id=elem.get("id", ""),
            value=decode_value(elem.get("value")),
            style=parse_style(elem.get("style", "")),
            parent=elem.get("parent", "1"),
            is_vertex=elem.get("vertex") == "1",
            is_edge=elem.get("edge") == "1",
            source=elem.get("source", ""),
            target=elem.get("target", ""),
        )

        # Edge anchor points from style
        cell.exit_x = _float_or_none(cell.style.get("exitX"))
        cell.exit_y = _float_or_none(cell.style.get("exitY"))
        cell.entry_x = _float_or_none(cell.style.get("entryX"))
        cell.entry_y = _float_or_none(cell.style.get("entryY"))

        geo = elem.find("mxGeometry")
        if geo is not None:
            cell.geometry = Geometry(
                x=float(geo.get("x", 0)),
                y=float(geo.get("y", 0)),
                width=float(geo.get("width", 0)),
                height=float(geo.get("height", 0)),
            )
            # Parse waypoints
            array = geo.find("Array")
            if array is not None:
                for pt in array.findall("mxPoint"):
                    cell.waypoints.append(Point(
                        x=float(pt.get("x", 0)),
                        y=float(pt.get("y", 0)),
                    ))

        cells.append(cell)

    return cells, page_w, page_h


def parse_drawio(path: Path) -> tuple[list[Cell], float, float]:
    """Parse a .drawio file and return cells plus page dimensions."""
    xml_string = path.read_text(encoding="utf-8")
    return parse_drawio_xml(xml_string, source=str(path))


def _float_or_none(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Coordinate resolution
# ---------------------------------------------------------------------------

def resolve_absolute_coords(cells: list[Cell]) -> dict[str, Cell]:
    """Build cell lookup and resolve absolute x/y by walking parent chain."""
    lookup = {c.id: c for c in cells}

    def get_abs(cell: Cell) -> tuple[float, float]:
        x, y = cell.geometry.x, cell.geometry.y
        parent = lookup.get(cell.parent)
        if parent and parent.id not in ("0", "1"):
            px, py = get_abs(parent)
            # Swimlane children offset by startSize
            start_size = float(parent.style.get("startSize", 0))
            if "swimlane" in parent.style:
                pass  # startSize is just the header; child coords are relative to container origin
            x += px
            y += py
        return x, y

    for cell in cells:
        if cell.is_vertex:
            cell.abs_x, cell.abs_y = get_abs(cell)

    return lookup


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------

def compute_bounds(cells: list[Cell]) -> tuple[float, float, float, float]:
    """Compute bounding box of all vertices."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for c in cells:
        if not c.is_vertex:
            continue
        x1, y1 = c.abs_x, c.abs_y
        x2 = x1 + c.geometry.width
        y2 = y1 + c.geometry.height
        min_x = min(min_x, x1)
        min_y = min(min_y, y1)
        max_x = max(max_x, x2)
        max_y = max(max_y, y2)

    if min_x == float("inf"):
        return 0, 0, 800, 600

    return min_x, min_y, max_x, max_y


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def svg_escape(text: str) -> str:
    return html.escape(text, quote=True)


def render_text(
    x: float, y: float, width: float, height: float,
    text: str, style: dict, is_header: bool = False
) -> str:
    """Render multi-line text centered in a bounding box."""
    if not text.strip():
        return ""

    font_size = float(style.get("fontSize", 11))
    font_style = int(style.get("fontStyle", 0))
    font_color = style.get("fontColor", "#000000")
    bold = font_style & 1
    italic = font_style & 2

    lines = text.split("\n")
    line_height = font_size * 1.3
    total_height = line_height * len(lines)

    cx = x + width / 2
    start_y = y + (height - total_height) / 2 + font_size

    if is_header:
        start_y = y + font_size + 3

    parts = []
    for i, line in enumerate(lines):
        ly = start_y + i * line_height
        weight = "bold" if bold else "normal"
        fs = "italic" if italic else "normal"
        parts.append(
            f'<text x="{cx:.1f}" y="{ly:.1f}" '
            f'text-anchor="middle" dominant-baseline="auto" '
            f'font-family="{FONT_FAMILY}" font-size="{font_size:.0f}" '
            f'font-weight="{weight}" font-style="{fs}" '
            f'fill="{font_color}">'
            f'{svg_escape(line.strip())}</text>'
        )

    return "\n    ".join(parts)


def render_rect(cell: Cell, ox: float, oy: float) -> str:
    """Render a rounded rectangle vertex."""
    x = cell.abs_x - ox
    y = cell.abs_y - oy
    w = cell.geometry.width
    h = cell.geometry.height
    fill = cell.style.get("fillColor", "#ffffff")
    stroke = cell.style.get("strokeColor", "#000000")
    stroke_w = float(cell.style.get("strokeWidth", 1))
    rounded = "rounded" in cell.style
    rx = 6 if rounded else 0
    dashed = "dashed=1" in str(cell.style) or cell.style.get("dashed") == "1"

    dash_attr = ' stroke-dasharray="6 3"' if dashed else ""

    svg = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{rx}" ry="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w:.1f}"{dash_attr}/>'
    )

    text = render_text(x, y, w, h, cell.value, cell.style)
    if text:
        svg += f"\n    {text}"

    return svg


def render_swimlane(cell: Cell, ox: float, oy: float) -> str:
    """Render a swimlane container with a header bar."""
    x = cell.abs_x - ox
    y = cell.abs_y - oy
    w = cell.geometry.width
    h = cell.geometry.height
    fill = cell.style.get("fillColor", "#dae8fc")
    stroke = cell.style.get("strokeColor", "#6c8ebf")
    stroke_w = float(cell.style.get("strokeWidth", 1))
    start_size = float(cell.style.get("startSize", 25))
    rounded = "rounded" in cell.style
    rx = 6 if rounded else 0
    font_italic = cell.style.get("fontStyle") and int(cell.style.get("fontStyle", 0)) & 2

    # Outer container
    svg = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="{rx}" ry="{rx}" '
        f'fill="{fill}" fill-opacity="0.3" stroke="{stroke}" stroke-width="{stroke_w:.1f}"/>'
    )

    # Header bar
    svg += (
        f'\n    <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{start_size:.1f}" '
        f'rx="{rx}" ry="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w:.1f}"/>'
    )
    # Clip the bottom corners of the header
    svg += (
        f'\n    <rect x="{x:.1f}" y="{y + start_size - rx:.1f}" width="{w:.1f}" height="{rx:.1f}" '
        f'fill="{fill}" stroke="none"/>'
    )

    # Header text
    text = render_text(x, y, w, start_size, cell.value, cell.style, is_header=True)
    if text:
        svg += f"\n    {text}"

    return svg


def render_cylinder(cell: Cell, ox: float, oy: float) -> str:
    """Render a cylinder shape (data store)."""
    x = cell.abs_x - ox
    y = cell.abs_y - oy
    w = cell.geometry.width
    h = cell.geometry.height
    fill = cell.style.get("fillColor", "#e1d5e7")
    stroke = cell.style.get("strokeColor", "#9673a6")
    cap = 10  # ellipse height

    svg = (
        f'<path d="M {x:.1f} {y + cap:.1f} '
        f'Q {x:.1f} {y:.1f} {x + w / 2:.1f} {y:.1f} '
        f'Q {x + w:.1f} {y:.1f} {x + w:.1f} {y + cap:.1f} '
        f'L {x + w:.1f} {y + h - cap:.1f} '
        f'Q {x + w:.1f} {y + h:.1f} {x + w / 2:.1f} {y + h:.1f} '
        f'Q {x:.1f} {y + h:.1f} {x:.1f} {y + h - cap:.1f} Z" '
        f'fill="{fill}" stroke="{stroke}"/>'
    )
    # Top cap ellipse
    svg += (
        f'\n    <ellipse cx="{x + w / 2:.1f}" cy="{y + cap:.1f}" '
        f'rx="{w / 2:.1f}" ry="{cap:.1f}" '
        f'fill="{fill}" stroke="{stroke}"/>'
    )

    text = render_text(x, y + cap, w, h - cap, cell.value, cell.style)
    if text:
        svg += f"\n    {text}"

    return svg


def render_actor(cell: Cell, ox: float, oy: float) -> str:
    """Render a stick-figure actor shape."""
    x = cell.abs_x - ox
    y = cell.abs_y - oy
    w = cell.geometry.width
    h = cell.geometry.height
    fill = cell.style.get("fillColor", "#fff2cc")
    stroke = cell.style.get("strokeColor", "#d6b656")

    cx = x + w / 2
    head_r = min(w, h) * 0.15
    head_cy = y + head_r + 2
    body_top = head_cy + head_r + 2
    body_bot = y + h * 0.6
    arm_y = body_top + (body_bot - body_top) * 0.3
    leg_bot = y + h * 0.82

    svg = f'<circle cx="{cx:.1f}" cy="{head_cy:.1f}" r="{head_r:.1f}" fill="{fill}" stroke="{stroke}"/>'
    svg += f'\n    <line x1="{cx:.1f}" y1="{body_top:.1f}" x2="{cx:.1f}" y2="{body_bot:.1f}" stroke="{stroke}" stroke-width="1.5"/>'
    svg += f'\n    <line x1="{x + w * 0.2:.1f}" y1="{arm_y:.1f}" x2="{x + w * 0.8:.1f}" y2="{arm_y:.1f}" stroke="{stroke}" stroke-width="1.5"/>'
    svg += f'\n    <line x1="{cx:.1f}" y1="{body_bot:.1f}" x2="{x + w * 0.25:.1f}" y2="{leg_bot:.1f}" stroke="{stroke}" stroke-width="1.5"/>'
    svg += f'\n    <line x1="{cx:.1f}" y1="{body_bot:.1f}" x2="{x + w * 0.75:.1f}" y2="{leg_bot:.1f}" stroke="{stroke}" stroke-width="1.5"/>'

    text = render_text(x, y + h * 0.8, w, h * 0.2, cell.value, cell.style)
    if text:
        svg += f"\n    {text}"

    return svg


def get_anchor_point(
    cell: Cell, ox: float, oy: float,
    anchor_x: float | None, anchor_y: float | None,
    default_x: float = 0.5, default_y: float = 0.5
) -> Point:
    """Get a point on a cell's bounding box using fractional anchors."""
    ax = anchor_x if anchor_x is not None else default_x
    ay = anchor_y if anchor_y is not None else default_y
    x = cell.abs_x - ox + cell.geometry.width * ax
    y = cell.abs_y - oy + cell.geometry.height * ay
    return Point(x, y)


def _is_vertical(ax: float, ay: float) -> bool:
    """True if the anchor exits/enters from the top or bottom of a shape."""
    return ay in (0.0, 1.0) and ax not in (0.0, 1.0)


def _is_horizontal(ax: float, ay: float) -> bool:
    """True if the anchor exits/enters from the left or right of a shape."""
    return ax in (0.0, 1.0) and ay not in (0.0, 1.0)


def build_orthogonal_path(
    start: Point, end: Point,
    waypoints: list[Point], ox: float, oy: float,
    exit_x: float = 0.5, exit_y: float = 0.5,
    entry_x: float = 0.5, entry_y: float = 0.5,
) -> list[Point]:
    """Build an orthogonal (right-angle) path between two points.

    Determines routing direction from the exit/entry anchor fractions:
      - Vertical exit + vertical entry   → down, across, down   (U-shape)
      - Horizontal exit + horizontal entry → across, down, across (Z-shape)
      - Vertical exit + horizontal entry  → down, across          (L-shape)
      - Horizontal exit + vertical entry  → across, down          (L-shape)
    """
    if waypoints:
        # Waypoints are absolute coordinates in the drawio file
        points = [start]
        for wp in waypoints:
            points.append(Point(wp.x - ox, wp.y - oy))
        points.append(end)
        return points

    v_exit = _is_vertical(exit_x, exit_y)
    h_exit = _is_horizontal(exit_x, exit_y)
    v_entry = _is_vertical(entry_x, entry_y)
    h_entry = _is_horizontal(entry_x, entry_y)

    if v_exit and v_entry:
        # vertical → horizontal → vertical
        mid_y = (start.y + end.y) / 2
        return [start, Point(start.x, mid_y), Point(end.x, mid_y), end]

    if h_exit and h_entry:
        # horizontal → vertical → horizontal
        mid_x = (start.x + end.x) / 2
        return [start, Point(mid_x, start.y), Point(mid_x, end.y), end]

    if v_exit and h_entry:
        # L-shape: vertical first, then horizontal
        return [start, Point(start.x, end.y), end]

    if h_exit and v_entry:
        # L-shape: horizontal first, then vertical
        return [start, Point(end.x, start.y), end]

    # Fallback: determine from relative position
    if abs(end.x - start.x) > abs(end.y - start.y):
        # Primarily horizontal movement
        mid_x = (start.x + end.x) / 2
        return [start, Point(mid_x, start.y), Point(mid_x, end.y), end]
    else:
        # Primarily vertical movement
        mid_y = (start.y + end.y) / 2
        return [start, Point(start.x, mid_y), Point(end.x, mid_y), end]


def _compute_edge_path(
    edge_cell: Cell, lookup: dict[str, Cell], ox: float, oy: float,
) -> list[Point] | None:
    """Compute the orthogonal path points for an edge cell.

    Returns the list of SVG-offset points, or None if source/target are missing.
    This is shared by render_edge (drawing) and render_edge_label (positioning).
    """
    src_cell = lookup.get(edge_cell.source)
    tgt_cell = lookup.get(edge_cell.target)
    if not src_cell or not tgt_cell:
        return None
    if not src_cell.is_vertex or not tgt_cell.is_vertex:
        return None

    src_cx = src_cell.abs_x + src_cell.geometry.width / 2
    src_cy = src_cell.abs_y + src_cell.geometry.height / 2
    tgt_cx = tgt_cell.abs_x + tgt_cell.geometry.width / 2
    tgt_cy = tgt_cell.abs_y + tgt_cell.geometry.height / 2

    dx = tgt_cx - src_cx
    dy = tgt_cy - src_cy

    if edge_cell.exit_x is None and edge_cell.exit_y is None:
        if abs(dx) > abs(dy):
            def_exit_x, def_exit_y = (1.0, 0.5) if dx > 0 else (0.0, 0.5)
        else:
            def_exit_x, def_exit_y = (0.5, 1.0) if dy > 0 else (0.5, 0.0)
    else:
        def_exit_x = edge_cell.exit_x if edge_cell.exit_x is not None else 0.5
        def_exit_y = edge_cell.exit_y if edge_cell.exit_y is not None else 0.5

    if edge_cell.entry_x is None and edge_cell.entry_y is None:
        if abs(dx) > abs(dy):
            def_entry_x, def_entry_y = (0.0, 0.5) if dx > 0 else (1.0, 0.5)
        else:
            def_entry_x, def_entry_y = (0.5, 0.0) if dy > 0 else (0.5, 1.0)
    else:
        def_entry_x = edge_cell.entry_x if edge_cell.entry_x is not None else 0.5
        def_entry_y = edge_cell.entry_y if edge_cell.entry_y is not None else 0.5

    start = get_anchor_point(src_cell, ox, oy, def_exit_x, def_exit_y)
    end = get_anchor_point(tgt_cell, ox, oy, def_entry_x, def_entry_y)

    return build_orthogonal_path(
        start, end, edge_cell.waypoints, ox, oy,
        exit_x=def_exit_x, exit_y=def_exit_y,
        entry_x=def_entry_x, entry_y=def_entry_y,
    )


def _path_point_at(points: list[Point], t: float) -> Point:
    """Find the point at fractional distance *t* (0.0–1.0) along a polyline."""
    import math
    if not points:
        return Point(0, 0)
    if t <= 0 or len(points) == 1:
        return points[0]
    if t >= 1:
        return points[-1]

    total = 0.0
    seg_lengths: list[float] = []
    for i in range(1, len(points)):
        dx = points[i].x - points[i - 1].x
        dy = points[i].y - points[i - 1].y
        seg_lengths.append(math.sqrt(dx * dx + dy * dy))
        total += seg_lengths[-1]

    if total < 0.1:
        return points[0]

    target = t * total
    walked = 0.0
    for i, seg_len in enumerate(seg_lengths):
        if walked + seg_len >= target:
            frac = (target - walked) / seg_len if seg_len > 0.001 else 0
            return Point(
                points[i].x + (points[i + 1].x - points[i].x) * frac,
                points[i].y + (points[i + 1].y - points[i].y) * frac,
            )
        walked += seg_len

    return points[-1]


def render_edge(cell: Cell, lookup: dict[str, Cell], ox: float, oy: float) -> str:
    """Render an edge (connector) between two vertices."""
    points = _compute_edge_path(cell, lookup, ox, oy)
    if not points:
        return ""

    stroke = cell.style.get("strokeColor", "#000000")
    if stroke == "none" or not stroke:
        stroke = "#666666"
    stroke_w = float(cell.style.get("strokeWidth", 1.2))
    dashed = cell.style.get("dashed") == "1"
    dash_attr = ' stroke-dasharray="6 3"' if dashed else ""

    # Build polyline path
    d_parts = [f"M {points[0].x:.1f} {points[0].y:.1f}"]
    for pt in points[1:]:
        d_parts.append(f"L {pt.x:.1f} {pt.y:.1f}")
    path_d = " ".join(d_parts)

    svg = (
        f'<path d="{path_d}" fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_w:.1f}"{dash_attr}/>'
    )

    # Arrowhead at end
    if len(points) >= 2:
        p1, p2 = points[-2], points[-1]
        svg += _render_arrowhead(p1, p2, stroke)

    return svg


def _render_arrowhead(p1: Point, p2: Point, color: str) -> str:
    """Render a small triangle arrowhead at p2 pointing from p1."""
    import math
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    length = math.sqrt(dx * dx + dy * dy)
    if length < 0.1:
        return ""

    ux, uy = dx / length, dy / length
    # Perpendicular
    px, py = -uy, ux

    s = ARROW_SIZE
    tip = p2
    left = Point(p2.x - ux * s + px * s * 0.5, p2.y - uy * s + py * s * 0.5)
    right = Point(p2.x - ux * s - px * s * 0.5, p2.y - uy * s - py * s * 0.5)

    return (
        f'\n    <polygon points="{tip.x:.1f},{tip.y:.1f} '
        f'{left.x:.1f},{left.y:.1f} {right.x:.1f},{right.y:.1f}" '
        f'fill="{color}"/>'
    )


def render_edge_label(cell: Cell, lookup: dict[str, Cell], ox: float, oy: float) -> str:
    """Render a floating edge label along the actual edge path."""
    if not cell.value.strip():
        return ""

    # Edge labels whose parent is an edge: position along the rendered path
    parent_cell = lookup.get(cell.parent)
    if parent_cell and parent_cell.is_edge:
        points = _compute_edge_path(parent_cell, lookup, ox, oy)
        if points:
            # geometry.x (relative=1): position along edge
            #   -1 = source, 0 = midpoint, 1 = target
            rel_x = cell.geometry.x
            t = (1.0 + rel_x) / 2.0   # convert to 0-1 range
            t = max(0.0, min(1.0, t))

            pos = _path_point_at(points, t)
            mx = pos.x
            my = pos.y

            font_size = float(cell.style.get("fontSize", 9))
            font_color = cell.style.get("fontColor", "#000000")

            lines = cell.value.split("\n")
            parts = []
            for i, line in enumerate(lines):
                ly = my + i * font_size * 1.2
                parts.append(
                    f'<text x="{mx:.1f}" y="{ly:.1f}" '
                    f'text-anchor="middle" dominant-baseline="middle" '
                    f'font-family="{FONT_FAMILY}" font-size="{font_size:.0f}" '
                    f'fill="{font_color}">'
                    f'{svg_escape(line.strip())}</text>'
                )
            return "\n    ".join(parts)

    # Standalone label (no parent edge) — render at absolute position
    x = cell.abs_x - ox
    y = cell.abs_y - oy
    w = cell.geometry.width
    h = cell.geometry.height

    if "strokeColor" in cell.style and cell.style.get("strokeColor") == "none":
        text = render_text(x, y, w, h, cell.value, cell.style)
        return text

    return ""


# ---------------------------------------------------------------------------
# Main SVG generation
# ---------------------------------------------------------------------------

def generate_svg(
    cells: list[Cell], page_w: float, page_h: float,
    drawio_xml: str = "",
) -> str:
    """Generate a complete SVG document from parsed cells.

    If *drawio_xml* is provided, the original .drawio source is embedded
    as a ``content`` attribute on the root ``<svg>`` element so draw.io
    can re-open and edit the SVG in place.
    """
    lookup = resolve_absolute_coords(cells)

    min_x, min_y, max_x, max_y = compute_bounds(cells)
    ox = min_x - PADDING
    oy = min_y - PADDING
    svg_w = max_x - min_x + PADDING * 2
    svg_h = max_y - min_y + PADDING * 2

    # Encode the .drawio XML so draw.io can round-trip edit the SVG
    content_attr = ""
    metadata_block = ""
    if drawio_xml:
        encoded = base64.b64encode(drawio_xml.encode("utf-8")).decode("ascii")
        content_attr = f' content="{encoded}"'
        # Embed readable XML in <metadata> for AI agent editing.
        # CDATA avoids conflicts between mxGraph tags and SVG namespace.
        metadata_block = (
            f'  <metadata id="drawio-source">\n'
            f'  <![CDATA[\n'
            f'{drawio_xml}\n'
            f'  ]]>\n'
            f'  </metadata>'
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{svg_w:.0f}" height="{svg_h:.0f}" '
        f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}"'
        f'{content_attr}>',
    ]
    if metadata_block:
        parts.append(metadata_block)
    parts += [
        f'  <rect width="100%" height="100%" fill="#ffffff"/>',
        f'  <g>',
    ]

    # Render order: swimlanes first (background), then shapes, then edges, then labels
    swimlanes = []
    shapes = []
    edges = []
    labels = []
    floating_labels = []

    for cell in cells:
        if cell.id in ("0", "1"):
            continue

        if cell.is_vertex:
            shape = cell.style.get("shape", "")
            if "swimlane" in cell.style:
                swimlanes.append(cell)
            elif "edgeLabel" in cell.style:
                labels.append(cell)
            elif cell.style.get("strokeColor") == "none" and cell.style.get("fillColor", "").lower() == "none":
                floating_labels.append(cell)
            elif not cell.geometry.width and not cell.geometry.height:
                continue  # Skip zero-size elements
            elif shape == "cylinder3":
                shapes.append(("cylinder", cell))
            elif shape == "actor":
                shapes.append(("actor", cell))
            else:
                shapes.append(("rect", cell))
        elif cell.is_edge:
            edges.append(cell)

    # 1. Swimlanes (containers)
    for cell in swimlanes:
        parts.append(f"    {render_swimlane(cell, ox, oy)}")

    # 2. Shapes
    for shape_type, cell in shapes:
        if shape_type == "cylinder":
            parts.append(f"    {render_cylinder(cell, ox, oy)}")
        elif shape_type == "actor":
            parts.append(f"    {render_actor(cell, ox, oy)}")
        else:
            parts.append(f"    {render_rect(cell, ox, oy)}")

    # 3. Edges
    for cell in edges:
        rendered = render_edge(cell, lookup, ox, oy)
        if rendered:
            parts.append(f"    {rendered}")

    # 4. Edge labels
    for cell in labels:
        rendered = render_edge_label(cell, lookup, ox, oy)
        if rendered:
            parts.append(f"    {rendered}")

    # 5. Floating labels (annotations like "waits until platform Healthy")
    for cell in floating_labels:
        x = cell.abs_x - ox
        y = cell.abs_y - oy
        w = cell.geometry.width
        h = cell.geometry.height
        text = render_text(x, y, w, h, cell.value, cell.style)
        if text:
            parts.append(f"    {text}")

    parts.append("  </g>")
    parts.append("</svg>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# SVG ↔ drawio XML extraction
# ---------------------------------------------------------------------------

_CDATA_RE = re.compile(
    r'<metadata\s+id="drawio-source">\s*<!\[CDATA\[(.+?)\]\]>\s*</metadata>',
    re.DOTALL,
)


def extract_drawio_xml(svg_text: str) -> str:
    """Extract the readable mxGraph XML from an SVG's <metadata> CDATA."""
    m = _CDATA_RE.search(svg_text)
    if m:
        return m.group(1).strip()
    raise ValueError("No <metadata id='drawio-source'> CDATA block found in SVG")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def export_file(src: Path) -> Path:
    """Export a .drawio file to a self-contained SVG. Returns output path."""
    drawio_xml = src.read_text(encoding="utf-8")
    cells, page_w, page_h = parse_drawio_xml(drawio_xml, source=str(src))
    svg_content = generate_svg(cells, page_w, page_h, drawio_xml=drawio_xml)
    dest = src.with_suffix(".svg")
    dest.write_text(svg_content, encoding="utf-8")
    return dest


def rebuild_svg(svg_path: Path) -> Path:
    """Rebuild an SVG in place from its embedded mxGraph XML.

    Use after editing the <metadata> block with an AI agent.
    Re-renders all visual elements and updates the base64 content attribute.
    """
    svg_text = svg_path.read_text(encoding="utf-8")
    drawio_xml = extract_drawio_xml(svg_text)
    cells, page_w, page_h = parse_drawio_xml(drawio_xml, source=str(svg_path))
    svg_content = generate_svg(cells, page_w, page_h, drawio_xml=drawio_xml)
    svg_path.write_text(svg_content, encoding="utf-8")
    return svg_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export .drawio diagrams to self-contained SVG files",
    )
    parser.add_argument(
        "files", nargs="*", type=Path,
        help="Files to process (default: all in docs/diagrams/)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--rebuild", action="store_true",
        help="Rebuild SVGs from their embedded <metadata> XML (after AI edits)",
    )
    mode.add_argument(
        "--clean", action="store_true",
        help="Remove exported SVG files",
    )
    args = parser.parse_args()

    # --rebuild mode: re-render SVGs from embedded XML
    if args.rebuild:
        if args.files:
            svg_files = [f for f in args.files if f.suffix == ".svg"]
        else:
            if not DIAGRAMS_DIR.is_dir():
                print(f"Error: {DIAGRAMS_DIR} not found.")
                sys.exit(1)
            svg_files = sorted(DIAGRAMS_DIR.glob("*.svg"))

        if not svg_files:
            print("No SVG files found.")
            sys.exit(0)

        print(f"Rebuilding {len(svg_files)} SVG(s) from embedded XML...\n")
        success = failed = 0
        for svg in svg_files:
            try:
                rebuild_svg(svg)
                print(f"  {svg.name} rebuilt")
                success += 1
            except Exception as e:
                print(f"  {svg.name} -> ERROR: {e}")
                failed += 1
        print(f"\nDone: {success} rebuilt, {failed} failed.")
        sys.exit(1 if failed else 0)

    # --clean mode: remove SVG files
    if args.clean:
        if args.files:
            svg_files = [f.with_suffix(".svg") if f.suffix == ".drawio" else f for f in args.files]
        else:
            if not DIAGRAMS_DIR.is_dir():
                print(f"Error: {DIAGRAMS_DIR} not found.")
                sys.exit(1)
            svg_files = sorted(DIAGRAMS_DIR.glob("*.svg"))

        removed = 0
        for svg in svg_files:
            if svg.exists():
                svg.unlink()
                print(f"  Removed {svg.name}")
                removed += 1
        print(f"Cleaned {removed} file(s).")
        sys.exit(0)

    # Default mode: export .drawio → SVG
    if args.files:
        drawio_files = [f for f in args.files if f.suffix == ".drawio"]
    else:
        if not DIAGRAMS_DIR.is_dir():
            print(f"Error: {DIAGRAMS_DIR} not found.")
            sys.exit(1)
        drawio_files = sorted(DIAGRAMS_DIR.glob("*.drawio"))

    if not drawio_files:
        print("No .drawio files found.")
        sys.exit(0)

    print(f"Exporting {len(drawio_files)} diagram(s) to SVG...\n")
    success = failed = 0

    for src in drawio_files:
        try:
            dest = export_file(src)
            print(f"  {src.name} -> {dest.name}")
            success += 1
        except Exception as e:
            print(f"  {src.name} -> ERROR: {e}")
            failed += 1

    print(f"\nDone: {success} exported, {failed} failed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
