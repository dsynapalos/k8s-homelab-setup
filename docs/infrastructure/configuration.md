# Configuration

## What This Document Covers

Reference for all optional features and their environment variables. Every value lives in your `.env` file (see [example.env](../../example.env)). The base cluster variables are covered in [Getting Started](../getting-started.md) — this document focuses on the features you opt into.

---

## VM Configuration

### VM CPU Type

| Variable | Description | Example |
|----------|-------------|----------|
| `VM_CPU_TYPE` | Proxmox CPU emulation type | `host` |

Controls what CPU features are exposed to VMs. Set to `host` to expose the physical CPU's full instruction set (required for Istio Ambient, which needs x86-64-v2). Set to `kvm64` for baseline x86-64 emulation. This variable is also referenced in the Istio section below but affects all VMs regardless of Istio.

---

## ArgoCD GitOps

### What It Does

ArgoCD watches your Git repository and automatically syncs Kubernetes manifests to the cluster. The automation handles the full setup: installing ArgoCD, generating SSH keys, registering deploy keys with your Git provider, and creating the AppProject.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ARGOCD_VERSION` | ArgoCD version to install | `3.3.0` |
| `ARGOCD_TARGET_REVISION` | Git branch for all ArgoCD Application CRs (default: `main`) | `main` |
| `REPOSITORY_SSH_URL` | Git repo SSH URL | `git@gitlab.com:user/repo.git` |
| `REPOSITORY_TOKEN` | Personal Access Token with `api` scope | `glpat-xxxxxxxxxxxx` |

### Setup

1. Create a Personal Access Token:
   - **GitLab**: Settings → Access Tokens → scope: `api`
   - **GitHub**: Settings → Developer settings → Personal access tokens → scope: `repo`
2. Add the three variables above to your `.env`
3. The automation will:
   - Generate a 4096-bit RSA keypair for ArgoCD
   - Store the public key in a ConfigMap (idempotent — won't regenerate on re-runs)
   - Register it as a read-only deploy key on GitLab (auto-detected from URL)
   - Create an ArgoCD repository Secret with the private key

For the SSH key management architecture, see [GitOps](../cicd/gitops.md).

### Branch Testing

Set `ARGOCD_TARGET_REVISION` to override which Git branch all ArgoCD Application CRs sync from. This works for both the app-of-apps and Sveltos orchestration paths.

```bash
# In .env — point all apps at a feature branch
ARGOCD_TARGET_REVISION=feature/my-change

# Then deploy
python3 setup-applications.py
```

**How it works**:
- The `bootstrap_applications` role substitutes `targetRevision: main` → `targetRevision: <your-branch>` on the parent manifest, tier CRs, and all individual Application CRs before applying them
- The `bootstrap_sveltos` role does the same substitution when creating ConfigMaps from Application CRs
- The parent and tier Application CRs include `ignoreDifferences` with `RespectIgnoreDifferences` so ArgoCD won't revert the overridden `targetRevision` during auto-sync
- Helm chart version references (e.g., `1.18.2`, `v0.20.3`) are unaffected — only `targetRevision: main` is replaced
- Set back to `main` (or remove the variable) and re-run to restore normal operation

---

## CephFS Storage (External Ceph)

### What It Does

Connects the cluster to an existing external Ceph cluster via the CephFS CSI driver. Provides dynamic PersistentVolume provisioning backed by CephFS, with two StorageClasses for different retention needs.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_CEPH` | Feature flag | `true` |
| `CEPH_CSI_VERSION` | CSI driver version (default: `3.12.2`) | `3.15.0` |
| `CEPH_FSID` | Ceph cluster FSID (`ceph fsid`) | `a1b2c3d4-...` |
| `CEPH_MONITOR` | Monitor address | `192.168.1.100:6789` |
| `CEPH_FS_NAME` | CephFS filesystem name (default: `cephfs`) | `cephfs` |
| `CEPH_FS_POOL` | CephFS data pool name (default: `cephfs_data`) | `cephfs_data` |
| `CEPH_K8S_USER` | Kubernetes client name, plain text (default: `kubernetes`) | `kubernetes` |
| `CEPH_K8S_KEY` | Client key (already base64-encoded) | `AQB...==` |
| `CEPH_ADMIN_KEY` | Admin key (already base64-encoded) | `AQA...==` |

### Setup

1. You need a running Ceph cluster (e.g., Proxmox built-in Ceph)
2. Create a CephFS filesystem with data and metadata pools
3. Create a Kubernetes client:
   ```bash
   ceph auth get-or-create client.kubernetes \
     mon 'allow r' \
     mds 'allow rw fsname=cephfs' \
     osd 'allow rw pool=cephfs_data, allow rw pool=cephfs_metadata, allow rw pool=.mgr' \
     mgr 'allow rw'
   ```
4. Gather the values:
   - Cluster FSID: `ceph fsid` → `CEPH_FSID`
   - Monitor: `ceph mon dump` → `CEPH_MONITOR`
   - Client key: `ceph auth get-key client.kubernetes | base64 -w 0` → `CEPH_K8S_KEY`
   - Admin key: `ceph auth get-key client.admin | base64 -w 0` → `CEPH_ADMIN_KEY`
5. Set `ENABLE_CEPH=true` and the variables above in `.env`

The automation installs `ceph-common` on all nodes, loads the kernel module, deploys the CSI driver via Helm, and creates StorageClasses `cephfs` (default, Delete) and `cephfs-retain` (Retain).

For storage architecture details, see [Storage](storage.md).

---

## Rook-Ceph Storage (In-Cluster)

### What It Does

Runs a full Ceph cluster inside Kubernetes using raw block devices on infra-role worker VMs. No external storage infrastructure needed — Rook discovers attached disks and turns them into block, filesystem, and object storage.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_ROOK` | Feature flag | `true` |

Rook uses the secondary disks automatically provisioned by the `setup_localhost` role. Disks are only attached to workers with `role: infra` — platform-role nodes do not receive secondary disks. Disks must be raw (no LVM signatures) — the automation handles this on fresh runs, but see [Troubleshooting](troubleshooting.md#rook-ceph) if re-provisioning.

### Setup

1. Ensure your Proxmox hosts have secondary disks available (beyond the OS disk)
2. Set `ENABLE_ROOK=true` in `.env`
3. The automation will:
   - Discover raw disks on each Proxmox host independently
   - Create LVM thin pools and attach virtual disks to infra-role worker VMs on the corresponding host
   - Deploy the Rook operator and CephCluster via ArgoCD (placement targets infra nodes)
   - Create three StorageClasses: `rook-ceph-block`, `rook-cephfs`, `rook-ceph-bucket`

For storage architecture details, see [Storage](storage.md).

---

## NVIDIA GPU Passthrough

### What It Does

Passes a physical GPU from the Proxmox host through to a worker VM, installs NVIDIA drivers, and makes the GPU available to Kubernetes pods via the NVIDIA device plugin.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_CUDA` | Feature flag | `true` |
| `GPU_PCI_ADDRESS` | PCI address of GPU on Proxmox host | `0000:01:00` |
| `NVIDIA_DEVICE_PLUGIN_VERSION` | Device plugin version | `v0.18.2` |

### Setup

1. Enable IOMMU on the Proxmox host and bind the GPU to the `vfio-pci` driver
2. Find the PCI address:
   ```bash
   lspci -D | grep -i vga | awk '{print $1}' | cut -d'.' -f1
   # Output: 0000:01:00
   ```
3. Ensure the target node has `compute: cuda` in its labels in `inventory/k8s.yaml`
4. Set the variables above in `.env`

The automation handles everything else: Q35 machine type, PCI passthrough during VM creation, driver selection (second-latest LTS server), Container Toolkit for CRI-O, RuntimeClass `nvidia`, and the device plugin DaemonSet.

For GPU architecture, monitoring, and stress testing, see [GPU Support](gpu-support.md).

### Using GPUs in Pods

Pods must request GPU access explicitly with both fields:

```yaml
spec:
  runtimeClassName: nvidia          # Enables GPU library injection
  containers:
  - name: cuda-container
    image: nvidia/cuda:12.2.0-base-ubuntu22.04
    resources:
      limits:
        nvidia.com/gpu: 1           # Requests GPU allocation from scheduler
```

Without `runtimeClassName: nvidia`, the pod won't see the GPU libraries. Without the resource limit, the scheduler won't assign a GPU. This two-layer model prevents unauthorized GPU access.

---

## Istio Ambient Service Mesh

### What It Does

Adds transparent mTLS encryption between all pods in enrolled namespaces, without sidecars. Istio's ztunnel (zero-trust tunnel) runs as a DaemonSet and handles L4 encryption using HBONE tunneling on port 15008.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_ISTIO` | Feature flag | `true` |
| `ISTIO_VERSION` | Istio version | `1.28.3` |
| `ISTIO_MESH_ID` | Mesh ID (same across federated clusters) | `mesh1` |
| `ISTIO_CLUSTER_NAME` | Unique cluster name within mesh | `cluster-1` |
| `ISTIO_NETWORK` | Network identifier | `network-1` |
| `VM_CPU_TYPE` | Proxmox CPU type — **must be `host`** | `host` |

### Setup

1. **CPU requirement**: Istio 1.23+ needs x86-64-v2 (SSE4.1, SSE4.2, POPCNT). Set `VM_CPU_TYPE=host` in `.env` to expose the host CPU features to VMs. Using `kvm64` will cause ztunnel to crash.
2. Set the variables above in `.env`
3. The automation will:
   - Configure Cilium with three Istio compatibility settings (masquerade, socketLB, CNI chaining)
   - Deploy four Helm charts: istio-base, istio-cni, istiod, ztunnel
   - Verify all components are healthy post-install

### Enrolling Namespaces

Istio Ambient uses namespace-level opt-in. Label a namespace and all pods in it automatically get mTLS — no restart needed:

```bash
# Add namespace to mesh
kubectl label namespace <namespace> istio.io/dataplane-mode=ambient

# Remove from mesh
kubectl label namespace <namespace> istio.io/dataplane-mode-

# Check enrollment
kubectl get namespaces -L istio.io/dataplane-mode
```

**Recommendations**:
- ✅ Enroll application namespaces (backend, frontend, api-gateway)
- ❌ Keep platform namespaces out (kube-system, istio-system, monitoring, argocd) to avoid circular dependencies

### mTLS Modes

| Mode | Behavior |
|------|----------|
| **PERMISSIVE** (default) | Mesh-to-mesh uses mTLS, external traffic still allowed |
| **STRICT** | All traffic must be mTLS (blocks non-mesh sources) |

Start with PERMISSIVE. Use `AuthorizationPolicy` for fine-grained access control rather than jumping to STRICT mode.

For networking internals and Cilium integration, see [Networking](networking.md).

---

## Dragonfly P2P Image Distribution

### What It Does

Deploys [Dragonfly](https://d7y.io/) as a peer-to-peer layer between CRI-O and Harbor. A DaemonSet client on every node intercepts image pulls via a local proxy (port 4001), coordinates piece-based P2P transfer through a scheduler, and caches layers on a seed client. Reduces bandwidth and speeds up multi-node image pulls.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_DRAGONFLY` | Feature flag | `true` |
| `DRAGONFLY_VERSION` | Dragonfly client image tag | `v1.2.11` |

No Helm chart version variable — the Helm chart version (`1.6.14`) and manager/scheduler image tags (`v2.4.2`) are pinned in the ArgoCD Application CR.

### What Changes

When `ENABLE_DRAGONFLY=true`:

- The `bootstrap_pki_secret` role pre-creates the `dragonfly-system` namespace and `dragonfly-ca-cert` Secret (root CA for TLS trust with Harbor)
- The `distribute_pki` role configures CRI-O registry mirrors to route pulls through `127.0.0.1:4001` (the local Dragonfly client)
- The `bootstrap_sveltos` role (if Sveltos is enabled) includes the Dragonfly ClusterProfile with `dependsOn: [cert-manager, trust-manager, rook-ceph-cluster]`
- The Harbor bootstrap Job registers Dragonfly as a preheat provider and creates event-based preheat policies on all proxy cache projects

When `ENABLE_DRAGONFLY=false` (default):

- No Dragonfly components are deployed
- CRI-O mirrors still route through Harbor proxy cache (without Dragonfly P2P)
- Harbor preheat configuration is skipped

### Setup

1. Set `ENABLE_DRAGONFLY=true` in `.env`
2. Run `setup-clusters.py` — the automation will provision the CA Secret and CRI-O mirrors
3. Run `setup-applications.py` — deploys the Dragonfly ArgoCD Application via Sveltos

For architecture, proxy rules, and tuning details, see [Dragonfly](../applications/infrastructure/dragonfly.md).

---

## Sveltos Orchestration Layer

### What It Does

Replaces the ArgoCD app-of-apps pattern with [Project Sveltos](https://projectsveltos.github.io/sveltos/) ClusterProfiles for fine-grained dependency ordering. ArgoCD remains the deployment engine — Sveltos only controls **when** each ArgoCD Application CR is created, using `dependsOn` chains and `validateHealths` Lua scripts.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_SVELTOS` | Feature flag | `true` |
| `SVELTOS_VERSION` | Sveltos Helm chart version | `1.5.4` |

### What Changes

When `ENABLE_SVELTOS=true`:

- The Lua health-check hack in `argocd-cm` is **skipped** (Sveltos handles ordering, not sync waves)
- The `bootstrap_applications` role is **skipped** (no app-of-apps parent CR needed)
- The `bootstrap_rook_ceph` Ansible role is **skipped** (Rook ordering handled by Sveltos `dependsOn`)
- The `bootstrap_sveltos` role installs Sveltos via Helm, creates ConfigMaps from all Application CRs, and applies ClusterProfile manifests

When `ENABLE_SVELTOS=false` (default):

- The existing app-of-apps pattern with sync waves is used unchanged
- No Sveltos components are installed

### Setup

1. Set `ENABLE_SVELTOS=true` in `.env`
2. Run `setup-clusters.py` — the automation will:
   - Install Sveltos via Helm chart into the `projectsveltos` namespace
   - Label the management cluster SveltosCluster with `cluster: homelab`
   - Create ConfigMaps from each Application CR in `argocd_applications/cluster-apps/infra/` and `platform/`
   - Apply ClusterProfile manifests from `sveltos_profiles/` with dependency ordering
   - Conditionally include/exclude profiles for `ENABLE_ROOK`, `ENABLE_CUDA`, and `ENABLE_DRAGONFLY`

### Directory Structure

Application CRs are organized by node placement:

```
argocd_applications/cluster-apps/
├── infra.yaml                    ← Parent CR for infra tier (wave 1)
├── infra/                        ← Infra-node apps (9 CRs, tolerate role=infra)
│   ├── cert-manager.yaml
│   ├── trust-manager.yaml
│   ├── cloudnative-pg.yaml
│   ├── dragonfly.yaml
│   ├── harbor.yaml
│   ├── keycloak.yaml
│   ├── argocd-oidc.yaml
│   ├── rook-ceph-operator.yaml
│   └── rook-ceph-cluster.yaml
├── platform.yaml                 ← Parent CR for platform tier (wave 4)
└── platform/                     ← Platform-node apps (12 CRs, tolerate role=platform)
    ├── alertmanager.yaml
    ├── grafana.yaml
    ├── thanos.yaml
    └── ... (9 more monitoring apps)
```

ClusterProfile manifests live in `sveltos_profiles/` (one per Application CR, 21 total).

---

## API Server HA (kube-vip)

### What It Does

Deploys a [kube-vip](https://kube-vip.io/) static pod on each control plane node to provide a floating Virtual IP (VIP) for the Kubernetes API server. This enables API server high availability — if the active control plane node goes down, another control plane node takes over the VIP via ARP leader election, transparently redirecting `kubectl` and in-cluster API traffic.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `K8S_VIP` | Floating VIP address for the API server | `XXX.XXX.XXX.210` |
| `KUBE_VIP_VERSION` | kube-vip container image version (default: `0.8.7`) | `0.8.7` |

### Setup

1. Choose a VIP address on your LAN subnet that is:
   - **Outside your DHCP range** (so no device gets assigned this IP)
   - **Outside your `CILIUM_LOADBALANCER_IPPOOL`** (so it doesn't conflict with LoadBalancer IPs)
   - **Routable from your workstation** (same L2 subnet as nodes)
2. Set `K8S_VIP` and optionally `KUBE_VIP_VERSION` in `.env`
3. The automation will:
   - Deploy a kube-vip static pod manifest to `/etc/kubernetes/manifests/kube-vip.yaml` on each control plane node
   - Pass `--control-plane-endpoint=<VIP>:6443` to `kubeadm init`
   - Upload certificates so additional control plane nodes can join with `kubeadm join --control-plane`
   - Configure Cilium to use the VIP as `k8sServiceHost` (Cilium agents connect to the API via the VIP)

### How It Works

- **ARP mode**: kube-vip announces the VIP via gratuitous ARP on the local network. Only the leader node responds to ARP requests for the VIP address.
- **Leader election**: Uses a Kubernetes Lease object (`plndr-cp-lock` in `kube-system`) to elect a single owner. Lease duration is 5 seconds with a 3-second renew deadline and 1-second retry period.
- **Static pod**: Deployed as a static pod (not managed by the API server) so it can start before the cluster is initialized. Uses `hostNetwork: true` and requires `NET_ADMIN` + `NET_RAW` capabilities.
- **Metrics**: Exposes Prometheus metrics on port 2112.

### Existing Cluster Guard

kube-vip is only deployed when the primary control plane (`k8s-control-1`) does **not** yet have `/etc/kubernetes/admin.conf`. This prevents kube-vip from being retroactively added to an existing cluster that was initialized without a VIP — which would cause a mismatch between the `controlPlaneEndpoint` in the kubeadm config and the actual VIP.

If `K8S_VIP` is set but the cluster already exists, the VIP is ignored and Cilium continues using the primary control plane IP directly.

---

## cert-manager (TLS Certificates)

### What It Does

Automates TLS certificate provisioning for all Ingress endpoints using an Ansible-generated CA chain. Deployed as an ArgoCD Application — no feature flag required (always-on).

### Configuration

cert-manager has no `.env` variables. The version is pinned directly in the Kustomize resource URL at `argocd_applications/security/cert-manager/kustomization.yaml`. All ingresses reference the `homelab-ca-issuer` ClusterIssuer via annotation.

### How It Works

1. The `setup_pki` role generates a two-tier PKI chain on the control plane (Root CA → Intermediate CA)
2. The `distribute_pki` role installs the root CA certificate on all cluster nodes (system trust + CRI-O)
3. The `bootstrap_pki_secret` role pre-creates the `homelab-ca-secret` Secret in the `cert-manager` namespace (intermediate cert+key, root CA)
4. cert-manager deploys from upstream static manifest via ArgoCD (sync wave 1)
5. The `homelab-ca-issuer` ClusterIssuer references the pre-existing `homelab-ca-secret` and immediately starts signing leaf certificates
6. Every Ingress annotated with `cert-manager.io/cluster-issuer: homelab-ca-issuer` gets an automatic TLS certificate
7. ArgoCD runs in insecure mode with TLS terminated at the Cilium Ingress

For full details, see [cert-manager](../applications/security/cert-manager.md). For networking integration, see [Networking — TLS Certificate Management](networking.md#tls-certificate-management).

---

## Node Role Isolation (Taints & Tolerations)

### What It Does

Worker nodes are divided into two roles — **infra** and **platform** — using both labels and `NoSchedule` taints. This ensures workloads only schedule on nodes designated for their tier, preventing resource contention between infrastructure services (storage, PKI, registry) and application workloads (monitoring, alerting, dashboards).

### How It Works

Each worker node in `inventory/k8s.yaml` has a `role` label and a matching taint:

| Node | Role | Label | Taint |
|------|------|-------|-------|
| k8s-node-1 | Infra | `role: infra` | `role=infra:NoSchedule` |
| k8s-node-2 | Platform | `role: platform` | `role=platform:NoSchedule` |
| k8s-node-3 | Infra | `role: infra` | `role=infra:NoSchedule` |
| k8s-node-4 | Platform | `role: platform` | `role=platform:NoSchedule` |

Control-plane nodes are already tainted by kubeadm with `node-role.kubernetes.io/control-plane:NoSchedule`.

### Inventory Definition

Taints are defined per-host in `inventory/k8s.yaml`:

```yaml
k8s-node-1:
  labels:
    role: infra
  taints:
    - key: role
      value: infra
      effect: NoSchedule
```

The `provision_infra` role aggregates taints from inventory (similar to label aggregation). The `setup_cluster_master` and `setup_cluster_node` roles apply taints declaratively — adding missing taints, removing stale user-managed taints, and preserving system-managed taints (`kubernetes.io/*`, `k8s.io/*`, `nvidia.com/*`).

### Toleration Strategy

Every application manifest includes a toleration matching its target node role:

| Tier | Toleration | Applications |
|------|-----------|-------------|
| **Infra** | `role=infra:NoSchedule` | ArgoCD, cert-manager, trust-manager, CloudNativePG, Harbor, Keycloak, Rook (operator + cluster), istiod, Sveltos, CephFS CSI provisioner |
| **Platform** | `role=platform:NoSchedule` | Prometheus, Grafana, Thanos, Alertmanager, Loki, Jaeger, Matrix, matrix-bridge, OTel Collector, kube-state-metrics, metrics-server, dcgm-exporter |
| **All nodes** | `operator: Exists` | node-exporter, istio-cni, ztunnel, CephFS CSI nodeplugin, NVIDIA device plugin (DaemonSets on every node including control-plane) |

**Upstream application patching**: Applications installed from upstream manifests (not authored in this repo) receive tolerations via Kustomize strategic-merge patches or Helm values:

| Application | Patching Method |
|-------------|----------------|
| cert-manager | Kustomize patch (`tolerations-patch.yaml`) for 3 Deployments |
| Rook operator | Kustomize patch (`operator-tolerations-patch.yaml`) |
| CloudNativePG | Extended existing Kustomize patch (`operator-monitoring-patch.yaml`) |
| Harbor | Helm `valuesObject` tolerations for all components |
| ArgoCD | Ansible `kubernetes.core.k8s` strategic-merge patches after install |
| Istio (istiod) | Helm `tolerations` value — `role=infra:NoSchedule` |
| Istio (CNI, ztunnel) | Helm `tolerations` values — `role operator: Exists` + `control-plane operator: Exists` |
| CephFS CSI | Helm `tolerations` values — provisioner: `role=infra`, nodeplugin: `role operator: Exists` |
| NVIDIA device plugin | Inline DaemonSet manifest — `role operator: Exists` alongside `nvidia.com/gpu` |
| Sveltos | Helm `tolerations` value — `role=infra:NoSchedule` |

### Storage Isolation

Secondary disks for Rook-Ceph OSDs are only attached to infra-role nodes during VM provisioning. The CephCluster CR uses `useAllNodes: true` with placement affinity restricting OSD pods to `role: infra` nodes, so Ceph storage services don't consume resources on platform nodes.

---

## Feature Flag Summary

| Feature | Variable | Default | Dependencies |
|---------|----------|---------|-------------|
| CephFS CSI | `ENABLE_CEPH` | `false` | External Ceph cluster |
| Rook-Ceph | `ENABLE_ROOK` | `true` in `example.env` | Secondary disks on Proxmox |
| GPU passthrough | `ENABLE_CUDA` | `false` | IOMMU + vfio-pci on host, `compute: cuda` label |
| Istio Ambient | `ENABLE_ISTIO` | `false` | `VM_CPU_TYPE=host` |
| Sveltos | `ENABLE_SVELTOS` | `false` | None (replaces app-of-apps when enabled) |
| Dragonfly | `ENABLE_DRAGONFLY` | `false` | `ENABLE_ROOK` (Rook-Ceph for PVCs), Harbor, cert-manager |
| Gateway API | `ENABLE_GATEWAY_API` | `false` | Mutually exclusive with Ingress Controller mode |
| API Server HA | `K8S_VIP` + `KUBE_VIP_VERSION` | unset / `0.8.7` | VIP must be outside DHCP range and LB pool |

---

## Troubleshooting

```bash
# Verify all .env variables are set (compare against example.env)
diff <(grep -oP '^[A-Z_]+' example.env | sort) <(grep -oP '^[A-Z_]+' .env | sort)

# Verify feature flags and versions
grep -E 'ENABLE_|_VERSION' .env

# Test Proxmox API connectivity (use PROXMOX_API_HOST_1 or _2)
curl -k "https://${PROXMOX_API_HOST_1}:8006/api2/json/access/ticket" \
  -d "username=${PROXMOX_API_USER}&password=${PROXMOX_API_PASSWORD}"

# Check if a variable is empty (common with env lookups)
python3 -c "from dotenv import dotenv_values; v=dotenv_values('.env'); [print(f'EMPTY: {k}') for k,v in v.items() if not v]"
```

**Feature flag not taking effect**: Ensure the variable name in `.env` matches exactly what the inventory or role looks up. Check `inventory/k8s.yaml` and `inventory/localhost.yaml` for the expected variable names.

**CephFS credentials rejected**: `CEPH_K8S_KEY` and `CEPH_ADMIN_KEY` must be pre-encoded in base64 from the Ceph side. Don't double-encode — the automation passes them through as-is.

**Istio pods crashing on start**: If you see "CPU does not support x86-64-v2", set `VM_CPU_TYPE=host` in `.env` and recreate the VMs.
