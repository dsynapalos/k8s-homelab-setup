# Ansible Pipeline

## What It Does

Three Python entry points drive all cluster automation through Ansible Runner. Each one loads `.env`, cleans the `artifacts/` directory, and executes a specific playbook. There is no CI server — you run these scripts from your workstation and they orchestrate everything from VM creation to application deployment.

## Why It's Structured This Way

The pipeline is split into two independent lifecycles:

- **Infrastructure** (`setup-clusters.py`): Destructive, ~17 minutes, touches VMs and the Kubernetes cluster. You run this when building or rebuilding.
- **Applications** (`setup-applications.py`): Non-destructive, seconds, only uploads ArgoCD manifests. You run this during day-to-day development.

This separation means you can iterate on application configs without ever risking infrastructure state.

A third entry point, **`cleanup-clusters.py`**, reverses everything — destroying VMs, wiping storage, and removing local kubeconfig.

## Entry Points

### `setup-clusters.py` — Full Provisioning

Executes `setup_cluster.yaml` — the 14-play playbook that builds everything from scratch.

```bash
python3 setup-clusters.py    # ~17 minutes
```

- Loads `.env` via `python-dotenv`
- Adds `.venv/bin` to `PATH` (so `ansible_runner` can locate Ansible internally)
- Cleans `artifacts/` for fresh debug logs
- Prints execution stats and total time on completion

### `setup-applications.py` — Application Deployment

Executes `setup_applications.yaml` — a single play that uploads ArgoCD manifests.

```bash
python3 setup-applications.py    # Seconds
```

- Same cleanup/timing pattern as above
- No infrastructure changes, safe to run repeatedly
- Applies the app-of-apps parent manifest; ArgoCD handles all downstream syncing

### `cleanup-clusters.py` — Teardown

Executes `cleanup_cluster.yaml` — destroys all VMs and cleans up Proxmox storage.

```bash
python3 cleanup-clusters.py
```

- Destroys VMs via Proxmox API
- Removes LVM thin pools, volume groups, physical volumes
- Wipes disk signatures for clean re-provisioning
- Removes local `~/.kube/config`

---

## Playbook: `setup_cluster.yaml`

This is the main playbook. It runs 14 plays in sequence, each targeting specific host groups and executing one or more roles.

### Execution Flow

```
Play 1:  localhost          → test_ansible_runner + setup_localhost
Play 2:  proxmox            → provision_infra
Play 3:  k8s-control        → setup_cluster_master (includes setup_os)
Play 4:  k8s-nodes          → setup_cluster_node (includes setup_os)
Play 5:  k8s-control        → setup_pki
Play 6:  k8s (all nodes)    → distribute_pki
Play 7:  k8s (all nodes)    → bootstrap_cillium
Play 8:  k8s-control        → bootstrap_istio_ambient
Play 9:  localhost           → bootstrap_nvidia_device_plugin
Play 10: localhost           → bootstrap_argocd
Play 11: localhost           → bootstrap_pki_secret
Play 12: localhost           → bootstrap_harbor_secret
Play 13: localhost           → bootstrap_cephfs_storage_class / bootstrap_rook_ceph
Play 14: localhost           → bootstrap_applications
```

### Phase 1 — Local Preparation

**Hosts**: `localhost` · **Roles**: `test_ansible_runner`, `setup_localhost`

Validates that Ansible Runner is working, then prepares the control machine:

- Installs CLI tools: kubectl, Helm, Cilium CLI, Hubble CLI, and optionally istioctl
- Creates a Python venv (`venv_proxmox`) with `proxmoxer` for Proxmox API access
- Downloads the Ubuntu Server ISO and remasters it with cloud-init autoinstall configuration (injects GRUB menu entry, creates hybrid BIOS+UEFI bootable ISO using xorriso)
- Uploads the remastered ISO to Proxmox storage
- Discovers raw secondary disks on the Proxmox host (for Rook-Ceph OSDs)
- Creates LVM thin pools from secondary disks and calculates per-node disk allocation

The ISO remaster is idempotent — if the autoinstall ISO already exists on Proxmox, the entire block is skipped.

### Phase 2 — VM Provisioning

**Hosts**: `proxmox` · **Role**: `provision_infra` · **Strategy**: `free` (parallel)

Creates VMs on Proxmox for every host defined in inventory:

- Staggers VM creation with calculated delays to avoid Proxmox serial port conflicts
- Calls `create_vm.py` with full environment (CPU, memory, disk, network bridge, GPU PCI address for cuda nodes, secondary disk spec for worker nodes)
- Polls for IP assignment (`poll_for_ip.py`, 1800s timeout) — the VM boots from the autoinstall ISO and gets a DHCP address
- Configures the OS: sets hostname, creates SSH user with passwordless sudo, deploys authorized keys
- Applies static IP via netplan template, waits for reconnection
- Cleans up: disables DHCP, removes the default `ubuntu` user, disables password authentication

GPU passthrough is automatic for nodes with `labels.compute: cuda` — the VM is created with `hostpci0` pointing to the configured PCI address and the machine type is set to Q35.

### Phase 3 — Control Plane Initialization

**Hosts**: `k8s-control` · **Role**: `setup_cluster_master`

Prepares the OS (via included `setup_os` role) and initializes the Kubernetes control plane:

**OS preparation** (`setup_os`):
- Disables swap, enables IP forwarding
- Installs CRI-O (container runtime) and kubeadm/kubelet (Kubernetes), both version-pinned
- Configures UFW firewall rules for Kubernetes ports, Cilium VXLAN/WireGuard, and node ports
- Optionally loads Ceph kernel module (`ENABLE_CEPH`) or installs NVIDIA drivers (`compute: cuda` nodes)

**Cluster initialization**:
- Runs `kubeadm init --skip-phases=addon/kube-proxy` (Cilium replaces kube-proxy)
- Fetches the admin kubeconfig to `~/.kube/config` on localhost
- Waits for the node to report Ready
- Applies declarative node labels from inventory (removes stale labels, adds missing ones)

Idempotent via `creates: /etc/kubernetes/admin.conf` — re-running skips the init if the cluster already exists.

### Phase 4 — Worker Node Join

**Hosts**: `k8s-nodes` · **Role**: `setup_cluster_node`

Same OS preparation as the control plane, then joins each worker to the cluster:

- Generates a join token from the control plane (`kubeadm token create --print-join-command`)
- Runs the join command on each worker (idempotent via `creates: /etc/kubernetes/kubelet.conf`)
- Waits for the node to report Ready
- Applies node labels, protecting system-managed labels (`kubernetes.io/*`, `nvidia.com/*`, `accelerator`, `gpu-type`)

### Phase 5 — PKI Generation

**Hosts**: `k8s-control` · **Role**: `setup_pki`

Generates a two-tier CA hierarchy on the control plane node:

- **Root CA**: ECC secp384r1 key, self-signed certificate with 10-year validity. Private key stays on the control node filesystem and never leaves.
- **Intermediate CA**: ECC secp384r1 key, signed by root CA with 5-year validity and `pathlen:0` (cannot sign further sub-CAs).
- Fetches root CA certificate, intermediate certificate, and intermediate key to the Ansible controller (`/tmp/homelab-pki/`) for downstream plays.

All key generation uses `force: false` — re-runs skip generation if the files already exist.

### Phase 6 — PKI Distribution & Registry Mirrors

**Hosts**: `k8s` (all nodes) · **Role**: `distribute_pki`

Distributes the root CA and configures CRI-O registry mirrors on all cluster nodes:

- Copies the root CA certificate to `/usr/local/share/ca-certificates/` and runs `update-ca-certificates` so CRI-O and all system tools trust the homelab CA chain
- Writes a CRI-O registry mirror configuration (`/etc/containers/registries.conf.d/harbor-mirror.conf`) that routes image pulls through Harbor’s proxy cache projects:
  - `docker.io` → `harbor.k8s.local/dockerhub-cache`
  - `quay.io` → `harbor.k8s.local/quay-cache`
  - `registry.k8s.io` → `harbor.k8s.local/k8s-registry-cache`
  - `nvcr.io` → `harbor.k8s.local/nvcr-cache`
- Restarts CRI-O only when the CA or mirror config actually changes

### Phase 7 — Cilium Networking

**Hosts**: `k8s` (all nodes) · **Role**: `bootstrap_cillium`

Deploys Cilium CNI via Helm with full kube-proxy replacement:

- Two mutually exclusive modes controlled by `ENABLE_GATEWAY_API`:
  - **Gateway API mode**: Cilium with Envoy proxy, Gateway API CRDs, `bpf.masquerade=true`
  - **Ingress Controller mode**: Cilium Ingress Controller with Istio Ambient compatibility settings (`bpf.masquerade=false`, `socketLB.hostNamespaceOnly=true`, `cni.exclusive=false`)
- Both modes enable: WireGuard encryption, Hubble observability with TLS, L2 announcements
- Creates a `CiliumLoadBalancerIPPool` from the configured CIDR
- Creates per-node `CiliumL2AnnouncementPolicy` on worker nodes (ARP/NDP for LoadBalancer IPs)
- Patches CoreDNS with a `*.k8s.local` rewrite rule to resolve Ingress hostnames to the Cilium Ingress ClusterIP internally (enables OIDC backchannel calls without separate internal URLs)
- Restarts all non-hostNetwork pods to pick up Cilium networking

### Phase 8 — Istio Ambient (Optional)

**Hosts**: `k8s-control` · **Role**: `bootstrap_istio_ambient` · **Condition**: `ENABLE_ISTIO=true`

Installs the sidecar-less Istio Ambient service mesh via four Helm charts:

1. **istio-base**: CRDs and foundational resources
2. **istio-cni**: CNI plugin with `profile: ambient` for transparent traffic redirection
3. **istiod**: Control plane with `PILOT_ENABLE_AMBIENT_CONTROLLERS=true` and multi-cluster identity config
4. **ztunnel**: Per-node L4 proxy DaemonSet with matching mesh identity settings

Includes post-install verification: waits for all pods Running, checks CNI and ztunnel pod counts match node count, verifies control plane health endpoint.

### Phase 9 — NVIDIA Device Plugin (Optional)

**Hosts**: `localhost` · **Role**: `bootstrap_nvidia_device_plugin` · **Condition**: `ENABLE_CUDA=true`

Creates the `nvidia` RuntimeClass and deploys the NVIDIA device plugin:

- Creates RuntimeClass `nvidia` (pods must opt-in to GPU access)
- Deploys device plugin DaemonSet targeting `compute: cuda` nodes with `system-node-critical` priority
- Waits for pods to be Running, verifies `nvidia.com/gpu` resource appears on nodes
- Labels GPU nodes with `accelerator: nvidia-gpu` and `gpu-type: gtx-1060`

### Phase 10 — ArgoCD

**Hosts**: `localhost` · **Role**: `bootstrap_argocd`

Installs ArgoCD and configures Git repository access:

- Deploys ArgoCD from upstream manifests (version `ARGOCD_VERSION`)
- Configures ArgoCD server in insecure mode (`server.insecure: "true"` via `argocd-cmd-params-cm`)
- Creates a single Ingress with cert-manager TLS termination and `ingress.cilium.io/force-https` for HTTP→HTTPS redirect
- Removes legacy dual-ingress resources (HTTP + HTTPS passthrough) if they exist
- Generates SSH keypair (if not already stored in ConfigMap), registers as deploy key on GitLab
- Creates the `homelab` AppProject allowing all repos, namespaces, and resource types
- Enables the Application health check in `argocd-cm` (Lua script that reports child Application health status, required for app-of-apps sync wave ordering)

See [GitOps](gitops.md) for the full SSH key management flow.

### Phase 11 — PKI Secret for cert-manager

**Hosts**: `localhost` · **Role**: `bootstrap_pki_secret`

Pre-creates the `homelab-ca-secret` Secret in the `cert-manager` namespace before ArgoCD deploys cert-manager:

- Creates the `cert-manager` namespace
- Creates a `kubernetes.io/tls` Secret containing the intermediate CA certificate, intermediate CA private key, and root CA certificate (as `ca.crt`)
- When cert-manager deploys (via ArgoCD), the `homelab-ca-issuer` ClusterIssuer immediately finds its signing material — no self-signed bootstrap chain needed

This Secret is the same one that trust-manager reads to distribute the CA across namespaces.

### Phase 12 — Harbor Admin Secret

**Hosts**: `localhost` · **Role**: `bootstrap_harbor_secret`

Pre-creates a randomly generated admin password for Harbor before ArgoCD deploys it:

- Creates the `harbor` namespace
- Checks if the `harbor-admin-password` Secret already exists (idempotent)
- If missing, generates a 32-character random password and stores it in an Opaque Secret with key `HARBOR_ADMIN_PASSWORD`
- The Harbor Helm chart reads this via `existingSecretAdminPassword`, and the bootstrap Job reads it via `secretKeyRef` to authenticate API calls
- Nobody needs to know this password — all human access goes through Keycloak OIDC

### Phase 13 — Storage (Optional)

**Hosts**: `localhost` · **Roles**: `bootstrap_cephfs_storage_class`, `bootstrap_rook_ceph`

Two mutually independent storage options:

**CephFS CSI** (`ENABLE_CEPH=true`):
- Deploys ceph-csi-cephfs Helm chart connecting to an external Ceph cluster
- Creates Ceph credentials Secret with base64-encoded IDs and pre-encoded keys
- Scales provisioner replicas based on worker node count
- Creates StorageClasses: `cephfs` (default, Delete) and `cephfs-retain` (Retain)

**Rook-Ceph** (`ENABLE_ROOK=true`):
- Deploys ArgoCD Application manifests for the Rook operator and cluster
- Waits for operator Deployment to be Available (5 min timeout)
- Waits for CephCluster CR to reach `phase: Ready` (15 min timeout)

### Phase 14 — Applications

**Hosts**: `localhost` · **Role**: `bootstrap_applications`

Uploads the ArgoCD app-of-apps parent manifest:

- Applies `roles/bootstrap_applications/files/cluster-apps_manifest.yaml` to Kubernetes via `kubernetes.core.k8s`
- This single Application CR (`cluster-apps`) points to `argocd_applications/cluster-apps/` and discovers two second-order Applications: `cluster-platform` (sync wave 1) and `cluster-services` (sync wave 2)
- ArgoCD then cascades through the hierarchy, deploying platform apps in dependency order before service apps
- See [GitOps](../cicd/gitops.md) for the full app-of-app-of-apps architecture and sync wave ordering

---

## Playbook: `setup_applications.yaml`

A single-play subset of the main playbook — runs only Phase 14 (bootstrap_applications).

```yaml
- name: setup applications
  hosts: localhost
  roles:
    - bootstrap_applications
```

Use this for fast iteration on application manifests without touching infrastructure. The role applies the app-of-apps parent manifest, and ArgoCD picks up any changes from the Git repository.

---

## Playbook: `cleanup_cluster.yaml`

Reverses the provisioning pipeline:

- Builds a list of VM names from inventory (`k8s-control` + `k8s-nodes`)
- Destroys VMs via `destroy_vms.py` (Proxmox API)
- Removes Proxmox storage pool registrations via `cleanup_storage.py`
- SSHs to Proxmox host to discover and clean LVM structures (thin pools, VGs, PVs)
- Wipes disk signatures (`wipefs -a`) so disks are discovered as raw on next run
- Removes local `~/.kube/config`

---

## Utility Role: `install_repo`

A reusable role called by `setup_localhost` and `setup_os` to add APT repositories:

- Downloads a GPG signing key
- Adds a signed APT repository
- Installs the specified package
- Holds the package version with `dpkg_selections`

Used for: kubectl, kubelet, kubeadm, CRI-O, Helm.

---

## Inventory Structure

Two inventory files serve different target groups:

**`inventory/k8s.yaml`** — Cluster nodes:
- Host groups: `proxmox`, `k8s-control`, `k8s-nodes` (all inherit from `k8s` parent)
- All variables sourced from `.env` via `{{ lookup("env", "VAR_NAME") }}`
- Per-host node labels (e.g., `compute: cuda` for GPU nodes)

**`inventory/localhost.yaml`** — Control machine:
- Used for Kubernetes API operations (ArgoCD, CephFS, GPU device plugin)
- Uses venv Python interpreter: `{{ playbook_dir }}/.venv/bin/python`
- Contains all localhost-specific variables (Ceph config, GPU settings)

---

## Troubleshooting

Every run cleans `artifacts/` before execution. After a run, check:

| File | Contents |
|------|----------|
| `artifacts/*/stdout` | Full playbook output |
| `artifacts/*/stderr` | Error messages |
| `artifacts/*/job_events/*.json` | Per-task execution details with timing |

## Links

- [Ansible Runner Documentation](https://ansible-runner.readthedocs.io/)
- [Ansible Roles](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html)
- [kubeadm Documentation](https://kubernetes.io/docs/reference/setup-tools/kubeadm/)
- [Proxmox API Reference](https://pve.proxmox.com/pve-docs/api-viewer/)
