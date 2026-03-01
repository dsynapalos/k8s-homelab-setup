---
name: render-drawio-diagram
description: Create or edit draw.io architecture diagrams stored as self-contained SVGs. Use this when asked to create, render, edit, or fix a draw.io, mxGraph, SVG architecture diagram, or diagram edge routing.
---

# Render Draw.io Diagram

Diagrams in this project are **self-contained SVG files** in `docs/diagrams/`. Each SVG embeds three layers:

1. **Rendered SVG elements** — displayed inline by GitHub/GitLab
2. **Readable mxGraph XML** — in a `<metadata id="drawio-source"><![CDATA[...]]>` block for AI editing
3. **Base64-encoded content** — in the `content` attribute on `<svg>` for draw.io round-trip editing

A pure-Python renderer at `library/scripts/export-diagrams.py` converts the embedded XML into the visual SVG layer. The SVG is the single source of truth — there are no standalone `.drawio` files.

## Workflows

### Edit an existing diagram

1. Read the SVG file and locate the `<metadata id="drawio-source">` CDATA block.
2. Modify the mxGraph XML inside the CDATA block (add/remove/move cells, change edges, add waypoints).
3. Run `python3 library/scripts/export-diagrams.py --rebuild` to re-render the visual layer and update the base64 `content` attribute.

### Create a new diagram

1. Write valid mxGraph XML following the format and style rules below.
2. Create the SVG file at `docs/diagrams/<name>.svg` with this skeleton, placing the XML inside the CDATA block:

```xml
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="100" height="100" viewBox="0 0 100 100">
  <metadata id="drawio-source">
  <![CDATA[
<mxfile host="draw.io">
  <diagram name="Diagram Name" id="diagram-id">
    <mxGraphModel dx="1600" dy="1000" grid="1" gridSize="10" guides="1"
                  tooltips="1" connect="1" arrows="1" fold="1" page="1"
                  pageScale="1" pageWidth="1800" pageHeight="1100"
                  math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>

        <!-- vertices and edges here -->

      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
  ]]>
  </metadata>
</svg>
```

3. Run `python3 library/scripts/export-diagrams.py --rebuild docs/diagrams/<name>.svg` to render the visual layer. The script replaces the placeholder SVG with the fully rendered version.

### Fix edge routing

1. Read the metadata XML and identify the edge cell by its `id`, `source`, and `target`.
2. Compute absolute positions of the source and target (walk parent chain: sum parent x/y offsets for children inside containers).
3. Choose the fix:
   - **Anchors only** — change `exitX`/`exitY`/`entryX`/`entryY` in the edge style to control which side the edge exits/enters.
   - **Waypoints** — add `<Array as="points">` with `<mxPoint>` elements to force the edge through specific coordinates (use absolute coordinates in the drawio coordinate space, not SVG-offset space).
4. Run `--rebuild` to re-render.

## Script Reference

```bash
python3 library/scripts/export-diagrams.py --rebuild              # Rebuild all SVGs
python3 library/scripts/export-diagrams.py --rebuild one.svg      # Rebuild single SVG
python3 library/scripts/export-diagrams.py --clean                # Remove all SVGs
```

## Layout Rules

- Use **layered horizontal layout** (left-to-right flow).
- Use **orthogonal edge routing only** — no diagonal connectors.
- Maintain at least **40px vertical and horizontal spacing** between elements.
- Do **not** place connectors over containers.
- Keep all nodes **aligned to grid** (snap coordinates to multiples of 10).
- Keep containers **rectangular**.
- **Avoid edge crossings** whenever possible — reorder elements vertically to minimize crossings.
- **Position elements to prevent line crossings** — before placing an element, consider where its edges will route. If an element's connection would cross over another element, move the source or target so the line has a clear path.
- **Stack external elements vertically** aligned with their connection targets inside containers, so edges run horizontally without detours.
- **Leave routing gaps** — when two containers are stacked vertically, leave at least 50px between them for edges that need to pass through.
- Use **consistent styles** for same-type components (e.g., all databases share the same fill color and shape, all services share the same style).

## Structural Rules

- **Containers** represent clusters, namespaces, or logical groupings — use `swimlane` style with a title bar.
- **Services** are placed inside their parent container.
- **External systems** (users, third-party APIs, DNS) are placed **outside** all containers, on the left or top.
- **Data stores** (databases, object storage, queues) are placed on the **right** side of the diagram.
- **Entry points** (ingress, load balancers, clients) are placed on the **left** side of the diagram.

## Style Guide

| Component Type | Shape | Fill | Stroke | Font |
|---|---|---|---|---|
| Container / Cluster | `swimlane;startSize=25;rounded=1` | `#dae8fc` | `#6c8ebf` | bold 11px |
| Service / Pod | `rounded=1;whiteSpace=wrap;html=1` | `#d5e8d4` | `#82b366` | 10px |
| External System | Rectangle / `shape=actor` | `#fff2cc` | `#d6b656` | 10px |
| Data Store | `shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=10` | `#e1d5e7` | `#9673a6` | 10px |
| Entry Point / LB | `rounded=1` | `#f8cecc` | `#b85450` | bold 10px |
| Config / Capability label | `rounded=1` | `#f5f5f5` | `#666666` | 9px, `fontColor=#666666` |

## XML Format

All mxGraph XML must:

- Start with `<mxfile>` root element.
- Contain a single `<diagram>` with a `<mxGraphModel>`.
- Use `<mxCell>` elements for all nodes and edges.
- Set `parent="1"` for top-level elements, or the container cell ID for children.
- Include `mxGeometry` with explicit `x`, `y`, `width`, `height` for every vertex.
- Use `edgeStyle=orthogonalEdgeStyle` on all edges.
- Use `source` and `target` attributes on edge cells referencing vertex IDs.

## Edge Routing

### Base edge style

All edges must use this base style:

```
style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
```

### How the renderer routes edges

The renderer uses **direction-aware orthogonal routing** based on exit/entry anchor fractions. Four patterns are selected automatically:

| Exit anchor | Entry anchor | Route pattern | Example |
|---|---|---|---|
| Vertical (top/bottom) | Vertical (top/bottom) | down, across, down | Bottom of A to top of B |
| Horizontal (left/right) | Horizontal (left/right) | across, down, across | Right of A to left of B |
| Vertical | Horizontal | down, across (L-shape) | Bottom of A to left of B |
| Horizontal | Vertical | across, down (L-shape) | Right of A to top of B |

The midpoint for 3-segment routes is calculated as the average of start and end coordinates. **If that midpoint falls inside a container or crosses over elements, use waypoints instead.**

### Anchor points

Control which side an edge exits/enters by adding anchor fractions to the style. Values are fractions (0-1) of the shape's width/height:

| Anchor | Side |
|---|---|
| `exitX=0.5;exitY=0` | Top center |
| `exitX=0.5;exitY=1` | Bottom center |
| `exitX=0;exitY=0.5` | Left center |
| `exitX=1;exitY=0.5` | Right center |

Always include the corresponding `Dx=0;Dy=0` values. Example — exit right, enter left:

```
style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
```

### Edge Labels

To label an edge, add a child `mxCell` with `connectable="0"` and `parent` set to the edge ID:

```xml
<mxCell id="e1" edge="1" source="a" target="b" parent="1"
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
<mxCell id="e1_label" value="API Call" style="edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];fontSize=9;"
        vertex="1" connectable="0" parent="e1">
  <mxGeometry x="-0.2" relative="1" as="geometry"><mxPoint as="offset"/></mxGeometry>
</mxCell>
```

The renderer positions labels **along the actual rendered edge path** using the `geometry x` value with `relative="1"`:

| `x` value | Position on edge |
|---|---|
| `-1` | At the source end |
| `0` | Midpoint (default) |
| `1` | At the target end |
| `-0.2` | 40% from source (slightly before midpoint) |
| `0.3` | 65% from source (slightly past midpoint) |

The conversion formula is `t = (1 + x) / 2`, where `t` is the fraction (0–1) along the polyline path. Use negative values to shift labels toward the source, positive toward the target. The `mxPoint as="offset"` element can optionally shift the label by a fixed pixel amount.

### Waypoints

When the auto-calculated midpoint route would cross over containers or elements, use **explicit waypoints** via `<Array as="points">` inside the edge's `mxGeometry`:

```xml
<mxCell id="e1" edge="1" source="a" target="b" parent="1"
        style="edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0.5;entryY=1;entryDx=0;entryDy=0;">
  <mxGeometry relative="1" as="geometry">
    <Array as="points">
      <mxPoint x="455" y="672"/>
      <mxPoint x="455" y="595"/>
      <mxPoint x="680" y="595"/>
    </Array>
  </mxGeometry>
</mxCell>
```

**Waypoint coordinates are absolute** in the drawio coordinate space (not SVG-offset coordinates). When waypoints are present, the renderer connects start, waypoint 1, waypoint 2, ..., end with straight line segments — it does not apply auto-routing.

Use waypoints to:
- Route edges through the whitespace gap between stacked containers.
- Force a line to travel horizontally through open space before turning vertically.
- Prevent two edges from overlapping by offsetting their waypoint y-coordinates.

### Waypoint placement rules

1. **Compute absolute positions first.** A child element's absolute position = parent's top-left + child's local offset. For nested children (e.g., inside a swimlane inside another swimlane), walk the full parent chain.
2. **Place waypoints in whitespace.** Find the gap between container boundaries and route through its center. Example: if container A ends at y=560 and container B starts at y=610, the whitespace center is y=585.
3. **Never approximate.** Calculate from the geometry values in the XML. If element A's right edge is at absolute x=210 and element B's left edge is at absolute x=520, place the vertical leg at x=365 (midpoint of the gap).
4. **Avoid overlapping parallel edges.** If two edges share a horizontal or vertical segment, offset one by 10-20px so they don't draw on top of each other.

## Existing Diagrams

| File | Content |
|---|---|
| `docs/diagrams/ansible-pipeline.svg` | Ansible pipeline phases with entry points and artifacts |
| `docs/diagrams/gitops-app-of-apps.svg` | ArgoCD app-of-apps hierarchy and sync waves |
| `docs/diagrams/infrastructure-overview.svg` | Overall infrastructure architecture |
| `docs/diagrams/networking-architecture.svg` | Cilium CNI and network topology |
| `docs/diagrams/storage-architecture.svg` | Rook-Ceph and storage class setup |
| `docs/diagrams/service-interaction.svg` | Cross-service dependencies and data flow |

## Process

### Creating a new diagram

1. Parse the user's description. Identify all components, types (container, service, external, data store, entry point), and relationships.
2. Lay out elements following the structural and layout rules.
3. Write all edges. For each edge, compute the absolute positions of source and target and determine whether anchors alone suffice or waypoints are needed.
4. Create the SVG file with the XML inside the metadata CDATA block.
5. Run `python3 library/scripts/export-diagrams.py --rebuild docs/diagrams/<name>.svg`.

### Editing an existing diagram

1. Read the SVG file and extract the CDATA block.
2. Make the XML changes (move elements, add/remove edges, fix waypoints).
3. Run `python3 library/scripts/export-diagrams.py --rebuild`.
