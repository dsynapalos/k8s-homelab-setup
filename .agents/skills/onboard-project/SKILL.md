---
name: onboard-project
description: "Research and onboard an external project before implementing or deploying it. Use when: investigating, onboarding, deploying, integrating, or implementing a new tool, framework, library, Helm chart, Kubernetes operator, or open-source project. Fetches official docs and GitHub source, then reviews local codebase patterns before any design or code changes."
---

# Onboard Project

Research an external project thoroughly using online documentation and GitHub source code, then align with local codebase conventions before any implementation.

## When to Use

- Adding a new application, operator, or Helm chart to the cluster
- Integrating an external tool or library
- Investigating a project you haven't worked with before
- Deploying or configuring a third-party component
- Any task where you need to understand an external project first

## Procedure

### Phase 1 — External Research

Gather authoritative information about the project before touching any local files.

1. **Identify the project name** from the user's request (e.g., "Velero", "Loki", "Traefik").

2. **Fetch official documentation** — use `fetch_webpage` to retrieve content from:
   - `https://{project}.io/docs/` (or the project's known docs URL)
   - The project's main landing page for an overview
   - Installation/getting-started guides
   - Configuration reference pages relevant to the task

3. **Search the GitHub repository** — use `github_repo` to find:
   - README and installation instructions
   - Helm chart `values.yaml` (if deploying via Helm)
   - CRD definitions and example custom resources
   - Default configuration and important flags
   - Version compatibility matrices

4. **Summarize findings** — before proceeding, produce a brief internal summary:
   - What the project does and its core concepts
   - Installation method (Helm, manifests, operator, etc.)
   - Key configuration options relevant to the task
   - Version to use and compatibility constraints
   - Dependencies or prerequisites

### Phase 2 — Local Codebase Review

Study existing patterns and conventions in the workspace to ensure the new work fits.

1. **Read the project instructions** — load workspace instructions:
   - `AGENTS.md` (if not already in context)
   - `docs/README.md` for the documentation index

2. **Review analogous implementations** — find the most similar existing component:
   - Check `argocd_applications/` for a similar app's manifest structure
   - Check `roles/` for a similar role's task patterns
   - Check `sveltos_profiles/` for an existing ClusterProfile example
   - Check `docs/applications/` for the documentation template

3. **Identify patterns to follow** — extract from the analogous component:
   - Kustomize overlay structure (`kustomization.yaml`, patches, resources)
   - Taint tolerations (infra vs platform tier)
   - Namespace conventions
   - Helm value overrides vs raw manifests
   - Network retry patterns (`retries: 5`, `delay: 10-15`)
   - Environment variable lookups (`lookup("env", "VAR_NAME")`)
   - Sync wave assignment (if using app-of-apps)
   - `dependsOn` ordering (if using Sveltos)

### Phase 3 — Design

Present a plan before writing code.

1. **Propose the implementation** — outline:
   - Which tier the component belongs to (infra or platform)
   - Files to create or modify (manifests, roles, docs, profiles)
   - Configuration variables to add to `example.env`
   - Feature flag (if optional): `ENABLE_*` pattern
   - Dependencies on existing components

2. **Get user confirmation** before proceeding to implementation.

### Phase 4 — Implementation

Execute the plan following project conventions.

1. Create manifests, roles, profiles, and docs as agreed.
2. Follow all patterns identified in Phase 2.
3. Validate: check for errors, ensure idempotency, verify taint tolerations are present.

## Key Rules

- **Never skip Phase 1.** Even if you think you know the project, fetch current docs — APIs and defaults change between versions.
- **Never skip Phase 2.** Every new component must match existing codebase patterns exactly.
- **Fetch before you code.** No manifests, no roles, no config changes until Phases 1-2 are complete.
- **Version pinning.** If a new version variable is needed, add it to `example.env` — never hardcode.
- **Ask when uncertain.** If the external docs are ambiguous or the local pattern is unclear, ask the user rather than guessing.
