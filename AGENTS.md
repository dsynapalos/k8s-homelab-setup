# AGENTS.md — AI Coding Agent Instructions

> This file provides rules and context for any AI coding agent working in this repository.
> For the full documentation index, see [`docs/README.md`](docs/README.md).

**Trust these instructions.** Only search the codebase if the information here is incomplete or found to be in error.

## Project Overview

Homelab Kubernetes cluster automation — Python entry points drive Ansible Runner to provision VMs on Proxmox, initialize a kubeadm cluster with Cilium CNI, and deploy applications via ArgoCD GitOps. See [`README.md`](README.md) for the component summary and quick start.

- **Purpose**: Homelab/learning project — single Proxmox host, single-replica components, relaxed security
- **Stack**: Python + Ansible Runner + kubeadm + CRI-O + Cilium + ArgoCD
- **Optional layers**: Istio Ambient, Rook-Ceph, CephFS CSI, NVIDIA GPU passthrough, Sveltos orchestration

**Key constraint**: Ansible is only invoked via `ansible_runner.run()` from Python. Never suggest `ansible-playbook`, `ansible-inventory`, or `ansible -m ping` commands.

---

## Setup & Validation

```bash
# 1. Bootstrap (one-time): Python venv + Ansible dependencies
sudo chmod +x init.sh && ./init.sh

# 2. Configure: copy and fill in all required values
cp example.env .env

# 3. Run full cluster provisioning (~26 min, destructive)
python3 setup-clusters.py

# 4. Run application deployment only (seconds, safe)
python3 setup-applications.py

# 5. Verify cluster health
kubectl get nodes
cilium status
kubectl get applications -n argocd
```

Always run `./init.sh` before first use. Always populate `.env` before running any Python entry point.

→ Detailed onboarding: [`docs/getting-started.md`](docs/getting-started.md)

---

## Documentation Navigation

**Start with [`docs/README.md`](docs/README.md)** — it is the full index with catalog tables, folder structure, and conventions. Use it to locate the right document for any topic before searching source code.

| Question | Document |
|----------|----------|
| How do I run this? | [`docs/getting-started.md`](docs/getting-started.md) |
| What runs where and in what order? | [`docs/infrastructure/architecture.md`](docs/infrastructure/architecture.md) |
| What does each `.env` variable do? | [`docs/infrastructure/configuration.md`](docs/infrastructure/configuration.md) |
| How does the Ansible pipeline work? | [`docs/cicd/ansible-pipeline.md`](docs/cicd/ansible-pipeline.md) |
| How are apps deployed via ArgoCD? | [`docs/cicd/gitops.md`](docs/cicd/gitops.md) |
| How does a specific app work? | `docs/applications/<category>/<app>.md` (mirrors `argocd_applications/`) |
| Something is broken | [`docs/infrastructure/troubleshooting.md`](docs/infrastructure/troubleshooting.md) → links to per-component sections |

Troubleshooting is distributed: each doc has its own `## Troubleshooting` section. The cross-cutting [`troubleshooting.md`](docs/infrastructure/troubleshooting.md) links to all of them. Never duplicate troubleshooting content.

**Lookup order**: Check the per-component doc first (e.g., `docs/applications/monitoring/thanos.md`) before reading role source code. For configuration questions, go to [`docs/infrastructure/configuration.md`](docs/infrastructure/configuration.md).

---

## Entry Points

| Script | Playbook | Duration | Destructive? | Use case |
|--------|----------|----------|-------------|----------|
| `setup-clusters.py` | `setup_cluster.yaml` (16 plays) | ~26 min | Yes | New clusters, infra changes, adding nodes |
| `setup-applications.py` | `setup_applications.yaml` (2 plays, 1 conditional) | Seconds | No | App manifest updates |
| `cleanup-clusters.py` | `cleanup_cluster.yaml` | ~2 min | Yes | Full teardown |
| `expose-ca.py` | `expose_ca.yaml` (1 play) | Seconds | No | Re-display CA trust scripts |

### Playbook Execution Order (`setup_cluster.yaml`)

```
Play  1: localhost     → test_ansible_runner + setup_localhost
Play  2: proxmox       → provision_infra              (strategy: free)
Play  3: k8s-control   → setup_cluster_master         (includes setup_os)
Play  4: k8s-nodes     → setup_cluster_node           (includes setup_os)
Play  5: k8s-control   → setup_pki
Play  6: k8s (all)     → distribute_pki
Play  7: k8s (all)     → bootstrap_cillium
Play  8: k8s-control   → bootstrap_istio_ambient      (ENABLE_ISTIO)
Play  9: localhost      → bootstrap_nvidia_device_plugin (ENABLE_CUDA)
Play 10: localhost      → bootstrap_argocd
Play 11: localhost      → bootstrap_sveltos            (ENABLE_SVELTOS)
Play 12: localhost      → bootstrap_pki_secret
Play 13: localhost      → bootstrap_harbor_secret
Play 14: localhost      → bootstrap_cephfs_storage_class / bootstrap_rook_ceph (ENABLE_CEPH / ENABLE_ROOK)
Play 15: localhost      → bootstrap_applications
Play 16: localhost      → display root CA trust instructions
```

Plays 9–16 target `localhost` for K8s API calls via kubeconfig. Full execution flow: [`docs/cicd/ansible-pipeline.md`](docs/cicd/ansible-pipeline.md).

---

## Configuration

**Single source of truth**: `.env` file (copy from `example.env`). All Ansible variables use `{{ lookup("env", "VAR_NAME") }}`.

- **No defaults in roles** — missing vars fail fast
- **Feature flags** (all default `false`):
  - `ENABLE_ROOK` — Rook-Ceph in-cluster storage
  - `ENABLE_CEPH` — External CephFS CSI driver
  - `ENABLE_CUDA` — NVIDIA GPU passthrough + drivers
  - `ENABLE_ISTIO` — Istio Ambient service mesh
  - `ENABLE_GATEWAY_API` — Cilium Gateway API mode (vs Ingress Controller)
  - `ENABLE_SVELTOS` — Sveltos orchestration layer (replaces app-of-apps hierarchy)
- **Pinned versions** (`K8S_VERSION`, `CRIO_VERSION`, `CILIUM_VERSION`, `ISTIO_VERSION`, `CEPH_CSI_VERSION`, `ROOK_VERSION`, `SVELTOS_VERSION`, `ARGOCD_TARGET_REVISION`, etc.) — never hardcode in roles
- **Multi-Proxmox**: `PROXMOX_API_HOST_1`/`_2`, `PROXMOX_NODE_1`/`_2` — each host in inventory maps to a cluster via `vm_provision.proxmox_cluster`
- **API server HA**: `K8S_VIP` + `KUBE_VIP_VERSION` for kube-vip floating VIP

Full variable reference: [`docs/infrastructure/configuration.md`](docs/infrastructure/configuration.md).

---

## Code Patterns

### Ansible tasks

- `kubernetes.core.k8s` with `state: present` — never shell out to `kubectl apply`
- `kubernetes.core.helm` for Helm charts
- Register command outputs to conditionally skip tasks (idempotency)
- `when:` clauses for optional features (not `is defined` checks)
- `include_tasks` with conditionals for optional task sets
- `creates:` parameter for idempotent file/command operations
- `delegate_to: localhost` for all K8s API calls
- `no_log: true` on any task that handles Proxmox passwords, join tokens, or certificate keys
- **Network resilience**: All network-dependent tasks (`apt`, `get_url`, `helm_repository`, `helm`, `kubernetes.core.k8s` with remote URLs) must include `retries: 5`, `delay: 10-15`, `register: result`, `until: result is success`

### K8s resources

- Secrets: `data:` field with `b64encode` filter, not `stringData`
- ConfigMaps for public data, Secrets for private data
- Kubeconfig: fetched to `~/.kube/config` from `/etc/kubernetes/new_cluster_admin.conf`

### ArgoCD applications

- Kustomize manifests in `argocd_applications/{category}/{app}/`
- Each app needs: `kustomization.yaml`, workload definition, service(s)
- Application CRs in `argocd_applications/cluster-apps/infra/` or `platform/`
- Three-tier hierarchy: parent → tiers (infra wave 1, platform wave 4) → individual apps
- Sveltos ClusterProfiles (optional, `ENABLE_SVELTOS=true`) replace the app-of-apps hierarchy with explicit `dependsOn` ordering
- Sync waves enforce ordering within the infra tier (1 → CRDs/operators, 2 → Harbor, 3 → Keycloak/OIDC)
- Platform-tier apps deploy simultaneously after the entire infra tier is Healthy
- Application health check (Lua script in `argocd-cm`) required for sync waves to block on child apps
- **Every pod spec must include a taint toleration** matching its target tier (`role=infra:NoSchedule` for infra apps, `role=platform:NoSchedule` for platform apps)
- DaemonSets that must run on all nodes (CNI, ztunnel, node-exporter, CSI nodeplugin) need `operator: Exists` tolerations for both `role` and `control-plane` taints
- Upstream apps without authored manifests get tolerations via Kustomize strategic-merge patches or Helm values

### Environment variables

- `{{ lookup("env", "VAR_NAME") }}` — always
- Feature flags: `when: lookup('env', 'ENABLE_ROOK') == 'true'`
- No hardcoded defaults in roles

---

## Repository Structure

```
├── setup-clusters.py / setup-applications.py / cleanup-clusters.py / expose-ca.py  ← Python entry points
├── setup_cluster.yaml / setup_applications.yaml / cleanup_cluster.yaml / expose_ca.yaml  ← Ansible playbooks
├── .env (from example.env)          ← All configuration (not committed)
├── inventory/                       ← Ansible inventories (k8s.yaml, localhost.yaml)
├── roles/                           ← Ansible roles (one per function)
├── argocd_applications/             ← Kustomize manifests deployed via ArgoCD
│   ├── cluster-apps/                ← App-of-app-of-apps hierarchy (infra + platform tiers)
│   ├── monitoring/                  ← Prometheus, Grafana, Thanos, Alertmanager, OTel, Loki, Jaeger, Matrix, etc.
│   ├── security/                    ← cert-manager, trust-manager, Keycloak, ArgoCD OIDC
│   ├── infrastructure/              ← Harbor container registry, Dragonfly P2P cache
│   └── storage/                     ← CloudNativePG, Rook operator + cluster
├── sveltos_profiles/                ← Sveltos ClusterProfile manifests
├── .agents/
│   └── skills/                      ← Agent skills (auto-loaded by description match)
│       ├── onboard-project/         ← Research external projects before implementing
│       └── render-drawio-diagram/   ← Creates/edits draw.io diagrams stored as self-contained SVGs
├── library/                         ← Custom Ansible modules + scripts
├── artifacts/                       ← Ansible Runner output (auto-cleaned each run)
└── docs/                            ← Project documentation (mirrors argocd_applications/)
```

`argocd_applications/` mirrors `docs/applications/` — same folder layout, so manifest paths map directly to doc paths.

---

## Inventory Structure

Two files — described fully in [`docs/infrastructure/architecture.md`](docs/infrastructure/architecture.md#inventory-structure):

- **`inventory/k8s.yaml`**: Cluster nodes with host groups (`proxmox`, `k8s-control`, `k8s-nodes`), per-host `proxmox_cluster` field, node labels, node taints (`role=infra:NoSchedule` or `role=platform:NoSchedule`), `k8s_vip`/`kube_vip_version` group vars
- **`inventory/localhost.yaml`**: Control machine with `proxmox_cluster` dict map (one entry per Proxmox host), Ceph/GPU/ArgoCD credentials

---

## Naming Conventions

| Type | Convention | Examples |
|------|-----------|----------|
| Files | lowercase-hyphens | `matrix-bridge.md`, `rook-cluster.md` |
| Folders | ArgoCD category | `monitoring/`, `storage/` |
| Roles | snake_case | `bootstrap_cillium`, `setup_cluster_master` |
| Docs | same structure as `argocd_applications/` | `docs/applications/monitoring/thanos.md` |
| Node labels | inventory `labels:` dict → K8s node labels | `node-role.kubernetes.io/role: platform` |
| Node taints | inventory `taints:` list → K8s node taints | `role=infra:NoSchedule` |

---

## Idempotency Requirements

Every role must be safe to re-run. Key patterns:

- SSH keys: check ConfigMap existence before generating
- Deploy keys: query Git provider API before registering
- Node labels: declarative (add missing, remove stale, protect system namespaces)
- Node taints: declarative (add missing, remove stale user-managed taints, protect system taints from `kubernetes.io/*`, `k8s.io/*`, `nvidia.com/*`)
- Packages: `apt: state=present`
- Kernel modules: `modprobe: state=present` + persist to `/etc/modules-load.d/`
- K8s resources: `kubernetes.core.k8s: state=present`
- kubeadm init: `creates: /etc/kubernetes/admin.conf`
- kube-vip: guarded by `admin_conf_stat` on primary control plane (`delegate_to: k8s-control-1`, `run_once: true`). Primary mounts `super-admin.conf` (K8s 1.29+ RBAC-free), secondaries mount `admin.conf`.

---

## Making Changes

| Change type | Steps |
|-------------|-------|
| New application | Create manifests in `argocd_applications/`, add Application CR to `cluster-apps/infra/` or `platform/`, add taint toleration matching the target tier (`role=infra` or `role=platform`), create Sveltos ClusterProfile in `sveltos_profiles/` (if using Sveltos), create doc in `docs/applications/`, update [`docs/README.md`](docs/README.md) catalog |
| New node | Update `.env` and `inventory/k8s.yaml`, run `setup-clusters.py` |
| New optional feature | Add `ENABLE_*` to `example.env`, conditionals in roles, document in [`docs/infrastructure/configuration.md`](docs/infrastructure/configuration.md) |
| Config change | Update `.env`, choose entry point based on scope |

Always test idempotency: running the same script twice must not break anything.

---

## Security Considerations

- **Never commit `.env`** — it contains Proxmox passwords, API tokens, and repository credentials
- **`no_log: true`** on any Ansible task that handles Proxmox passwords, join tokens, or certificate keys
- **Secrets use `data:` with `b64encode`**, not `stringData` — prevents accidental plaintext in manifests
- **ConfigMaps for public data**, Secrets for private data
- **TLS everywhere** — cert-manager issues certificates, trust-manager distributes CA bundles
- **No hardcoded credentials** — all sensitive values come from `.env` via environment lookups

---

## Debugging

Check `artifacts/` after any run — see [`docs/infrastructure/troubleshooting.md`](docs/infrastructure/troubleshooting.md) for the full guide including per-component diagnostics.

- `artifacts/*/stdout` — full playbook output
- `artifacts/*/stderr` — error messages
- `artifacts/*/job_events/*.json` — per-task execution with timing

```bash
# Quick health checks
kubectl get nodes
cilium status
kubectl get applications -n argocd

# Environment loaded correctly?
grep -E "K8S_|PROXMOX_|ENABLE_" .env

# Last run output
cat artifacts/*/stdout | tail -50
cat artifacts/*/stderr
```

---

## Agent Skills

Skills live in `.agents/skills/<skill-name>/SKILL.md`. Each skill has YAML frontmatter (`name`, `description`) and Markdown instructions. Copilot loads a skill automatically when the task matches its `description`.

| Skill | Description |
|-------|-------------|
| `onboard-project` | Research and onboard an external project before implementing — fetches official docs and GitHub source, then reviews local codebase patterns |
| `render-drawio-diagram` | Creates or edits draw.io architecture diagrams stored as self-contained SVGs — includes workflows, edge routing rules, waypoint placement, and the pure-Python renderer |

Diagram-specific agent instructions are also available in [`docs/diagrams/AGENTS.md`](docs/diagrams/AGENTS.md) — covers the editing workflow, renderer usage, current diagram inventory, and key rules.
