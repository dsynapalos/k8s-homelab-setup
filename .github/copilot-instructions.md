# Copilot Instructions — Kubernetes Cluster Automation

> **Read `docs/README.md` first.** It is the documentation index with a full catalog, navigation tables, and conventions. This file supplements it with agent-specific rules and quick-reference patterns.

**Trust these instructions.** Only search the codebase if the information here is incomplete or found to be in error.

## Project Identity

- **Purpose**: Homelab/learning project — single Proxmox host, single-replica components, relaxed security
- **Stack**: Python + Ansible Runner + kubeadm + CRI-O + Cilium + ArgoCD
- **Optional layers**: Istio Ambient, Rook-Ceph, CephFS CSI, NVIDIA GPU passthrough
- **Ansible is NOT used as a CLI** — only via `ansible_runner.run()` from Python. Never suggest `ansible-playbook`, `ansible-inventory`, or `ansible -m ping` commands.

## Entry Points

| Script | Playbook | Duration | Destructive? | When to use |
|--------|----------|----------|-------------|-------------|
| `setup-clusters.py` | `setup_cluster.yaml` (15 plays) | ~26 min | Yes (creates/destroys VMs) | New clusters, infra changes, adding nodes |
| `setup-applications.py` | `setup_applications.yaml` (1 play) | Seconds | No | App manifest changes, GitOps iteration |
| `cleanup-clusters.py` | `cleanup_cluster.yaml` | ~2 min | Yes (destroys everything) | Full teardown, start over |
| `expose-ca.py` | `expose_ca.yaml` (1 play) | Seconds | No | Re-display root CA trust scripts |

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

→ Detailed onboarding: `docs/getting-started.md`

## Configuration

**Single source of truth**: `.env` file (copy from `example.env`).

All Ansible variables use `{{ lookup("env", "VAR_NAME") }}`. No defaults in roles — missing vars fail fast.

**Feature flags** (all default `false`):
- `ENABLE_ROOK` — Rook-Ceph in-cluster storage
- `ENABLE_CEPH` — External CephFS CSI driver
- `ENABLE_CUDA` — NVIDIA GPU passthrough + drivers
- `ENABLE_ISTIO` — Istio Ambient service mesh
- `ENABLE_GATEWAY_API` — Cilium Gateway API mode (vs Ingress Controller)

**Pinned versions** (in `example.env`): `K8S_VERSION`, `CRIO_VERSION`, `CILIUM_VERSION`, `ISTIO_VERSION`, `CEPH_CSI_VERSION`, `ROOK_VERSION`. Never hardcode versions in roles — always read from `.env`.

→ Full variable reference: `docs/infrastructure/configuration.md`

## Playbook Execution Order (`setup_cluster.yaml`)

```
Play  1: localhost     → test_ansible_runner + setup_localhost
Play  2: proxmox       → provision_infra              (strategy: free)
Play  3: k8s-control   → setup_cluster_master         (includes setup_os)
Play  4: k8s-nodes     → setup_cluster_node           (includes setup_os)
Play  5: k8s-control   → setup_pki
Play  6: k8s (all)     → distribute_pki
Play  7: k8s (all)     → bootstrap_cillium
Play  8: k8s-control   → bootstrap_istio_ambient
Play  9: localhost      → bootstrap_nvidia_device_plugin
Play 10: localhost      → bootstrap_argocd
Play 11: localhost      → bootstrap_pki_secret
Play 12: localhost      → bootstrap_harbor_secret
Play 13: localhost      → bootstrap_cephfs_storage_class / bootstrap_rook_ceph
Play 14: localhost      → bootstrap_applications
Play 15: localhost      → display root CA trust instructions
```

Play 8 is conditional on `ENABLE_ISTIO`, Play 9 on `ENABLE_CUDA`, Play 13 on `ENABLE_CEPH`/`ENABLE_ROOK`. Plays 9-15 target `localhost` for K8s API calls via kubeconfig.

→ Full execution flow: `docs/cicd/ansible-pipeline.md`

## Repository Structure

```
├── setup-clusters.py / setup-applications.py / cleanup-clusters.py / expose-ca.py  ← Python entry points
├── setup_cluster.yaml / setup_applications.yaml / cleanup_cluster.yaml / expose_ca.yaml  ← Ansible playbooks
├── .env (from example.env)          ← All configuration (not committed)
├── inventory/                       ← Ansible inventories (k8s.yaml, localhost.yaml)
├── roles/                           ← Ansible roles (one per function)
├── argocd_applications/             ← Kustomize manifests deployed via ArgoCD
│   ├── cluster-apps/                ← App-of-app-of-apps hierarchy (platform + services)
│   ├── monitoring/                  ← Prometheus, Grafana, Thanos, Alertmanager, Matrix, etc.
│   ├── security/                    ← cert-manager, trust-manager, Keycloak, ArgoCD OIDC
│   ├── infrastructure/              ← Harbor container registry
│   └── storage/                     ← CloudNativePG, Rook operator + cluster
├── library/                         ← Custom Ansible modules
├── artifacts/                       ← Ansible Runner output (auto-cleaned each run)
└── docs/                            ← Project documentation (see below)
```

### Documentation map

```
docs/
├── README.md                ← START HERE — full index, catalog, conventions
├── getting-started.md       ← Onboarding: prerequisites, install, verify
├── infrastructure/
│   ├── architecture.md      ← Project structure, role map, data flow
│   ├── configuration.md     ← .env variable reference, feature flags
│   ├── networking.md        ← Cilium, Istio Ambient, ingress
│   ├── storage.md           ← CephFS CSI vs Rook-Ceph
│   ├── gpu-support.md       ← NVIDIA passthrough, drivers, RuntimeClass
│   ├── security.md          ← Vulnerability scanning, CVE reporting, image signing
│   └── troubleshooting.md   ← Debug entry point (links to per-app sections)
├── cicd/
│   ├── ansible-pipeline.md  ← Python entry points, playbook phases, artifacts
│   └── gitops.md            ← ArgoCD, app-of-apps, sync waves, deploy keys
└── applications/            ← One doc per component (mirrors argocd_applications/)
    ├── monitoring/           (prometheus, grafana, thanos, alertmanager,
    │                          matrix, matrix-bridge, otel-collector,
    │                          dcgm-exporter, node-exporter,
    │                          kube-state-metrics, metrics-server)
    ├── storage/              (cloudnative-pg, rook-operator, rook-cluster)
    ├── security/             (cert-manager, trust-manager, keycloak)
    └── infrastructure/       (harbor)
```

**Every application doc** follows the same structure: What It Does → Why It's Here → How It's Configured → Integration Points → Troubleshooting → Links.

`argocd_applications/` mirrors `docs/applications/` — same folder layout, so manifest paths map directly to doc paths.

## Agent Rules

### Information seeking

1. **Start with `docs/README.md`** to locate the right document for any topic.
2. **Check the per-component doc first** (e.g., `docs/applications/monitoring/thanos.md`) before reading role source code.
3. **Troubleshooting is distributed**: each doc has its own `## Troubleshooting` section. `docs/infrastructure/troubleshooting.md` is the cross-cutting entry point that links to all of them.
4. **Configuration questions** → `docs/infrastructure/configuration.md` (full `.env` reference).
5. **`argocd_applications/` mirrors `docs/applications/`** — same folder structure, so manifest paths map to doc paths.

### Code patterns to follow

**Ansible tasks**:
- Use `kubernetes.core.k8s` with `state: present` — never shell out to `kubectl apply`
- Use `kubernetes.core.helm` for Helm charts
- Register command outputs to conditionally skip tasks (idempotency)
- Use `when:` clauses for optional features — not `is defined` checks
- Use `include_tasks` with conditionals for optional task sets
- Use `creates:` parameter for file operations

**K8s operations**:
- All cluster API calls delegate to localhost: `delegate_to: localhost`
- Kubeconfig: fetched to `~/.kube/config` from `/etc/kubernetes/new_cluster_admin.conf`
- Secrets: use `data:` field with `b64encode` filter, not `stringData`
- ConfigMaps for public data (e.g., SSH public keys), Secrets with `no_log: true` for private data

**ArgoCD applications**:
- Kustomize-based manifests in `argocd_applications/{category}/{app}/`
- Each app needs: `kustomization.yaml`, workload definition, `service.yaml`
- Application CR manifests live in `argocd_applications/cluster-apps/platform/` or `argocd_applications/cluster-apps/services/`
- Three-tier app-of-app-of-apps: parent (`cluster-apps`) → tiers (`cluster-platform` wave 1, `cluster-services` wave 4) → individual apps
- Sync waves within the platform tier enforce ordering (1 → CRDs/operators, 2 → Harbor, 3 → Keycloak/OIDC)
- Service-tier apps deploy simultaneously after the entire platform tier is Healthy
- Application health check (Lua script in `argocd-cm`) required for sync waves to block on child apps

**Environment variables**:
- All config comes from `.env` via `{{ lookup("env", "VAR_NAME") }}`
- No hardcoded defaults in roles — fail-fast on missing vars
- Feature flags are string comparisons: `when: lookup('env', 'ENABLE_ROOK') == 'true'`

### Naming conventions

- **Files**: lowercase with hyphens (`matrix-bridge.md`, `rook-cluster.md`)
- **Folders**: match ArgoCD category (`monitoring/`, `storage/`)
- **Roles**: snake_case matching function (`bootstrap_cillium`, `setup_cluster_master`)
- **Labels**: inventory `labels:` dict propagates to K8s node labels

### Idempotency patterns

- **SSH keys**: Check ConfigMap existence before generating (`bootstrap_argocd`)
- **Deploy keys**: Query GitLab API for existing keys before registering
- **Node labels**: Declarative — applies inventory labels, removes unlabeled keys (excludes system namespaces)
- **Packages**: `apt: state=present` (installs only if missing)
- **Kernel modules**: `modprobe: state=present` + persist to `/etc/modules-load.d/`
- **K8s resources**: `kubernetes.core.k8s: state=present` (creates or updates)

### When making changes

- **New application**: Create manifests in `argocd_applications/`, add Application CR to `argocd_applications/cluster-apps/platform/` or `argocd_applications/cluster-apps/services/`, create doc in `docs/applications/`, update `docs/README.md` catalog
- **New node**: Update `.env` and `inventory/k8s.yaml`, run `setup-clusters.py`
- **New optional feature**: Add `ENABLE_*` flag to `example.env`, use conditionals in roles, document in `docs/infrastructure/configuration.md`
- **Config change**: Update `.env`, decide which entry point to run based on what changed
- **Always test idempotency**: running the same script twice must not break anything

### Debugging

Check `artifacts/` after any run:
- `artifacts/*/stdout` — full playbook output
- `artifacts/*/stderr` — error messages
- `artifacts/*/job_events/*.json` — per-task execution with timing

Common first checks:
```bash
# Environment loaded correctly?
grep -E "K8S_|PROXMOX_|ENABLE_" .env

# Cluster accessible?
kubectl get nodes

# Cilium healthy?
cilium status

# ArgoCD apps synced?
kubectl get applications -n argocd

# Recent run output?
cat artifacts/*/stdout | tail -50
```

→ Full troubleshooting guide: `docs/infrastructure/troubleshooting.md`