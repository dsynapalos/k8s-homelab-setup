# AGENTS.md — Diagrams Directory

> Instructions for AI coding agents working with architecture diagrams in this repository.

## Overview

All architecture diagrams live in `docs/diagrams/` as **self-contained SVG files**. There are no standalone `.drawio` files. Each SVG embeds three layers:

1. **Rendered SVG elements** — displayed inline by GitHub/GitLab
2. **Readable mxGraph XML** — in `<metadata id="drawio-source"><![CDATA[...]]>` for AI editing
3. **Base64-encoded content** — in the `content` attribute on `<svg>` for draw.io round-trip editing

The SVG is the single source of truth.

## Skill File

Full technical rules for creating/editing diagrams (XML format, style guide, edge routing, waypoints, layout rules) are in the skill file:

**`.github/skills/render-drawio-diagram/SKILL.md`**

Always read that file before creating or editing any diagram. This document covers only the high-level workflow and directory-level context.

## Renderer

A pure-Python script at `library/scripts/export-diagrams.py` converts the embedded mxGraph XML into the visual SVG layer. No external tools (draw.io CLI, headless browser, etc.) are required.

```bash
python3 library/scripts/export-diagrams.py --rebuild                          # Rebuild all SVGs
python3 library/scripts/export-diagrams.py --rebuild docs/diagrams/one.svg    # Rebuild single SVG
python3 library/scripts/export-diagrams.py --clean                            # Remove all SVGs
```

**Always run `--rebuild` after editing the CDATA block.** The script updates both the rendered SVG elements and the base64 `content` attribute.

## Workflow

### Editing an existing diagram

1. Read the SVG file — locate the `<metadata id="drawio-source">` CDATA block.
2. Modify the mxGraph XML (add/remove/move cells, change edges, add waypoints).
3. Run `python3 library/scripts/export-diagrams.py --rebuild docs/diagrams/<name>.svg`.

### Creating a new diagram

1. Write valid mxGraph XML following the format and style rules in `SKILL.md`.
2. Create the SVG at `docs/diagrams/<name>.svg` using the skeleton template from `SKILL.md`.
3. Run `--rebuild` to render the visual layer.
4. Reference the new diagram from the relevant doc(s) — diagrams are linked via `![alt](../diagrams/<name>.svg)`.

### Fixing edge routing

1. Identify the edge cell in the CDATA XML by `id`, `source`, `target`.
2. Compute absolute positions (walk parent chain for nested elements).
3. Fix via anchor adjustments (`exitX`/`exitY`/`entryX`/`entryY`) or explicit waypoints.
4. Run `--rebuild`.

## Current Diagrams

| File | Content | Referenced from |
|------|---------|-----------------|
| `ansible-pipeline.svg` | Ansible pipeline phases, entry points, artifacts | `docs/cicd/ansible-pipeline.md` |
| `gitops-app-of-apps.svg` | ArgoCD app-of-apps hierarchy and sync waves | `docs/cicd/gitops.md` |
| `infrastructure-overview.svg` | Proxmox hosts, VMs, kube-vip, secondary storage | `docs/infrastructure/architecture.md` |
| `networking-architecture.svg` | Cilium CNI, Istio Ambient, ingress, services | `docs/infrastructure/networking.md` |
| `storage-architecture.svg` | Rook-Ceph components, CephFS CSI, StorageClasses, consumers | `docs/infrastructure/storage.md` |

## Key Rules

- **No standalone `.drawio` files** — SVGs are the source of truth.
- **Always rebuild after editing** — the rendered layer must match the CDATA XML.
- **Grid-aligned coordinates** — snap to multiples of 10.
- **Orthogonal edges only** — no diagonal connectors.
- **Consistent styles** — use the style guide in `SKILL.md` (color codes for containers, services, data stores, etc.).
- **Waypoint coordinates are absolute** in drawio coordinate space, not SVG-offset space.
- **File naming** — lowercase with hyphens, matching the topic (`storage-architecture.svg`, not `storage_arch.svg`).

## Dependencies

- Python 3 (standard library only — `xml.etree.ElementTree`, `base64`, `html`, `re`)
- No external packages or tools required
