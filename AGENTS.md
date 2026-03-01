# AGENTS.md — AI Coding Agent Instructions

> This file provides rules and context for any AI coding agent working in this repository.
> For the full documentation index, see [`docs/README.md`](docs/README.md).

## Project Overview

Homelab Kubernetes cluster automation — Python entry points drive Ansible Runner to provision VMs on Proxmox, initialize a kubeadm cluster with Cilium CNI, and deploy applications via ArgoCD GitOps. See [`README.md`](README.md) for the component summary and quick start.

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

---

## Entry Points

| Script | Playbook | Destructive? | Use case |
|--------|----------|-------------|----------|
| `setup-clusters.py` | `setup_cluster.yaml` (15 plays) | Yes | New clusters, infra changes, adding nodes |
| `setup-applications.py` | `setup_applications.yaml` (1 play) | No | App manifest updates |
| `cleanup-clusters.py` | `cleanup_cluster.yaml` | Yes | Full teardown |
| `expose-ca.py` | `expose_ca.yaml` (1 play) | No | Re-display CA trust scripts |

The playbook execution order (15 plays) is documented in [`docs/cicd/ansible-pipeline.md`](docs/cicd/ansible-pipeline.md).

---

## Configuration

**Single source of truth**: `.env` file (copy from `example.env`). All Ansible variables use `{{ lookup("env", "VAR_NAME") }}`.

- **No defaults in roles** — missing vars fail fast
- **Feature flags** (`ENABLE_ROOK`, `ENABLE_CEPH`, `ENABLE_CUDA`, `ENABLE_ISTIO`, `ENABLE_GATEWAY_API`) all default `false`
- **Pinned versions** (`K8S_VERSION`, `CRIO_VERSION`, `CILIUM_VERSION`, etc.) — never hardcode in roles
- **Multi-Proxmox**: `PROXMOX_API_HOST_1`/`_2`, `PROXMOX_NODE_1`/`_2` — each host in inventory maps to a cluster via `vm_provision.proxmox_cluster`
- **API server HA**: `K8S_VIP` + `KUBE_VIP_VERSION` for kube-vip floating VIP

Full variable reference: [`docs/infrastructure/configuration.md`](docs/infrastructure/configuration.md).

---

## Code Patterns

### Ansible tasks

- `kubernetes.core.k8s` with `state: present` — never shell out to `kubectl apply`
- `kubernetes.core.helm` for Helm charts
- `when:` clauses for optional features (not `is defined` checks)
- `creates:` parameter for idempotent file/command operations
- `delegate_to: localhost` for all K8s API calls
- `no_log: true` on any task that handles Proxmox passwords, join tokens, or certificate keys

### K8s resources

- Secrets: `data:` field with `b64encode` filter, not `stringData`
- ConfigMaps for public data, Secrets for private data
- Kubeconfig: fetched to `~/.kube/config` from `/etc/kubernetes/new_cluster_admin.conf`

### ArgoCD applications

- Kustomize manifests in `argocd_applications/{category}/{app}/`
- Application CRs in `argocd_applications/cluster-apps/platform/` or `services/`
- Three-tier hierarchy: parent → tiers (platform wave 1, services wave 4) → individual apps
- Sync waves enforce ordering within platform tier

### Environment variables

- `{{ lookup("env", "VAR_NAME") }}` — always
- Feature flags: `when: lookup('env', 'ENABLE_ROOK') == 'true'`
- No hardcoded defaults in roles

---

## Inventory Structure

Two files — described fully in [`docs/infrastructure/architecture.md`](docs/infrastructure/architecture.md#inventory-structure):

- **`inventory/k8s.yaml`**: Cluster nodes with host groups (`proxmox`, `k8s-control`, `k8s-nodes`), per-host `proxmox_cluster` field, node labels, `k8s_vip`/`kube_vip_version` group vars
- **`inventory/localhost.yaml`**: Control machine with `proxmox_cluster` dict map (one entry per Proxmox host), Ceph/GPU/ArgoCD credentials

---

## Naming Conventions

| Type | Convention | Examples |
|------|-----------|----------|
| Files | lowercase-hyphens | `matrix-bridge.md`, `rook-cluster.md` |
| Folders | ArgoCD category | `monitoring/`, `storage/` |
| Roles | snake_case | `bootstrap_cillium`, `setup_cluster_master` |
| Docs | same structure as `argocd_applications/` | `docs/applications/monitoring/thanos.md` |

---

## Idempotency Requirements

Every role must be safe to re-run. Key patterns:

- SSH keys: check ConfigMap existence before generating
- Deploy keys: query Git provider API before registering
- Node labels: declarative (add missing, remove stale, protect system namespaces)
- Packages: `apt: state=present`
- K8s resources: `kubernetes.core.k8s: state=present`
- kubeadm init: `creates: /etc/kubernetes/admin.conf`
- kube-vip: guarded by `admin_conf_stat` on primary control plane (`delegate_to: k8s-control-1`, `run_once: true`)

---

## Making Changes

| Change type | Steps |
|-------------|-------|
| New application | Create manifests in `argocd_applications/`, add Application CR to `cluster-apps/platform/` or `services/`, create doc in `docs/applications/`, update [`docs/README.md`](docs/README.md) catalog |
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

```bash
# Quick health checks
kubectl get nodes
cilium status
kubectl get applications -n argocd

# Last run output
cat artifacts/*/stdout | tail -50
cat artifacts/*/stderr
```

---

## Agent Skills

Skills live in `.github/skills/<skill-name>/SKILL.md`. Each skill has YAML frontmatter (`name`, `description`) and Markdown instructions. Copilot loads a skill automatically when the task matches its `description`.

| Skill | Description |
|-------|-------------|
| `render-drawio-diagram` | Creates or edits draw.io architecture diagrams stored as self-contained SVGs — includes workflows, edge routing rules, waypoint placement, and the pure-Python renderer |
