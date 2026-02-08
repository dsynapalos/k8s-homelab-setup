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
| `ARGOCD_VERSION` | ArgoCD version to install | `3.1.7` |
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

Runs a full Ceph cluster inside Kubernetes using raw block devices on worker VMs. No external storage infrastructure needed — Rook discovers attached disks and turns them into block, filesystem, and object storage.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ENABLE_ROOK` | Feature flag | `true` |

Rook uses the secondary disks automatically provisioned by the `setup_localhost` role. Disks must be raw (no LVM signatures) — the automation handles this on fresh runs, but see [Troubleshooting](troubleshooting.md#rook-ceph) if re-provisioning.

### Setup

1. Ensure your Proxmox host has secondary disks available (beyond the OS disk)
2. Set `ENABLE_ROOK=true` in `.env`
3. The automation will:
   - Discover raw disks on the Proxmox host
   - Create LVM thin pools and attach virtual disks to worker VMs
   - Deploy the Rook operator and CephCluster via ArgoCD
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
| `NVIDIA_DEVICE_PLUGIN_VERSION` | Device plugin version | `v0.14.5` |

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
| `ISTIO_VERSION` | Istio version | `1.26.6` |
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

## cert-manager (TLS Certificates)

### What It Does

Automates TLS certificate provisioning for all Ingress endpoints using a self-signed CA chain. Deployed as an ArgoCD Application — no feature flag required (always-on).

### Configuration

cert-manager has no `.env` variables. The version is pinned directly in the Kustomize resource URL at `argocd_applications/security/cert-manager/kustomization.yaml`. All ingresses reference the `homelab-ca-issuer` ClusterIssuer via annotation.

### How It Works

1. cert-manager deploys from upstream static manifest via ArgoCD (sync-wave 1)
2. A self-signed CA chain is bootstrapped (selfsigned-issuer → homelab-ca Certificate → homelab-ca-issuer ClusterIssuer)
3. Every Ingress annotated with `cert-manager.io/cluster-issuer: homelab-ca-issuer` gets an automatic TLS certificate
4. ArgoCD runs in insecure mode with TLS terminated at the Cilium Ingress

For full details, see [cert-manager](../applications/security/cert-manager.md). For networking integration, see [Networking — TLS Certificate Management](networking.md#tls-certificate-management).

---

## Feature Flag Summary

| Feature | Variable | Default | Dependencies |
|---------|----------|---------|-------------|
| CephFS CSI | `ENABLE_CEPH` | `false` | External Ceph cluster |
| Rook-Ceph | `ENABLE_ROOK` | `true` in `example.env` | Secondary disks on Proxmox |
| GPU passthrough | `ENABLE_CUDA` | `false` | IOMMU + vfio-pci on host, `compute: cuda` label |
| Istio Ambient | `ENABLE_ISTIO` | `false` | `VM_CPU_TYPE=host` |
| Gateway API | `ENABLE_GATEWAY_API` | `false` | Mutually exclusive with Ingress Controller mode |

---

## Troubleshooting

```bash
# Verify all .env variables are set (compare against example.env)
diff <(grep -oP '^[A-Z_]+' example.env | sort) <(grep -oP '^[A-Z_]+' .env | sort)

# Verify feature flags and versions
grep -E 'ENABLE_|_VERSION' .env

# Test Proxmox API connectivity
curl -k "https://${PROXMOX_API_HOST}:8006/api2/json/access/ticket" \
  -d "username=${PROXMOX_API_USER}&password=${PROXMOX_API_PASSWORD}"

# Check if a variable is empty (common with env lookups)
python3 -c "from dotenv import dotenv_values; v=dotenv_values('.env'); [print(f'EMPTY: {k}') for k,v in v.items() if not v]"
```

**Feature flag not taking effect**: Ensure the variable name in `.env` matches exactly what the inventory or role looks up. Check `inventory/k8s.yaml` and `inventory/localhost.yaml` for the expected variable names.

**CephFS credentials rejected**: `CEPH_K8S_KEY` and `CEPH_ADMIN_KEY` must be pre-encoded in base64 from the Ceph side. Don't double-encode — the automation passes them through as-is.

**Istio pods crashing on start**: If you see "CPU does not support x86-64-v2", set `VM_CPU_TYPE=host` in `.env` and recreate the VMs.
