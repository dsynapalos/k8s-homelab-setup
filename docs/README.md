# Documentation Guide

This file is the index and style guide for everything in `docs/`. Use it to find the right document for a question, understand how the documentation is organized, and follow conventions when writing new pages.

> **Audience**: Humans learning the project _and_ AI agents navigating the codebase. Every section below is written to be useful to both.

---

## Folder Structure

```
docs/
├── README.md                        ← You are here (index & conventions)
├── getting-started.md               ← First-read onboarding guide
│
├── infrastructure/                  ← Cluster-level concerns (not apps)
│   ├── architecture.md              ← Project structure, entry points, role map
│   ├── configuration.md             ← .env reference, feature flags
│   ├── networking.md                ← Cilium CNI, Istio Ambient, ingress
│   ├── storage.md                   ← CephFS CSI & Rook-Ceph options
│   ├── gpu-support.md               ← NVIDIA PCI passthrough & drivers
│   └── troubleshooting.md           ← Cross-cutting debug guide (links to app docs)
│
├── cicd/                            ← Automation & deployment pipeline
│   ├── ansible-pipeline.md          ← Python entry points, Ansible Runner, playbooks
│   └── gitops.md                    ← ArgoCD, deploy keys, Application manifests
│
└── applications/                    ← Per-application docs (mirrors argocd_applications/)
    ├── monitoring/
    │   ├── prometheus.md            ← (deprecated) Replaced by otel-collector
    │   ├── grafana.md               ← Dashboards, datasource provisioning
    │   ├── thanos.md                ← Long-term storage, Receive, Query, Compactor
    │   ├── alertmanager.md          ← Alert routing & deduplication
    │   ├── matrix.md                ← Matrix Synapse homeserver, bootstrap job
    │   ├── alertmanager-matrix-bridge.md  ← Webhook → Matrix message translation
    │   ├── dcgm-exporter.md         ← NVIDIA GPU metrics exporter
    │   ├── kube-state-metrics.md    ← Kubernetes object state metrics
    │   ├── node-exporter.md         ← Host-level CPU/memory/disk metrics
    │   ├── otel-collector.md        ← Metrics pipeline (Prometheus receiver → Thanos)
    │   └── metrics-server.md        ← kubectl top / HPA metrics aggregator
    └── storage/
        ├── rook-operator.md         ← Rook operator lifecycle & CRDs
        └── rook-cluster.md          ← CephCluster CR, pools, StorageClasses
```

### Key principle: `applications/` mirrors `argocd_applications/`

The folder layout under `docs/applications/` matches the manifest directory `argocd_applications/` so you can map any deployed application to its documentation by following the same path:

| Manifest directory | Documentation |
|---|---|
| `argocd_applications/monitoring/prometheus/` | `docs/applications/monitoring/prometheus.md` |
| `argocd_applications/storage/rook-cluster/` | `docs/applications/storage/rook-cluster.md` |

---

## How to Find Information

### By question type

| I want to… | Start here |
|---|---|
| Set up the project from scratch | [getting-started.md](getting-started.md) |
| Understand how pieces fit together | [infrastructure/architecture.md](infrastructure/architecture.md) |
| Change a `.env` variable or enable a feature | [infrastructure/configuration.md](infrastructure/configuration.md) |
| Debug a failing component | [infrastructure/troubleshooting.md](infrastructure/troubleshooting.md) → links to per-app sections |
| Understand how Ansible runs | [cicd/ansible-pipeline.md](cicd/ansible-pipeline.md) |
| Understand how apps get deployed | [cicd/gitops.md](cicd/gitops.md) |
| Learn how a specific application works | `applications/<category>/<app>.md` |
| Add a new application to the cluster | [cicd/gitops.md](cicd/gitops.md) (manifest pattern), then create a matching app doc |

### By layer

```
┌─────────────────────────────────────┐
│  getting-started.md                 │  ← "How do I run this?"
├─────────────────────────────────────┤
│  cicd/                              │  ← "How does automation work?"
│    ansible-pipeline.md              │
│    gitops.md                        │
├─────────────────────────────────────┤
│  infrastructure/                    │  ← "How is the cluster built?"
│    architecture, configuration,     │
│    networking, storage, gpu-support │
├─────────────────────────────────────┤
│  applications/                      │  ← "What runs inside the cluster?"
│    monitoring/*, storage/*          │
├─────────────────────────────────────┤
│  infrastructure/troubleshooting.md  │  ← "Something broke"
└─────────────────────────────────────┘
```

Reading order for a newcomer: **getting-started → architecture → configuration → ansible-pipeline → gitops**, then dive into whichever infrastructure or application topic is relevant.

---

## Document Catalog

### Root

| Document | Purpose |
|---|---|
| [getting-started.md](getting-started.md) | First-read onboarding. Prerequisites, `init.sh`, `.env` setup, running the cluster, verifying it works. |

### Infrastructure (`infrastructure/`)

Platform-level concerns that exist regardless of which applications are deployed.

| Document | Purpose |
|---|---|
| [architecture.md](infrastructure/architecture.md) | High-level project map — entry points, Ansible roles, execution phases, data flow between components. |
| [configuration.md](infrastructure/configuration.md) | Reference for every `.env` variable. Organized by feature flag (`ENABLE_*`) with defaults and required values. |
| [networking.md](infrastructure/networking.md) | Cilium CNI (eBPF, WireGuard, L2 announcements, Gateway API), Istio Ambient (ztunnel, HBONE, mTLS), ingress routing. |
| [storage.md](infrastructure/storage.md) | Two storage paths: external CephFS CSI driver and in-cluster Rook-Ceph. Comparison, configuration, kernel module setup. |
| [gpu-support.md](infrastructure/gpu-support.md) | NVIDIA PCI passthrough from Proxmox, LTS driver selection logic, CRI-O runtime handler, RuntimeClass, device plugin. |
| [troubleshooting.md](infrastructure/troubleshooting.md) | Cross-cutting debug guide. Owns General Debugging, VM Provisioning, Kubernetes Cluster, and ArgoCD sections. Links to per-app troubleshooting sections for everything else. |

### CI/CD (`cicd/`)

How code and configuration get from your machine into the cluster.

| Document | Purpose |
|---|---|
| [ansible-pipeline.md](cicd/ansible-pipeline.md) | The three Python entry points (`setup-clusters.py`, `setup-applications.py`, `cleanup-clusters.py`), Ansible Runner mechanics, playbook structure, artifact debugging. |
| [gitops.md](cicd/gitops.md) | ArgoCD setup — SSH deploy keys, AppProject, Application manifests, sync waves, Kustomize integration. |

### Applications (`applications/`)

One doc per deployed application. Each follows the same internal structure (see conventions below).

#### Monitoring (`applications/monitoring/`)

| Document | Purpose |
|---|---|
| [prometheus.md](applications/monitoring/prometheus.md) | *(Deprecated)* Standalone Prometheus deployment. Replaced by [OTel Collector](applications/monitoring/otel-collector.md). Manifests kept for reference. |
| [grafana.md](applications/monitoring/grafana.md) | Dashboard visualization. Datasource provisioning (uid: prometheus), dashboard ConfigMaps, ingress. |
| [thanos.md](applications/monitoring/thanos.md) | Long-term metric storage and alerting. Receive, Query, Store, Compactor, Ruler. S3 via Rook-Ceph ObjectStore. Ruler evaluates alert rules and fires to Alertmanager. |
| [alertmanager.md](applications/monitoring/alertmanager.md) | Alert routing. Deduplication, grouping, webhook delivery to alertmanager-matrix-bridge. |
| [matrix.md](applications/monitoring/matrix.md) | Matrix Synapse homeserver. PostgreSQL sidecar, bootstrap job (bot user + Alerts room), Element client access. |
| [alertmanager-matrix-bridge.md](applications/monitoring/alertmanager-matrix-bridge.md) | Webhook translator. Init container config generation from `matrix-bot` Secret, emoji-formatted HTML messages. |
| [dcgm-exporter.md](applications/monitoring/dcgm-exporter.md) | NVIDIA GPU metrics. Requires RuntimeClass nvidia + GPU allocation. Metric deduplication via `max() by (gpu, Hostname)`. |
| [kube-state-metrics.md](applications/monitoring/kube-state-metrics.md) | Kubernetes object state metrics. Deployment/pod/node/job counts, conditions, resource requests. |
| [node-exporter.md](applications/monitoring/node-exporter.md) | Host metrics DaemonSet. CPU, memory, disk I/O, network, filesystem utilization from every node. |
| [otel-collector.md](applications/monitoring/otel-collector.md) | Metrics collection pipeline. Prometheus receiver, remote write to Thanos, expandable for traces/logs. |
| [metrics-server.md](applications/monitoring/metrics-server.md) | Kubernetes Metrics API aggregator. Enables `kubectl top`, HPA, and VPA via kubelet metric collection. |

#### Storage (`applications/storage/`)

| Document | Purpose |
|---|---|
| [rook-operator.md](applications/storage/rook-operator.md) | Rook operator deployment. CRDs, RBAC, CSI drivers, discovery daemon, operator ConfigMap patches. |
| [rook-cluster.md](applications/storage/rook-cluster.md) | CephCluster CR definition. MON/MGR/OSD/MDS/RGW components, block/filesystem/object pools, StorageClasses, health status. |

---

## Conventions

### Document structure (application docs)

Every application doc follows this skeleton:

```markdown
# Application Name

## What It Does
One-paragraph explanation of internal behavior, data flow, and purpose.

## Why It's Here
What problem this component solves in the stack.

## How It's Configured
Relevant `.env` variables, ConfigMaps, Secrets, Helm values.

## Integration Points
Table showing what this component connects to (direction, target, purpose).

## Troubleshooting
kubectl commands to check health, plus common failure modes and fixes.

## Links
Official documentation and upstream references.
```

Infrastructure docs are longer-form and may have additional sections (e.g., subsections for Cilium vs. Istio in networking.md, or CephFS vs. Rook in storage.md), but they follow the same general pattern: context first, configuration reference, troubleshooting at the end.

### Troubleshooting pattern

- Each application and infrastructure doc has its own `## Troubleshooting` section with component-specific diagnostics.
- [infrastructure/troubleshooting.md](infrastructure/troubleshooting.md) is the **cross-cutting entry point**. It owns topics that don't belong to a single application (General Debugging, VM Provisioning, Kubernetes Cluster, ArgoCD) and **links to** per-doc troubleshooting sections for everything else. Content is never duplicated between troubleshooting.md and the individual docs.

### Writing style

| Convention | Example |
|---|---|
| Present tense, active voice | "Prometheus scrapes targets every 15 seconds" |
| Concrete values over abstractions | "Port 9090", not "the configured port" |
| `kubectl` commands, not prose descriptions | Show how to verify, not just what to verify |
| No `ansible-*` CLI commands | The project uses Ansible Runner (Python library), not the ansible binary |
| Link to other docs instead of duplicating | `See [networking.md](infrastructure/networking.md#istio-ambient)` |
| Placeholders marked explicitly | *(Placeholder)* tag in catalog and in the doc itself |

### Naming

- **File names** match the component name in lowercase with hyphens: `alertmanager-matrix-bridge.md`, `rook-cluster.md`.
- **Folder names** match the ArgoCD application category: `monitoring/`, `storage/`.
- **Anchors** for cross-doc linking use the `## Section Name` heading converted to lowercase with hyphens: `#troubleshooting`, `#rook-ceph`, `#istio-ambient`.

### When to create a new doc

1. A new ArgoCD application is added → create `docs/applications/<category>/<app>.md` following the skeleton above.
2. A new infrastructure concern is added (e.g., a backup system) → create `docs/infrastructure/<topic>.md`.
3. A new CI/CD mechanism is added → create `docs/cicd/<mechanism>.md`.
4. Update this index (`docs.md`) with the new entry in the catalog table.

---

## For AI Agents

If you are an AI agent navigating this repository:

1. **Start here** (`docs/docs.md`) to understand what documentation exists and where.
2. **Use the catalog tables** above to locate the right file for a topic. The "Purpose" column tells you what each doc covers without opening it.
3. **Follow the folder convention**: `docs/applications/` mirrors `argocd_applications/`, so manifest locations map directly to doc locations.
4. **Troubleshooting lives in two places**: component-specific sections inside each doc, and the cross-cutting [troubleshooting.md](infrastructure/troubleshooting.md) for cluster-wide issues. Check the per-component doc first, fall back to troubleshooting.md.
5. **Configuration is centralized**: All environment variables are documented in [configuration.md](infrastructure/configuration.md). The `.env` file is the single source of truth at runtime; the doc is the reference for what each variable does.
6. **Deprecated docs** (`prometheus.md`) are marked with a banner and kept for reference.
7. **The project does not use `ansible` CLI** — only `ansible_runner.run()` from Python. Never suggest `ansible-playbook`, `ansible-inventory`, or `ansible -m ping` commands.
