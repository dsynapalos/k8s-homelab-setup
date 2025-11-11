# Kubernetes Cluster Automation Project

This project automates end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured K8s clusters with Cilium networking and ArgoCD.

## Architecture Overview

**Tech Stack**: Python + Ansible + kubeadm + CRI-O + Cilium + ArgoCD
- **Infrastructure**: Proxmox VM provisioning with Ubuntu autoinstall ISO modification
- **Networking**: Cilium eBPF CNI with WireGuard encryption, L2 load balancer announcements
- **Storage**: Optional CephFS dynamic provisioning via Ceph CSI driver
- **GPU Support**: Optional NVIDIA GPU passthrough with intelligent LTS driver selection and CUDA workload support
- **Monitoring**: Prometheus + Grafana + DCGM Exporter for GPU metrics
- **Alerting**: Alertmanager + Matrix Synapse + alertmanager-matrix-bridge for real-time mobile notifications

**Execution Flow**: 
- **Full cluster**: `setup-clusters.py` → `setup_cluster.yaml` → role-based automation (~17 min)
- **Applications only**: `setup-applications.py` → `setup_applications.yaml` → manifest upload (seconds)

**Critical Understanding**: This is a two-playbook architecture with distinct lifecycles:
1. `setup_cluster.yaml`: Infrastructure + cluster provisioning (destructive, creates/modifies VMs)
2. `setup_applications.yaml`: Application deployment only (safe, no infra changes)

## Key Components & Data Flow

### Entry Points (Python → Ansible)
**`setup-clusters.py`** (Full provisioning)
- Uses `ansible_runner` to execute `setup_cluster.yaml`
- Cleans `artifacts/` directory on each run for fresh debugging logs
- 7-phase execution: localhost setup → VM provisioning → OS prep → cluster init → Cilium → storage/GPU → ArgoCD
- ~17 minutes including ISO download/remaster
- **When to use**: New clusters, adding nodes, infrastructure changes

**`setup-applications.py`** (Application-only)
- Uses `ansible_runner` to execute `setup_applications.yaml`
- Single-phase: Uploads ArgoCD Application manifests from `argocd_applications/`
- Seconds to complete (no infrastructure operations)
- **When to use**: GitOps manifest changes, adding applications to existing cluster

### Inventory Architecture
**`inventory/k8s.yaml`**: Cluster node configuration
- **Pattern**: All variables use `{{ lookup("env", "VAR_NAME") }}` from `.env` file
- **Host groups**: `proxmox`, `k8s-control`, `k8s-nodes` (all inherit from `k8s` parent)
- **Node labels**: Defined per-host in `labels:` dict (e.g., `compute: cuda` for GPU nodes)
- **Connection vars**: SSH keys, users, Python interpreter paths

**`inventory/localhost.yaml`**: Control machine configuration  
- Used for K8s API operations (ArgoCD, CephFS, NVIDIA device plugin roles)
- Uses venv Python: `{{ playbook_dir }}/.venv/bin/python`
- Consolidates all localhost-specific variables (Ceph config, GPU settings, ArgoCD credentials)

### Ansible Role Patterns & Responsibilities

#### Infrastructure Layer
**`provision_infra`** (Proxmox host → VMs)
- Downloads Ubuntu Server ISO, remasters with cloud-init autoinstall config
- Creates hybrid-boot ISO (BIOS + UEFI) using 7z + xorriso
- Uploads to Proxmox via API with idempotency checks
- **GPU passthrough**: Calls `create_vm.py` with `hostpci0: {GPU_PCI_ADDRESS}` for nodes with `compute: cuda` label
- **Q35 machine type**: Automatically enabled when GPU detected (required for PCIe passthrough)

**`setup_os`** (OS → Container-ready)
- Disables swap, configures APT repos for K8s packages
- Installs CRI-O (version-matched to K8s), kubeadm, kubelet, kubectl
- **Firewall**: UFW rules for K8s (6443, 10250), Cilium (8472/udp VXLAN, 51871/udp WireGuard)
- **CephFS prep**: `configure_ceph_kernel.yaml` loads ceph module, persists to `/etc/modules-load.d/`
- **CUDA prep**: `configure_cuda.yaml` (runs on `compute: cuda` nodes)
  - Intelligent LTS driver selection: queries `ubuntu-drivers list --gpgpu`, picks **second-latest** (proven stable)
  - Installs NVIDIA Container Toolkit, configures CRI-O nvidia runtime handler (NOT default)
  - Creates `/etc/crio/crio.conf.d/99-nvidia.conf` with `monitor_path` for nvidia runtime
  - Reboots node if driver installed (idempotent - preserves existing driver on re-runs)

#### Cluster Layer
**`setup_cluster_master`** (Control plane initialization)
- Runs `kubeadm init`, installs Cilium via Helm (WireGuard + L2 announcements)
- Generates `/etc/kubernetes/new_cluster_admin.conf`, fetches to localhost `~/.kube/config`
- **Delegation**: All K8s operations use `delegate_to: localhost` with kubeconfig

**`setup_cluster_node`** (Worker join + labeling)
- Runs `kubeadm join` with token from control plane
- **Declarative labels**: Applies labels from inventory `node_labels`, removes unlabeled keys (enforces desired state)
- **Protected namespaces**: Excludes system labels (`kubernetes.io/*`, `k8s.io/*`, `nvidia.com/*`)

**`bootstrap_cillium`** (Networking finalization)
- Creates `CiliumLoadBalancerIPPool` for LoadBalancer service IPs
- Per-node `CiliumL2AnnouncementPolicy` (ARP/NDP announcements)
- Restarts unmanaged pods to apply Cilium networking

#### Storage & GPU Layer (Optional)
**`bootstrap_cephfs_storage_class`** (when `ENABLE_CEPH=true`)
- Deploys Ceph CSI driver v3.9.0 via Helm (CPU-compatible, no x86-64-v2 requirement)
- **Secret encoding**: Uses `data:` field with Ansible `b64encode` for `userID`/`adminID`, passes through pre-encoded Ceph keys
- **Dynamic scaling**: `provisioner.replicaCount: {{ 2 if groups['k8s-nodes'] | length >= 2 else 1 }}`
- Creates StorageClasses: `cephfs` (default, Delete) and `cephfs-retain` (Retain)

**`bootstrap_nvidia_device_plugin`** (when `ENABLE_CUDA=true`)
- Creates Kubernetes `RuntimeClass: nvidia` for GPU access isolation
- Deploys NVIDIA device plugin DaemonSet with `nodeSelector: compute: cuda`
- Advertises `nvidia.com/gpu` resources, adds labels (`accelerator: nvidia-gpu`)
- **Security model**: Pods MUST specify both `runtimeClassName: nvidia` AND `resources.limits."nvidia.com/gpu": 1`

#### GitOps Layer
**`bootstrap_argocd`** (ArgoCD + Git integration)
- **SSH key idempotency**: Checks for ConfigMap `argocd-ssh-public-key` before generating new key
- **Deploy key automation**: 
  1. Parses `REPOSITORY_SSH_URL` to extract host/project path
  2. URL-encodes path (`/` → `%2F`) for GitLab API
  3. Queries existing deploy keys via `GET /api/v4/projects/{path}/deploy_keys`
  4. Registers new key only if fingerprint not found (prevents duplicates)
- Creates `homelab` AppProject (permissive: all repos, all namespaces)
- Configures Ingress with TLS passthrough for ArgoCD UI

**`bootstrap_applications`** (Application manifests)
- Uploads ArgoCD Application manifests from `argocd_applications/` directory
- **Pattern**: `kubernetes.core.k8s` with `definition: "{{ lookup('file', item) }}"`
- **Fileglob**: `loop: "{{ lookup('fileglob', role_path + '/files/*_manifest.yaml', wantlist=True) }}"`
- Example: `argocd_applications/monitoring/prometheus/` → full Prometheus stack

### Environment Configuration Pattern
**`.env` file**: Single source of truth for all configuration
- **Required**: Copy `example.env` to `.env` with actual values
- **Inventory integration**: All vars accessed via `{{ lookup("env", "VAR_NAME") }}` 
- **Categories**:
  - **Node IPs/SSH**: `K8S_CONTROL_1_IP`, `K8S_NODE_1_IP`, `K8S_SSH_USER`, `K8S_SSH_KEY`
  - **Versions**: `K8S_VERSION=1.34`, `CRIO_VERSION=1.33`, `CILIUM_VERSION=1.18.1`
  - **Proxmox**: `PROXMOX_API_USER`, `PROXMOX_API_PASSWORD`, `PROXMOX_API_HOST`, `PROXMOX_NODE`
  - **Networking**: `VM_GATEWAY`, `VM_NAMESERVER`, `CILIUM_LOADBALANCER_IPPOOL` (e.g., `192.168.1.193/27`)
  - **Git/ArgoCD**: `REPOSITORY_SSH_URL`, `REPOSITORY_TOKEN` (requires `api` scope)
  - **CephFS**: `ENABLE_CEPH=false`, `CEPH_FSID`, `CEPH_MONITOR`, `CEPH_K8S_KEY` (pre-encoded base64)
  - **GPU**: `ENABLE_CUDA=false`, `GPU_PCI_ADDRESS=0000:01:00` (from `lspci -D | grep -i vga`)
- **No defaults in roles**: All configuration must come from environment (fail-fast on missing vars)

## Development Workflows

### Initial Setup & Full Deployment
```bash
sudo chmod +x init.sh && ./init.sh  # Python venv + Ansible dependencies
cp example.env .env                  # Configure environment variables
python3 setup-clusters.py           # Full automation run (~17 min)
```

### Application Development Cycle
```bash
# 1. Create/modify ArgoCD Application manifests
mkdir -p argocd_applications/myapp
vim argocd_applications/myapp/deployment.yaml
vim argocd_applications/myapp/service.yaml

# 2. Add manifest to bootstrap_applications role
vim roles/bootstrap_applications/files/myapp_manifest.yaml

# 3. Deploy to cluster (quick iteration)
python3 setup-applications.py       # Seconds, no infrastructure changes

# 4. Verify in ArgoCD UI
# Access via: https://argocd.k8s.local (configured in /etc/hosts)
```

### Task Execution Comparison
| Task | Entry Point | Duration | Infra Changes | K8s Changes | Use Case |
|------|-------------|----------|---------------|-------------|----------|
| Full cluster | `setup-clusters.py` | ~17 min | VM create/destroy | Full cluster build | New deployment, add nodes |
| Applications only | `setup-applications.py` | Seconds | None | Manifests upload | App updates, GitOps changes |
| OS-only changes | `setup-clusters.py` | ~5 min | None | None | Firewall, packages, drivers |
| Cluster reset | `setup-clusters.py` | ~17 min | VM recreate | Full rebuild | Major version upgrades |

### Debugging Patterns
**Ansible Runner Artifacts**: Check `artifacts/` directory for detailed execution logs
- `stdout`: Full command output
- `stderr`: Error messages
- `job_events/*.json`: Detailed task execution timeline
- Auto-cleaned on each run for fresh debugging

**Common Investigation Commands**:
```bash
# Verify environment configuration
cat .env | grep -E "(K8S_|PROXMOX_|ENABLE_)"

# Check Ansible inventory rendering
ansible-inventory -i inventory/k8s.yaml --list

# Test connectivity to nodes
ansible k8s -i inventory/k8s.yaml -m ping

# Check Cilium status
cilium status --wait

# Verify GPU detection
kubectl describe node k8s-node-1 | grep nvidia.com/gpu

# Check ArgoCD deploy key registration
kubectl get configmap argocd-ssh-public-key -n argocd -o yaml
```

### Idempotency Patterns in Roles
**Key principle**: Re-running playbooks should be safe and converge to desired state

**Examples from codebase**:
- **SSH keys**: Check ConfigMap before generating (`bootstrap_argocd/tasks/main.yaml`)
- **Deploy keys**: Query GitLab API for existing keys before registering
- **Node labels**: Only update labels that differ from inventory (`setup_cluster_node`)
- **Secrets**: Use `state: present` with `data` field to avoid overwriting
- **Packages**: `apt: state=present` (installs only if missing)
- **Kernel modules**: `modprobe: state=present` + persist to `/etc/modules-load.d/`

**When adding new tasks**:
- Use `kubernetes.core.k8s: state=present` for K8s resources (not `kubectl apply`)
- Register command outputs to conditionally skip subsequent tasks
- Use `creates:` parameter for file operations
- Check existence before creation (ConfigMap, Secret, API keys)

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

### GPU Monitoring Stack
- **DCGM Exporter**: NVIDIA Data Center GPU Manager metrics collector
  - **Version**: v3.3.5-3.4.1 (DaemonSet deployment on compute nodes)
  - **Metrics exposed**: GPU utilization, temperature, power, memory, clocks, PCIe throughput
  - **Deployment**: Runs on nodes with `compute: cuda` label using `runtimeClassName: nvidia`
  - **Service discovery**: Prometheus scrapes via kubernetes-services job using service annotations
  - **Configuration**: Standard ClusterIP service with `prometheus.io/scrape: "true"` annotation
  - **Note**: Requires GPU allocation (`nvidia.com/gpu: 1`) to access device metrics
  
- **Prometheus Integration**: Vanilla Prometheus (not Operator)
  - **Scraping**: Uses kubernetes_sd_configs for service discovery
  - **Target**: `dcgm-exporter.monitoring.svc:9400/metrics`
  - **Relabeling**: Preserves pod/container/namespace labels from DCGM metrics
  - **No ServiceMonitor**: Uses annotation-based discovery (`prometheus.io/*` annotations)
  
- **Grafana Dashboard**: NVIDIA GPU monitoring dashboard
  - **Location**: `argocd_applications/monitoring/grafana/dashboard_definitions/nvidia-gpu-dashboard.json`
  - **Panels**: 8 visualizations (utilization, temp, power, memory usage, FB free, SM clock, memory clock, PCIe)
  - **Query pattern**: Uses `max() by (gpu, Hostname)` aggregation to prevent duplicate time series
  - **Datasource**: Requires `uid: prometheus` in datasource config for proper dashboard linking
  - **Label filtering**: Queries use hardware labels (gpu, Hostname), not pod-specific labels
  - **ConfigMap**: Deployed without hash suffix (`disableNameSuffixHash: true`) for stable naming
  
- **Metric Deduplication**: Critical for accurate visualization
  - **Problem**: DCGM adds pod/container/namespace labels when GPU allocated, creating per-pod time series
  - **Solution**: Aggregate by hardware identifiers only: `max() by (gpu, Hostname)`
  - **Scraping**: Only service-level scraping (no pod annotations) to prevent duplicate targets
  - **Result**: Single time series per GPU regardless of workload pod lifecycle

### Alerting Stack
- **Alertmanager**: Routes alerts from Prometheus to notification channels
  - **Version**: v0.27.0
  - **Configuration**: `argocd_applications/monitoring/alertmanager/alertmanager.yml`
  - **Webhook routing**: Routes all alerts to `http://alertmanager-matrix:3000/alerts/default`
  - **API**: Uses v2 API (v1 deprecated in 0.28.0)

- **Matrix Synapse**: Self-hosted Matrix homeserver for notifications
  - **Version**: v1.98.0
  - **Storage**: PostgreSQL 15-alpine sidecar for persistence
  - **Registration**: Enabled via init container (`enable_registration: true`, `enable_registration_without_verification: true`)
  - **Init container**: Always checks/adds registration settings on pod start (handles PVC persistence)
  - **Ingress**: Accessible at `http://matrix.k8s.local` (port 8008 HTTP, 8448 federation)
  - **Bootstrap automation**: PostSync Job creates bot user, Alerts room, saves credentials to Secret

- **Matrix Bootstrap Job**: Automated bot registration and room setup
  - **Execution**: ArgoCD PostSync hook (sync-wave 3)
  - **Image**: Alpine 3.19 with curl, openssl, kubectl
  - **Authentication**: HMAC-SHA1 using auto-generated `registration_shared_secret` from homeserver.yaml
  - **Bot user**: Creates `@alertbot-TIMESTAMP:matrix.k8s.local` (timestamp for idempotency)
  - **Room creation**: Creates public "Alerts" room with alias `#alerts:matrix.k8s.local`
  - **Room settings**: `public_chat` preset, `world_readable` history, public join rules
  - **Secret generation**: Saves to `matrix-bot` Secret (user-id, access-token, room-id, webhook-url)
  - **Idempotency**: Checks for existing valid Secret before running, skips if present
  - **RBAC**: Requires ServiceAccount with pods/exec and secrets permissions

- **alertmanager-matrix-bridge**: Translates Alertmanager webhooks to Matrix messages
  - **Version**: docker.io/metio/matrix-alertmanager-receiver:2025.11.5
  - **Config generation**: Init container dynamically creates config.yaml from Secret values
  - **Init container pattern**: Heredoc with quoted delimiter ('CONFIGEOF'), sed substitutions with YAML quoting
  - **YAML quoting**: Critical for values with special characters (@, !, :) - uses `sed "s|...|\"${VAR}\"|g"`
  - **Deployment**: Reads from `matrix-bot` Secret (created in sync-wave 3)
  - **Sync-wave**: Wave 4 (after bootstrap creates Secret in wave 3)
  - **Templates**: Emoji-based formatting (⚠️ warning, 🚨 critical, ✅ resolved, ℹ️ info)
  - **Endpoint**: Listens on port 3000 at `/alerts/` path

- **Alert Rules**: GPU temperature monitoring
  - **Location**: `argocd_applications/monitoring/prometheus/alert-rules.yaml`
  - **Rule**: `GPUHighTemperature` - fires when `DCGM_FI_DEV_GPU_TEMP > 60` for 5 minutes
  - **Severity**: warning
  - **Annotations**: Includes GPU ID, hostname, actual temperature, threshold value

- **End-to-End Flow**: Prometheus → Alert Rules → Alertmanager → Bridge → Matrix → Element App (mobile push)

### Application Stack Structure
**Pattern**: Kustomize-based manifests in `argocd_applications/monitoring/`
- **Prometheus**: Vanilla deployment (not Operator) with kubernetes_sd_configs service discovery
- **Grafana**: ConfigMaps for dashboards (`disableNameSuffixHash: true` for stable naming)
- **DCGM Exporter**: DaemonSet on GPU nodes with RuntimeClass nvidia
- **Node Exporter**: DaemonSet for host metrics
- **Alertmanager**: StatefulSet for alert routing

**Key files per application**:
- `kustomization.yaml`: Kustomize configuration
- `deployment.yaml` or `statefulset.yaml` or `daemonset.yaml`: Workload definition
- `service.yaml`: ClusterIP service with Prometheus annotations
- `ingress.yaml` (optional): Cilium ingress for external access
- Config files: `prometheus.yml`, `alert-rules.yaml`, etc.

## Critical Conventions

### Inventory Patterns
- **Host groups**: `k8s-control`, `k8s-nodes`, `proxmox` with inherited `k8s` parent
- **Variable precedence**: Environment variables override defaults via lookup functions
- **Connection vars**: SSH keys and users configured per environment
- **Separate inventories**: `localhost.yaml` for local tasks, `k8s.yaml` for cluster nodes
- **Label propagation**: `labels:` dict in inventory → Kubernetes node labels via `setup_cluster_node` role

### Kubernetes Operations
- **Kubeconfig**: Generated as `/etc/kubernetes/new_cluster_admin.conf` on control plane
- **Module usage**: `kubernetes.core.k8s` and `kubernetes.core.helm` for declarative operations
- **Resource management**: YAML definitions embedded in task files, not separate manifests
- **Delegation**: ArgoCD/CephFS/GPU tasks run on `localhost` but operate on cluster via kubeconfig
- **State management**: Always use `state: present` (not imperative `kubectl apply`)

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
- **Static files**: `roles/*/files/` for artifacts like ISO images and VM scripts (`create_vm.py`)
- **ArgoCD apps**: Kustomize manifests in `argocd_applications/{category}/{app}/` directory structure
- **Role tasks**: `main.yaml` orchestrates, sub-tasks in same directory for complex workflows
- **Python wrappers**: Top-level `setup-*.py` files use ansible_runner, clean artifacts on each run

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
- **DCGM metrics missing**: Ensure DCGM exporter pod has GPU allocation and runtimeClassName: nvidia
- **Duplicate GPU metrics**: Remove pod-level Prometheus annotations, only scrape service endpoint
- **Dashboard shows multiple time series**: Add `max() by (gpu, Hostname)` aggregation to queries
- **Grafana datasource not found**: Ensure datasource has explicit `uid: prometheus` in configuration

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
- **Prometheus service discovery**: Use service-level annotations only, avoid pod annotations to prevent duplicate scraping
- **Grafana ConfigMaps**: Set `disableNameSuffixHash: true` for dashboard ConfigMaps to ensure stable naming
- **GPU dashboard queries**: Always aggregate by hardware labels (`gpu`, `Hostname`) to prevent per-pod time series duplication
- **DCGM deployment**: Must use `runtimeClassName: nvidia` and request GPU to access device metrics

When modifying roles, maintain the delegation patterns for K8s operations and preserve environment variable templating for flexibility across deployments.

## Quick Reference for AI Agents

### Understanding the Two Execution Paths
1. **Infrastructure + Apps** (`setup-clusters.py`): Use when VMs/cluster need changes. ~17 min, destructive.
2. **Apps only** (`setup-applications.py`): Use when only manifests change. Seconds, safe.

### Key Files to Check First
- `.env`: All configuration lives here (IPs, versions, feature flags)
- `inventory/k8s.yaml`: Node definitions, labels, VM specs from env vars
- `inventory/localhost.yaml`: Localhost vars for K8s API operations
- `setup_cluster.yaml`: 8-play sequence (read to understand execution order)
- `artifacts/stdout`: Most recent run output for debugging

### Common Patterns You'll See
- **All config from env**: `{{ lookup("env", "VAR_NAME") }}` everywhere
- **Delegation**: K8s tasks run on localhost: `delegate_to: localhost`, `kubeconfig: /etc/kubernetes/new_cluster_admin.conf`
- **Conditional roles**: Optional features via `when: ENABLE_CEPH == "true"` or `when: ENABLE_CUDA == "true"`
- **Idempotent checks**: ConfigMap/Secret existence checks before creation
- **Label-driven behavior**: GPU nodes have `labels.compute: cuda` in inventory

### Troubleshooting Decision Tree
1. **Playbook failed**: Check `artifacts/stdout` and `artifacts/stderr`
2. **Template error**: Missing `.env` variable - check `example.env` for reference
3. **K8s resource not created**: Check kubeconfig path and delegation
4. **SSH failure**: Verify `K8S_SSH_KEY` path and node accessibility
5. **GPU not working**: Check RuntimeClass exists, pod has `runtimeClassName: nvidia`, CRI-O config
6. **Duplicate metrics**: Only scrape services, not pods; use aggregation in queries

### Making Changes Safely
- **New application**: Add to `argocd_applications/`, run `setup-applications.py`
- **New node**: Update `.env` and `inventory/k8s.yaml`, run `setup-clusters.py`
- **Config change**: Update `.env`, determine which playbook is appropriate
- **New optional feature**: Add `ENABLE_*` flag, use conditionals in roles
- Always test idempotency: running twice should not break anything