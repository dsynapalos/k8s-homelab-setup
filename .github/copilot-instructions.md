# Kubernetes Cluster Automation Project

This project automates end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured K8s clusters with Cilium networking and ArgoCD.

## Architecture Overview

**Execution Flow**: 
- **Full cluster**: `setup-clusters.py` → `setup_cluster.yaml` → role-based automation
- **Applications only**: `setup-applications.py` → `setup_applications.yaml` → `bootstrap_argocd` + `bootstrap_applications`

- **Infrastructure**: Proxmox VM provisioning with Ubuntu autoinstall ISO modification
- **K8s Stack**: kubeadm + CRI-O runtime + Cilium CNI + ArgoCD GitOps
- **Networking**: Cilium with WireGuard encryption, L2 load balancer announcements
- **Storage**: Optional CephFS dynamic provisioning via Ceph CSI driver
- **GPU Support**: Optional NVIDIA GPU passthrough and CUDA workload support

## Key Components

### Main Entry Points
- `setup-clusters.py`: Python wrapper using ansible-runner for full cluster provisioning
  - Runs `setup_cluster.yaml` playbook
  - Phases: localhost setup → infra provisioning → OS setup → cluster init → networking → ArgoCD
  - Execution time: ~17 minutes (includes ISO download/remaster)
  - Use case: New cluster deployments
  
- `setup-applications.py`: Python wrapper for application-only deployment
  - Runs `setup_applications.yaml` playbook
  - Phases: ArgoCD bootstrap → application manifest upload
  - Execution time: Seconds to minutes (no infrastructure changes)
  - Use case: Adding/updating applications on existing clusters
  - Workflow: Ensures ArgoCD installed → uploads manifests from `argocd_applications/`

- `setup_cluster.yaml`: Main playbook orchestrating 7-phase deployment
- `setup_applications.yaml`: Application playbook with 2 phases (ArgoCD + applications)
- `inventory/k8s.yaml`: Environment-driven inventory with extensive variable templating
- `inventory/localhost.yaml`: Localhost-specific inventory for ArgoCD and application tasks

### Role Structure Pattern
```
roles/
├── provision_infra/        # Proxmox VM creation, ISO handling, GPU passthrough
│   ├── files/
│   │   └── create_vm.py        # VM creation with optional GPU passthrough (hostpci0)
├── setup_localhost/        # CLI tools installation (kubectl, helm, cilium-cli, hubble-cli)
├── setup_os/              # OS preparation (packages, firewall, CRI-O, kernel modules)
│   ├── tasks/
│   │   ├── configure_ceph_kernel.yaml  # CephFS kernel module setup
│   │   └── configure_cuda.yaml         # NVIDIA driver + container toolkit for CUDA nodes
├── setup_cluster_master/  # kubeadm init, kubeconfig generation
├── setup_cluster_node/    # kubeadm join workers
├── bootstrap_cillium/     # Helm-based Cilium deployment, L2 announcements
├── bootstrap_cephfs_storage_class/ # CephFS CSI driver deployment, StorageClass creation
│   ├── tasks/
│   │   └── install_cephfs.yaml    # Helm deployment, Secret creation, dynamic scaling
├── bootstrap_nvidia_device_plugin/ # NVIDIA device plugin for GPU scheduling
│   ├── tasks/
│   │   ├── main.yaml                      # Conditional inclusion based on ENABLE_CUDA
│   │   └── install_nvidia_device_plugin.yaml  # DaemonSet deployment, node labeling
├── bootstrap_argocd/      # ArgoCD + SSH key management + deploy key registration
│   ├── tasks/
│   │   ├── main.yaml           # Orchestration, key detection, deploy key logic
│   │   └── manage_ssh_keys.yaml # Key generation, ConfigMap/Secret creation
└── bootstrap_applications/ # Onboarding ArgoCD applications from manifests
    ├── tasks/
    │   └── main.yaml           # Uploads manifests from argocd_applications/ directory
    └── files/
        └── prometheus_manifest.yaml  # Example application manifest
```

### Environment Configuration
- **Required**: Copy `example.env` to `.env` with actual values
- **Critical vars**: IP addresses, SSH keys, Proxmox credentials, version specifications, repository URLs, Ceph configuration, GPU PCI address
- **Pattern**: All inventory uses `{{ lookup("env", "VAR_NAME") }}` for configuration
- **Git Integration**: `REPOSITORY_SSH_URL` and `REPOSITORY_TOKEN` for ArgoCD deploy key automation
- **Storage Integration**: `ENABLE_CEPH` flag enables optional CephFS dynamic provisioning
- **GPU Integration**: `ENABLE_CUDA` flag and `GPU_PCI_ADDRESS` for NVIDIA GPU passthrough and CUDA support

## Development Workflows

### Initial Setup
```bash
sudo chmod +x init.sh && ./init.sh  # Python venv + Ansible dependencies
cp example.env .env                  # Configure environment variables
python3 setup-clusters.py           # Full automation run (~17 min)
```

### Application Development Workflow
```bash
# 1. Create ArgoCD Application manifests in argocd_applications/
mkdir -p argocd_applications/myapp
vim argocd_applications/myapp/*.yaml

# 2. Update bootstrap_applications role to include new manifest
# Edit: roles/bootstrap_applications/tasks/main.yaml

# 3. Deploy applications only (no infrastructure changes)
python3 setup-applications.py       # Quick deployment (seconds)
```

### Workflow Comparison
| Task | Entry Point | Duration | Modifies Infrastructure | Use Case |
|------|-------------|----------|------------------------|----------|
| Full cluster build | `setup-clusters.py` | ~17 min | Yes (VMs, K8s, networking) | Initial deployment |
| Application update | `setup-applications.py` | Seconds | No | GitOps manifest changes |
| Add K8s node | `setup-clusters.py` | ~17 min | Yes (VM creation) | Scaling cluster |
| Update ArgoCD app | `setup-applications.py` | Seconds | No | Application lifecycle |

### Role Development Patterns
- **Task organization**: `main.yaml` includes sub-tasks for complex roles
- **Delegation**: K8s operations use `delegate_to: localhost` with local kubeconfig
- **Conditionals**: `when: inventory_hostname == 'k8s-control-1'` for control plane tasks
- **Registration**: Use `register:` for capturing command outputs and change detection

### Cilium Specifics
- **Dual mode support**: Gateway API vs Ingress Controller (controlled by `ENABLE_GATEWAY_API`)
- **L2 announcements**: Per-node CiliumL2AnnouncementPolicy for LoadBalancer IPs
- **Encryption**: WireGuard enabled by default
- **Post-install**: Automatic unmanaged pod restart after Cilium deployment

### CephFS Storage (Optional)
- **Feature flag**: Controlled by `ENABLE_CEPH` (default: false)
- **Driver version**: CSI driver v3.9.0 (CPU-compatible, supports older x86-64)
- **Authentication**: Uses Ansible `b64encode` filter for userID/adminID, passes through pre-encoded Ceph keys
- **Dynamic scaling**: Provisioner replicas based on `groups['k8s-nodes'] | length` (1 for single-node, 2+ for multi-node)
- **Kernel module**: Automatically installed and loaded by `setup_os` role
- **StorageClasses**: `cephfs` (default, Delete) and `cephfs-retain` (Retain)
- **Configuration**: Requires Ceph cluster FSID, monitor address, client credentials

### NVIDIA GPU Support (Optional)
- **Feature flag**: Controlled by `ENABLE_CUDA` (default: false)
- **GPU Passthrough**: Automatic PCI passthrough for nodes labeled `compute: cuda`
  - Configured during VM creation via `create_vm.py` using `GPU_PCI_ADDRESS` env var
  - Format: `hostpci0: {PCI_ADDRESS},pcie=1,x-vga=0` (includes all functions: GPU + Audio)
  - PCI address obtained from Proxmox host: `lspci -D | grep -i vga | awk '{print $1}' | cut -d'.' -f1`
  - Machine type: Automatically set to Q35 when GPU passthrough detected (required for PCIe passthrough)
- **Driver installation**: `setup_os/configure_cuda.yaml` runs on nodes with `labels.compute == 'cuda'`
  - **Intelligent LTS selection**: Queries `ubuntu-drivers list --gpgpu` for available LTS server drivers
  - **Selection logic**: Picks second-latest LTS server version (most proven stable, not bleeding edge)
    * Available drivers sorted: `[535-server, 570-server, 580-server]`
    * Selection: `[-2]` → **535-server** (second from latest)
    * Rationale: Latest may have regressions, second-latest has production track record
    * If only one driver: uses that one `[-1]`
    * Fallback: `nvidia-driver-535-server` if none available
  - **Idempotent behavior**: Preserves existing driver version on re-runs (no automatic upgrades)
  - **Upgrade strategy**: Manual only - test new driver, validate CUDA compatibility, remove old, re-run playbook
  - Installs NVIDIA Container Toolkit for CRI-O runtime
  - Configures CRI-O with nvidia runtime handler (NOT as default for security)
  - Creates proper CRI-O config at `/etc/crio/crio.conf.d/99-nvidia.conf`
  - Configures nvidia-container-runtime with full paths: `/usr/libexec/crio/runc`, `/usr/libexec/crio/crun`
  - Reboots node automatically after driver installation if needed
- **RuntimeClass**: Creates Kubernetes RuntimeClass `nvidia` for GPU access isolation
  - Only pods with `runtimeClassName: nvidia` get GPU library injection
  - Prevents unauthorized GPU access (security by design)
- **Device Plugin**: `bootstrap_nvidia_device_plugin` deploys NVIDIA k8s device plugin
  - DaemonSet with `nodeSelector: compute: cuda` and `runtimeClassName: nvidia`
  - Advertises GPU resources as `nvidia.com/gpu` to scheduler
  - Adds additional labels: `accelerator: nvidia-gpu` and `gpu-type: gtx-1060`
- **Pod Requirements**: For GPU access, pods must specify:
  1. `runtimeClassName: nvidia` (enables GPU library injection)
  2. `resources.limits."nvidia.com/gpu": 1` (requests GPU allocation)
- **Prerequisites**: Proxmox host must have IOMMU enabled and GPU bound to vfio-pci driver
- **Driver Lifecycle**: Initial deployments get second-latest LTS (proven stability), manual upgrades only to prevent CUDA compatibility issues

## Critical Conventions

### Inventory Patterns
- **Host groups**: `k8s-control`, `k8s-nodes`, `proxmox` with inherited `k8s` parent
- **Variable precedence**: Environment variables override defaults via lookup functions
- **Connection vars**: SSH keys and users configured per environment
- **Separate inventories**: `localhost.yaml` for local tasks, `k8s.yaml` for cluster nodes

### Kubernetes Operations
- **Kubeconfig**: Generated as `/etc/kubernetes/new_cluster_admin.conf` on control plane
- **Module usage**: `kubernetes.core.k8s` and `kubernetes.core.helm` for declarative operations
- **Resource management**: YAML definitions embedded in task files, not separate manifests
- **Delegation**: ArgoCD tasks run on `localhost` but operate on cluster via kubeconfig

### Node Label Management
- **Declarative labels**: Defined in inventory `node_labels` variable, applied by `setup_cluster_node` role
- **Automatic cleanup**: Removes user-managed labels not in inventory (enforces desired state)
- **Protected namespaces**: Excludes labels managed by Kubernetes system or operators:
  - `kubernetes.io/*` and `k8s.io/*` - Kubernetes system labels
  - `nvidia.com/*` - NVIDIA device plugin labels
  - `accelerator` and `gpu-type` - GPU-specific labels added by device plugin
- **Idempotency**: Only updates labels when missing or value differs from desired state

### SSH Key Management (ArgoCD)
- **Idempotency**: Public key stored in ConfigMap (`argocd-ssh-public-key`), not regenerated
- **Security**: Private key in Secret with `no_log: true`, public key in ConfigMap
- **Deploy Keys**: GitLab API integration checks existing keys before registering
- **URL Encoding**: Project paths encoded with `%2F` for GitLab API compatibility
- **Task separation**: `main.yaml` (orchestration) + `manage_ssh_keys.yaml` (generation)
- **Conditional execution**: Only include `manage_ssh_keys.yaml` when ConfigMap missing

### CephFS Configuration (bootstrap_cephfs_storage_class)
- **Secret encoding**: `data` field with Ansible b64encode filter, not `stringData`
  - `userID`: Plain text (e.g., "kubernetes") encoded with `{{ CEPH_K8S_USER | b64encode }}`
  - `adminID`: Literal "admin" encoded with `{{ 'admin' | b64encode }}`
  - `userKey`/`adminKey`: Pre-encoded Ceph keys passed through without encoding
- **Helm values**: `cephconf` parameter injects `mon_host` into ceph.conf
- **Dynamic replicas**: `{{ 2 if groups['k8s-nodes'] | length >= 2 else 1 }}`
- **OS prerequisites**: Ceph kernel module must be loaded (handled by `setup_os/configure_ceph_kernel.yaml`)
- **Idempotency**: All tasks safe to re-run (apt state: present, modprobe state: present, lineinfile checks existence)

### File Organization
- **Templates**: Jinja2 templates in `roles/*/templates/` (e.g., `netplan.j2`)
- **Static files**: `roles/*/files/` for artifacts like ISO images
- **ArgoCD apps**: Separate manifests in `argocd_applications/` directory

## Debugging & Troubleshooting

### Ansible Runner Artifacts
- **Location**: `artifacts/` directory (cleaned on each run)
- **Contents**: Command outputs, status, job events for detailed debugging

### Common Failure Points
- **Environment vars**: Missing or incorrect `.env` values cause template failures
- **SSH access**: Verify key-based authentication to target VMs
- **Proxmox API**: Check credentials and storage configuration
- **Cilium deployment**: Monitor pod readiness before proceeding with restarts
- **GitLab API**: Verify `REPOSITORY_TOKEN` has `api` scope, check project path encoding
- **SSH key conflicts**: Delete ConfigMap to force regeneration if keys become invalid
- **CephFS mounting**: Ensure ceph kernel module is loaded (`lsmod | grep ceph`)
- **Ceph authentication**: Verify Secret encoding - userID/adminID must be base64, keys already are
- **CSI version**: Use v3.9.0 for older CPUs (v3.12+ requires x86-64-v2 instructions)
- **GPU passthrough**: Verify IOMMU enabled on Proxmox host, GPU bound to vfio-pci driver
- **CUDA drivers**: Check `nvidia-smi` output on node, verify CRI-O runtime configuration
- **Driver selection**: Check ubuntu-drivers output for available server drivers, verify second-latest logic
- **Driver auto-upgrade**: Expected behavior - drivers are NEVER auto-upgraded, only selected at initial install
- **GPU not detected**: Ensure RuntimeClass `nvidia` exists and pod has `runtimeClassName: nvidia`
- **CRI-O nvidia runtime**: Check `/etc/crio/crio.conf.d/99-nvidia.conf` exists with proper monitor_path
- **nvidia-container-runtime**: Verify `/etc/nvidia-container-runtime/config.toml` has full paths to runc/crun

### Best Practices for Modifications
- **Avoid `is defined` checks**: Ansible handles undefined variables in `when` clauses
- **Use `when` not `failed_when`**: Let tasks naturally skip when conditions not met
- **ConfigMaps for public data**: Use ConfigMaps for non-sensitive data like public keys
- **Secrets for private data**: Use Secrets with `no_log: true` for sensitive information
- **Include patterns**: Use `include_tasks` with conditionals for optional task sets
- **URL encoding**: Use `replace('/', '%2F')` for GitLab API paths, not `urlencode`
- **Secret encoding**: Use `data` field with `b64encode` filter when source is plain text, not `stringData`
- **Dynamic scaling**: Use Jinja2 conditionals with `groups[]` for cluster-size-aware configuration
- **Kernel modules**: Load with `modprobe` (state: present) and persist in `/etc/modules-load.d/`
- **CRI-O runtime config**: Use `/etc/crio/crio.conf.d/*.conf` format with `monitor_path` for each runtime
- **Runtime paths**: Always use full paths (e.g., `/usr/libexec/crio/runc`) in nvidia-container-runtime config
- **RuntimeClass security**: Never set nvidia as default runtime; use RuntimeClass for isolation

When modifying roles, maintain the delegation patterns for K8s operations and preserve environment variable templating for flexibility across deployments.