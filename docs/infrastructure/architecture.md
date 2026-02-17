# Architecture

## What This Document Covers

A high-level map of how the project is structured — the entry points, the Ansible roles, and how they connect. If you want to understand what runs where and in what order, start here. For the detailed per-phase breakdown, see [Ansible Pipeline](../cicd/ansible-pipeline.md).

## Two Execution Paths

The project has two independent entry points with very different risk profiles:

### Full Provisioning (`setup-clusters.py`)

Runs `setup_cluster.yaml` — the 15-play playbook that builds everything from bare metal to a running cluster with applications.

- **Duration**: ~26 minutes
- **What it touches**: Proxmox VMs, OS configuration, Kubernetes cluster, networking, storage, GitOps, applications
- **When to use**: New deployments, adding nodes, infrastructure changes, major version upgrades
- **Risk**: Destructive — creates and configures VMs from scratch

### Application Deployment (`setup-applications.py`)

Runs `setup_applications.yaml` — a single play that uploads ArgoCD Application manifests.

- **Duration**: Seconds
- **What it touches**: ArgoCD Application CRs only
- **When to use**: Adding applications, modifying manifests, day-to-day development
- **Risk**: Non-destructive — never touches infrastructure or cluster state

This separation exists so you can iterate on application configs without risking a 26-minute rebuild. There's also `cleanup-clusters.py` which reverses everything (destroys VMs, wipes storage, removes kubeconfig), and `expose-ca.py` which re-displays the root CA trust setup instructions for importing the homelab CA certificate into your browser.

## Role Map

The automation is organized into Ansible roles, each responsible for one concern. They execute in a specific order defined by the playbook.

### Infrastructure Layer

| Role | Runs On | Purpose |
|------|---------|---------|
| `test_ansible_runner` | localhost | Smoke test — confirms Ansible Runner is functional |
| `setup_localhost` | localhost | Installs CLI tools, remasters Ubuntu ISO, discovers secondary storage, provisions LVM thin pools |
| `provision_infra` | proxmox | Creates VMs from autoinstall ISO, configures users, applies static networking |
| `install_repo` | (included) | Utility role for adding APT repositories (used by setup_localhost, setup_os) |

**CLI tools installed by `setup_localhost`**:

| Tool | Version Strategy |
|------|-----------------|
| kubectl | Pinned to `K8S_VERSION` via APT |
| Helm | Latest from Helm APT repo |
| Cilium CLI | Always fetches latest stable from GitHub |
| Hubble CLI | Always fetches latest stable from GitHub |
| istioctl | Pinned to `ISTIO_VERSION` (only when `ENABLE_ISTIO=true`) |

> Cilium CLI and Hubble CLI are **not version-pinned** — they always pull the latest stable release. This is usually fine since they're client tools, but be aware if you need reproducible environments.

**What `provision_infra` actually does**: Downloads the Ubuntu Server ISO, remasters it with a cloud-init `autoinstall.yaml` embedded in the image, injects a custom GRUB menu entry (into both `grub.cfg` and `loopback.cfg` with a 1-second timeout for fast automated boot), recalculates MD5 checksums, and creates a hybrid BIOS+UEFI ISO using 7z (extraction) + xorriso (rebuild with GPT partition GUIDs from the `[BOOT]` directory). The ISO is uploaded to Proxmox storage with an idempotency check (queries the API first, skips upload if already present). VM creation uses `create_vm.py` which checks for duplicate VMs by name — if a VM with the same name already exists, the script exits successfully without creating anything (making re-runs safe). The script enables QEMU Guest Agent, sets balloon memory to 0, uses `virtio-scsi-single` SCSI controller, and boots with `order=scsi0;ide2`. After boot, it configures hostname, creates the SSH user with passwordless sudo, deploys authorized keys, applies a static IP via a netplan template, kills the default `ubuntu` user sessions and processes, removes it, and disables password authentication in sshd. VMs are staggered during creation to avoid Proxmox API contention.

### Kubernetes Layer

| Role | Runs On | Purpose |
|------|---------|---------|
| `setup_os` | k8s nodes | Disables swap, installs CRI-O + kubeadm, configures firewall |
| `setup_cluster_master` | k8s-control | Runs kubeadm init, fetches kubeconfig to localhost, applies node labels |
| `setup_cluster_node` | k8s-nodes | Joins workers to cluster, applies and enforces declarative node labels |

**Kubeconfig handling**: `kubeadm init` generates `/etc/kubernetes/admin.conf` on the control plane. The role fetches this file to localhost as `new_cluster_admin.conf`, then copies it to `~/.kube/config` (controlled by the `OVERWRITE_KUBECONFIG` variable, which defaults to `true`). Other roles reference the kubeconfig at its localhost path (`/etc/kubernetes/new_cluster_admin.conf`) for Kubernetes API operations.

**Declarative node labels**: Both master and worker roles apply labels from inventory definitions and remove labels that are no longer declared — enforcing desired state on every run. Labels protected from removal differ by role:
- **Control plane**: `kubernetes.io/*` and `k8s.io/*` namespaces are protected
- **Workers**: `kubernetes.io/*`, `k8s.io/*`, `nvidia.com/*`, `accelerator`, and `gpu-type` are all protected (GPU labels are managed by the device plugin, not inventory)

Labels are only updated when a key is missing or its value differs from what's in inventory.

### PKI Layer

| Role | Runs On | Purpose |
|------|---------|---------|
| `setup_pki` | k8s-control | Generates Root CA (ECC secp384r1, 10-year) and Intermediate CA (5-year, pathlen:0), fetches certs to controller |
| `distribute_pki` | k8s (all) | Installs root CA in system trust store, configures CRI-O registry mirrors for Harbor proxy cache |
| `bootstrap_pki_secret` | localhost | Pre-creates the `homelab-ca-secret` Secret in `cert-manager` namespace (intermediate cert+key, root CA) |
| `bootstrap_harbor_secret` | localhost | Pre-creates the `harbor-admin-password` Secret with a random 32-char password (idempotent) |

**PKI chain**: Root CA → Intermediate CA → cert-manager leaf certificates. The root CA private key never leaves the control plane node. Only the certificates and the intermediate key are fetched to the Ansible controller for distribution. CRI-O mirrors route image pulls through Harbor's proxy cache (`harbor.k8s.local/{registry}-cache`).

### Networking Layer

| Role | Runs On | Purpose |
|------|---------|---------|
| `bootstrap_cillium` | k8s (all) | Deploys Cilium CNI with eBPF, WireGuard, L2 announcements, CoreDNS rewrite |
| `bootstrap_istio_ambient` | k8s-control | Deploys Istio Ambient mesh with ztunnel (optional) |
**Post-install actions**: After Cilium is deployed, the role restarts CRI-O on each node for proper CNI integration, then finds and deletes all non-hostNetwork pods across all namespaces so they restart with Cilium networking applied. For Istio, there's a 10-second pause after CRD installation, then each component waits for rollout (300s timeout) before proceeding.
### Platform Layer

| Role | Runs On | Purpose |
|------|---------|---------|
| `bootstrap_argocd` | localhost | Installs ArgoCD, manages SSH deploy keys, creates AppProject |
| `bootstrap_nvidia_device_plugin` | localhost | Creates RuntimeClass, deploys GPU device plugin (optional) |
| `bootstrap_cephfs_storage_class` | localhost | Deploys CephFS CSI driver for external Ceph (optional) |
| `bootstrap_rook_ceph` | localhost | Deploys Rook-Ceph operator and cluster via ArgoCD (optional) |
| `bootstrap_applications` | localhost | Uploads the app-of-apps parent manifest (ArgoCD cascades to all apps) |
| `cleanup_cluster` | localhost | Destroys VMs, removes storage pools, wipes disks |

### How `setup_os` Fits In

The `setup_os` role is never called directly from the playbook. Instead, both `setup_cluster_master` and `setup_cluster_node` include it at the start of their execution. This means OS preparation runs on every cluster node, but always as part of the master or worker setup flow.

The role also has two optional sub-task files that run conditionally:
- `configure_ceph_kernel.yaml` — loads the Ceph kernel module (when `ENABLE_CEPH=true`)
- `configure_cuda.yaml` — installs NVIDIA drivers and Container Toolkit (when node has `compute: cuda` label)

## Execution Order

The main playbook runs these 15 plays in sequence. Each play targets a specific host group:

```
 1. localhost        →  test_ansible_runner + setup_localhost
 2. proxmox          →  provision_infra
 3. k8s-control      →  setup_cluster_master (includes setup_os)
 4. k8s-nodes        →  setup_cluster_node (includes setup_os)
 5. k8s-control      →  setup_pki
 6. k8s (all nodes)  →  distribute_pki
 7. k8s (all nodes)  →  bootstrap_cillium
 8. k8s-control      →  bootstrap_istio_ambient
 9. localhost         →  bootstrap_nvidia_device_plugin
10. localhost         →  bootstrap_argocd
11. localhost         →  bootstrap_pki_secret
12. localhost         →  bootstrap_harbor_secret
13. localhost         →  bootstrap_cephfs_storage_class / bootstrap_rook_ceph
14. localhost         →  bootstrap_applications
15. localhost         →  display root CA trust instructions
```

Optional roles (Istio, CUDA, CephFS, Rook) are gated by environment variables and skip cleanly when disabled.

## Inventory Structure

Two inventory files target different host groups:

**`inventory/k8s.yaml`** — defines cluster nodes:
- Host groups: `proxmox`, `k8s-control`, `k8s-nodes` (all inherit from parent group `k8s`)
- Every variable comes from `.env` via `{{ lookup("env", "VAR_NAME") }}`
- Per-host node labels (e.g., `compute: cuda` for GPU workers)
- **Label aggregation**: Since Ansible's default `hash_behaviour=replace` doesn't merge dictionaries, the `provision_infra` role includes `aggregate_labels.yaml` which reads the raw inventory YAML and merges labels from all group levels. A host can inherit `infra: proxmox` from its group and `compute: cuda` from its host-level definition.
- **`bare-metal` group**: Exists as an empty placeholder (`hosts: {}`) with `labels.infra: baremetal` for future bare-metal node support

**`inventory/localhost.yaml`** — defines the control machine:
- Used for all Kubernetes API operations (ArgoCD, storage, GPU plugin)
- Points to the venv Python interpreter: `{{ playbook_dir }}/.venv/bin/python`
- Contains Ceph, GPU, and ArgoCD credentials
- Note: `ENABLE_ROOK` is **not** in the inventory — it's read directly from the environment via `lookup('env', 'ENABLE_ROOK')` in the playbook, unlike other feature flags

## Key Design Decisions

**Everything from `.env`**: No defaults live in roles. Every configurable value comes from environment variables, loaded by `python-dotenv` in the entry scripts. This means a missing variable fails fast rather than silently using a wrong default.

**Delegation pattern**: Kubernetes API operations (creating resources, Helm installs) always run on `localhost` using `delegate_to: localhost` with the fetched kubeconfig. The cluster nodes never need kubectl installed.

**Idempotency everywhere**: Every role is safe to re-run. ConfigMaps are checked before key generation, `kubeadm init` uses `creates:` guards, labels are diffed before applying, and APT packages use `state: present`.

**Label-driven behavior**: GPU passthrough, CUDA drivers, and device plugin targeting all key off the `compute: cuda` label in inventory. Add the label to a node and the entire GPU stack activates for it.

## Troubleshooting

```bash
# Verify .env variables are set (catches missing values before a run)
grep -E '^[A-Z]' .env | head -20
python3 -c "from dotenv import dotenv_values; v=dotenv_values('.env'); [print(f'EMPTY: {k}') for k,v in v.items() if not v]"

# Verify role file structure
find roles/ -name main.yaml -type f | sort

# Check last run artifacts (start here after a failure)
ls -la artifacts/*/
cat artifacts/*/stdout | tail -100
cat artifacts/*/stderr
```

**Role not executing**: Check the `when:` condition on the play in `setup_cluster.yaml`. Optional roles (Istio, CUDA, CephFS, Rook) are gated by environment variable checks — ensure the corresponding `ENABLE_*` variable is set to `true` in `.env`.

**Wrong execution order**: Roles run in the order listed in the playbook, not alphabetically. If a role depends on another, check `setup_cluster.yaml` to verify the play ordering.

**"Variable undefined" errors**: Every variable comes from `.env` via `lookup("env", "VAR_NAME")`. If a variable is missing, Ansible fails at template time. Cross-check with `example.env` for the complete list.

---

## File Organization

| Directory | Contents |
|-----------|----------|
| `roles/*/tasks/` | Ansible task files — `main.yaml` orchestrates, sub-tasks included conditionally |
| `roles/*/templates/` | Jinja2 templates (e.g., `netplan.j2` for static IP configuration) |
| `roles/*/files/` | Static files — Python scripts (`create_vm.py`, `discover_storage.py`), ArgoCD manifests |
| `argocd_applications/{category}/{app}/` | Kustomize manifests organized by category (monitoring, storage, security, infrastructure) |
| `argocd_applications/cluster-apps/` | App-of-app-of-apps hierarchy — parent, platform, and service Application manifests |
| `roles/bootstrap_applications/files/` | ArgoCD app-of-apps parent manifest (`cluster-apps_manifest.yaml`) |
| `inventory/` | `k8s.yaml` (cluster nodes) + `localhost.yaml` (control machine) |
| `env/envvars` | Ansible Runner environment variables (auto-generated) |
| `artifacts/` | Ansible Runner output — cleaned and repopulated on each run |
| `library/` | Custom Ansible modules (if any) |

## Related Documents

- [Ansible Pipeline](../cicd/ansible-pipeline.md) — detailed per-phase breakdown of what each role does
- [Configuration](configuration.md) — environment variable reference for optional features
- [GitOps](../cicd/gitops.md) — ArgoCD SSH key management architecture
