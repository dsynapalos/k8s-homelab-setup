# Kubernetes Cluster Automation with Ansible

Automated end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured clusters with Cilium networking and ArgoCD GitOps.

> **Note**: This is not production-ready, just a resource for studying Kubernetes, virtualization, Linux, and generic homelab goodness.

## Features

- **Automated VM Provisioning**: Creates Ubuntu VMs on Proxmox with custom autoinstall ISO
- **Container Runtime**: CRI-O runtime with version matching
- **CNI**: Cilium with eBPF data path, WireGuard encryption, and L2 load balancer announcements
- **Ingress**: Gateway API or Ingress Controller modes
- **Storage**: Optional CephFS dynamic provisioning via Ceph CSI driver
- **Observability**: Hubble for network flow visualization
- **GitOps**: ArgoCD for declarative cluster management
- **Developer Tools**: kubectl, helm, cilium-cli, hubble-cli auto-installed

## Quick Start

### Prerequisites

- Functional Proxmox cluster (or standalone Ubuntu OS instances)
- Control environment (Ubuntu, WSL, etc.)
- SSH access to target nodes

### Installation Steps

1. **Initialize environment**
   ```bash
   sudo chmod +x init.sh && ./init.sh
   ```

2. **Configure environment variables**
   
   Create an `.env` file in the project root (see [example.env](example.env) for reference):
   ```bash
   # Kubernetes Nodes
   K8S_CONTROL_1_IP={{ ssh IP of K8s control plane }}
   K8S_NODE_1_IP={{ ssh IP of K8s node }}
   K8S_SSH_USER={{ ssh user of K8s control/node }}
   K8S_SSH_KEY={{ path to ssh key of K8s control/node }}
   K8S_SSH_PUB_KEY={{ path to ssh public key of K8s control/node }}
   
   # Kubernetes Versions
   K8S_VERSION={{ version of k8s to be installed }}
   CRIO_VERSION={{ version of cri-o to be installed }}
   CILIUM_VERSION={{ version of cilium to be installed }}
   CILIUM_LOADBALANCER_IPPOOL={{ range of reserved IPs to be assigned to LB services }}
   
   # Ansible Configuration
   ANSIBLE_HOST_KEY_CHECKING={{ ignore vm key signature change }}
   ANSIBLE_VERBOSITY={{ ansible log verbosity }}
   
   # Proxmox Configuration
   UBUNTU_RELEASE_VERSION={{ version of ubuntu iso to download }}
   PROXMOX_API_USER={{ proxmox user }}
   PROXMOX_API_PASSWORD={{ proxmox password }}
   PROXMOX_API_HOST={{ proxmox host ip }}
   PROXMOX_LOCAL_STORAGE={{ proxmox storage for iso }}
   PROXMOX_NODE={{ proxmox node name }}
   
   # Network Configuration
   VM_GATEWAY={{ gateway ip }}
   VM_NAMESERVER={{ dns ip }}
   VM_NET_BRIDGE={{ proxmox net bridge }}
   VM_NET_MODEL={{ proxmox net model }}
   
   # VM Resources
   K8S_CONTROL_1_MEM_MB={{ memory assignment for control }}
   K8S_CONTROL_1_DISK_GB={{ disk assignment for control }}
   K8S_CONTROL_1_CPU={{ cpu count for control }}
   K8S_NODE_1_MEM_MB={{ memory assignment for node }}
   K8S_NODE_1_DISK_GB={{ disk assignment for node }}
   K8S_NODE_1_CPU={{ cpu count for node }}
   
   # ArgoCD GitOps Configuration (Optional)
   ARGOCD_VERSION={{ ArgoCD version to install (e.g., 3.1.7) }}
   REPOSITORY_SSH_URL={{ git repository SSH URL (e.g., git@gitlab.com:username/repo.git) }}
   REPOSITORY_TOKEN={{ Repository Personal Access Token with API scope }}
   
   # CephFS Storage Configuration (Optional)
   ENABLE_CEPH={{ enable CephFS dynamic storage provisioning (true/false) }}
   CEPH_CSI_VERSION={{ Ceph CSI driver version (e.g., 3.9.0) }}
   CEPH_CLUSTER_ID={{ Ceph cluster FSID from 'ceph fsid' }}
   CEPH_MON_HOST={{ Ceph monitor address (e.g., 192.168.1.100:6789) }}
   CEPH_K8S_USER={{ Ceph client username for Kubernetes (plain text) }}
   CEPH_K8S_KEY={{ Ceph client key for Kubernetes (base64-encoded) }}
   CEPH_ADMIN_KEY={{ Ceph admin key (base64-encoded) }}
   
   # NVIDIA GPU Configuration (Optional)
   ENABLE_CUDA={{ enable NVIDIA GPU passthrough and CUDA support (true/false) }}
   GPU_PCI_ADDRESS={{ PCI address of GPU on Proxmox host (e.g., 0000:01:00) }}
   NVIDIA_DEVICE_PLUGIN_VERSION={{ NVIDIA device plugin version (e.g., v0.14.5) }}
   ```
   
   **GitLab/Repository Integration** (optional for ArgoCD GitOps):
   1. Create a Personal Access Token in your Git hosting provider:
      - **GitLab**: Settings → Access Tokens → Create token with `api` scope
      - **GitHub**: Settings → Developer settings → Personal access tokens → Generate with `repo` scope
   2. Add `REPOSITORY_SSH_URL` and `REPOSITORY_TOKEN` to your `.env` file
   3. The automation will automatically:
      - Generate a 4096-bit RSA SSH key pair for ArgoCD
      - Store the public key in a Kubernetes ConfigMap for idempotency
      - Register the public key as a read-only deploy key (GitLab only - auto-detects from URL)
      - Create an ArgoCD repository secret with the private key
   
   **CephFS Storage Integration** (optional for dynamic persistent volumes):
   1. Ensure you have an existing Ceph cluster (e.g., Proxmox built-in Ceph)
   2. Create a CephFS filesystem with data and metadata pools
   3. Create a Kubernetes client user:
      ```bash
      ceph auth get-or-create client.kubernetes \
        mon 'allow r' \
        osd 'allow rw pool=cephfs_data' \
        mds 'allow rw' \
        -o /etc/ceph/ceph.client.kubernetes.keyring
      ```
   4. Gather required information:
      - Cluster FSID: `ceph fsid`
      - Monitor address: `ceph mon dump` (look for mon addresses)
      - Client key: `ceph auth get client.kubernetes` (already base64-encoded)
      - Admin key: `ceph auth get client.admin` (already base64-encoded)
   5. Set `ENABLE_CEPH=true` and configure Ceph variables in `.env`
   6. The automation will:
      - Install `ceph-common` package on all nodes
      - Load and persist the ceph kernel module
      - Deploy Ceph CSI driver via Helm
      - Create two StorageClasses: `cephfs` (default, Delete) and `cephfs-retain` (Retain)

   **NVIDIA GPU Passthrough** (optional for CUDA workloads):
   1. Enable IOMMU and configure VFIO on the Proxmox host
   2. Find the PCI address of your GPU on the Proxmox host:
      ```bash
      lspci | grep -i vga
      # Example output: 01:00.0 VGA compatible controller: NVIDIA Corporation GP106 [GeForce GTX 1060 6GB]
      ```
   3. Extract the PCI address in format `0000:01:00` (domain:bus:slot, omit function):
      ```bash
      lspci -D | grep -i vga | awk '{print $1}' | cut -d'.' -f1
      # Example output: 0000:01:00
      ```
   4. Set environment variables in `.env`:
      ```bash
      ENABLE_CUDA=true
      GPU_PCI_ADDRESS=0000:01:00
      NVIDIA_DEVICE_PLUGIN_VERSION=v0.14.5
      ```
   5. Ensure the target node has `compute: cuda` label in `inventory/k8s.yaml`
   6. The automation will:
      - Pass through GPU to VM during creation (Q35 machine type, includes all PCI functions)
      - Install NVIDIA drivers on the node (auto-selects best LTS server version)
      - Configure NVIDIA Container Toolkit for CRI-O
      - Create CRI-O nvidia runtime handler (NOT as default for security)
      - Create Kubernetes RuntimeClass `nvidia` for GPU access isolation
      - Deploy NVIDIA device plugin to Kubernetes
      - Advertise GPU resources to scheduler (`nvidia.com/gpu`)
   
   **Using GPUs in Pods**: Pods must explicitly request GPU access with both:
   ```yaml
   apiVersion: v1
   kind: Pod
   metadata:
     name: gpu-pod
   spec:
     runtimeClassName: nvidia  # Required: Enables GPU library injection
     containers:
     - name: cuda-container
       image: nvidia/cuda:12.2.0-base-ubuntu22.04
       command: ["nvidia-smi"]
       resources:
         limits:
           nvidia.com/gpu: 1  # Required: Requests GPU allocation
   ```
   This two-layer security model prevents unauthorized GPU access.

3. **Run automation**
   
   **Full cluster setup** (infrastructure + Kubernetes + ArgoCD):
   ```bash
   python3 setup-clusters.py
   ```
   
   **Application-only deployment** (assumes cluster already exists):
   ```bash
   python3 setup-applications.py
   ```

4. **Access cluster**
   ```bash
   kubectl get nodes
   cilium status
   hubble observe
   ```

## What Gets Deployed

### Infrastructure
- Custom Ubuntu autoinstall ISO with cloud-init
- Proxmox VMs with static IP configuration
- UFW firewall with Kubernetes and Cilium rules

### Kubernetes Stack
- kubeadm-based cluster initialization
- CRI-O container runtime
- Cilium CNI with:
  - kube-proxy replacement (eBPF)
  - WireGuard pod-to-pod encryption
  - L2 LoadBalancer announcements
  - Hubble observability suite

### Storage (Optional)
- CephFS dynamic provisioning via Ceph CSI driver
- Kernel-based CephFS mounts for high performance
- Two StorageClasses: `cephfs` (Delete) and `cephfs-retain` (Retain)
- Dynamic replica scaling based on cluster size

### GPU Support (Optional)
- NVIDIA GPU PCI passthrough to VMs during creation
  - Automatic Q35 machine type for PCIe passthrough
  - Includes all PCI functions (GPU + Audio)
- Intelligent LTS driver selection
  - Scans available LTS server drivers from ubuntu-drivers
  - Selects second-latest version (most battle-tested)
  - Example: If 535, 580 available → installs 535-server
  - Idempotent: preserves existing driver on re-runs (no auto-upgrades)
- NVIDIA Container Toolkit for CRI-O runtime
  - nvidia runtime handler (NOT default for security)
  - RuntimeClass isolation for GPU access control
- NVIDIA Device Plugin for GPU resource advertising
- Node labeling for GPU workload scheduling
- **GPU Monitoring Stack**:
  - NVIDIA DCGM Exporter for GPU metrics (utilization, temperature, power, memory)
  - Prometheus integration via service discovery
  - Grafana dashboard with 8 GPU performance panels

**GPU Driver Upgrade Strategy**:
- Initial installs get second-latest LTS (proven stable, not bleeding edge)
- Re-runs preserve existing driver version (no automatic upgrades)
- Manual upgrade process:
  1. Test new driver on non-production nodes
  2. Validate CUDA compatibility with workloads
  3. Remove old driver: `apt remove nvidia-driver-XXX-server`
  4. Re-run playbook to install current second-latest LTS
  5. Reboot and validate with `nvidia-smi`

### GitOps & Tools
- ArgoCD for application management
- kubectl, helm, cilium-cli, hubble-cli on localhost

## Performance

**Initial Run** (includes ISO download and remastering):
- ~3GB Ubuntu ISO download and modification
- Upload to Proxmox storage
- Full cluster deployment: **~17 minutes**

## Architecture

### Entry Points

The project provides two separate Python wrappers around Ansible playbooks for different lifecycle phases:

#### 1. `setup-clusters.py` → `setup_cluster.yaml`
**Purpose**: Full end-to-end cluster provisioning
**Phases**:
1. Localhost setup (CLI tools: kubectl, helm, cilium-cli)
2. Infrastructure provisioning (Proxmox VMs with autoinstall ISO)
3. OS preparation (packages, CRI-O, firewall)
4. Cluster initialization (kubeadm init/join)
5. Networking (Cilium CNI with L2 announcements)
6. GitOps (ArgoCD installation with SSH key setup)

**When to use**: New cluster deployments or full reprovisioning

#### 2. `setup-applications.py` → `setup_applications.yaml`
**Purpose**: Application deployment to existing clusters
**Phases**:
1. ArgoCD bootstrap (ensures ArgoCD is installed)
2. Application onboarding (`bootstrap_applications` role)
   - Uploads ArgoCD Application manifests from `argocd_applications/`
   - Declaratively configures applications (e.g., Prometheus)

**When to use**: 
- Adding new applications after cluster is running
- Re-running application deployments without touching infrastructure
- Development workflow for application manifests

**Separation Rationale**: 
- **Faster iteration**: Application changes don't require full cluster rebuild (~17 min → seconds)
- **Safety**: Prevents accidental infrastructure modification when deploying apps
- **Role isolation**: Clear boundary between cluster lifecycle and application lifecycle

### Role Breakdown

The automation is organized into modular Ansible roles, each responsible for a specific phase of cluster setup:

#### Infrastructure Provisioning
**`provision_infra`** - Proxmox VM provisioning with custom Ubuntu autoinstall ISO
- Downloads official Ubuntu Server ISO from releases.ubuntu.com
- Remasters ISO with cloud-init autoinstall configuration (7z extraction, GRUB modification)
- Creates hybrid-boot ISO (BIOS + UEFI support via xorriso)
- Uploads to Proxmox storage via API (idempotent - checks for existing ISOs)
- Creates VMs with specified resources (CPU, memory, disk)
- Configures static networking (IP, gateway, nameserver)
- **GPU passthrough** (optional, for nodes with `compute: cuda` label):
  - Adds PCI device passthrough during VM creation via `create_vm.py`
  - Format: `hostpci0: {PCI_ADDRESS},pcie=1,x-vga=0`
  - Includes all PCI functions (GPU + Audio device)
  - Requires `GPU_PCI_ADDRESS` environment variable

#### Operating System Configuration
**`setup_os`** - Prepares Ubuntu hosts for Kubernetes installation
- Disables swap (required by kubelet)
- Configures APT repositories for Kubernetes packages
- Installs container runtime (CRI-O) with version matching K8s
- Installs kubeadm, kubelet, kubectl
- Configures UFW firewall rules:
  - Control plane: API server (6443), etcd (2379-2380), kubelet (10250)
  - Worker nodes: HTTP/HTTPS ingress (80/443), NodePort range (30000-32767)
  - **Cilium networking**: VXLAN (8472/udp), WireGuard (51871/udp)
- Enables IP forwarding for pod routing
- **CephFS support** (optional, when `ENABLE_CEPH=true`):
  - Installs `ceph-common` package (provides mount.ceph and ceph CLI)
  - Loads ceph kernel module immediately via `modprobe`
  - Persists module loading via `/etc/modules-load.d/ceph.conf`
  - Validates configuration with grep check
- **CUDA support** (optional, for nodes with `compute: cuda` label):
  - Auto-selects best LTS server driver via `ubuntu-drivers list --gpgpu`
  - Installs NVIDIA Container Toolkit
  - Configures CRI-O with nvidia runtime handler (NOT as default for security)
  - Creates proper CRI-O config at `/etc/crio/crio.conf.d/99-nvidia.conf`
  - Configures nvidia-container-runtime with full runtime paths
  - Reboots node automatically after driver installation if needed
  - Verifies GPU with `nvidia-smi`
  - Idempotent: Preserves existing driver version on re-runs

#### Cluster Initialization
**`setup_cluster_master`** - Initializes Kubernetes control plane
- Runs `kubeadm init` with pod network CIDR configuration
- Installs Cilium CNI via Helm with:
  - kube-proxy replacement (eBPF data path)
  - WireGuard encryption for pod-to-pod traffic
  - L2 announcements for LoadBalancer services
  - Hubble observability (UI, relay, metrics)
  - Gateway API or Ingress Controller support
- Generates cluster admin kubeconfig
- Fetches kubeconfig to localhost (`~/.kube/config`)

**`setup_cluster_node`** - Joins worker nodes to cluster
- Runs `kubeadm join` with token from control plane
- Configures kubelet and CRI-O integration

#### Networking & Observability
**`bootstrap_cillium`** - Advanced Cilium CNI configuration
- Creates CiliumLoadBalancerIPPool for LoadBalancer service IPs
- Configures per-node L2AnnouncementPolicy (ARP/NDP announcements)
- Restarts unmanaged pods to apply Cilium networking
- Integrates with CRI-O runtime

#### GitOps & Application Deployment
**`bootstrap_argocd`** - Installs ArgoCD for declarative cluster management with Git repository integration
- **SSH Key Management** (idempotent):
  - Checks for existing SSH public key in Kubernetes ConfigMap (`argocd-ssh-public-key`)
  - If not found, generates new 4096-bit RSA key pair at `/tmp/argocd`
  - Stores public key in ConfigMap for subsequent runs (avoids regeneration)
- **Deploy Key Registration** (GitLab only):
  - Parses `REPOSITORY_SSH_URL` to extract host and project path
  - Auto-detects GitLab from repository host URL
  - Queries existing deploy keys via GitLab API
  - Registers public key as read-only deploy key only if not already present
- **ArgoCD Installation**:
  - Creates `argocd` namespace
  - Installs ArgoCD v{{ ARGOCD_VERSION }} controllers and UI
  - Configures dual Ingress (HTTP + HTTPS with TLS passthrough) for UI access
  - Creates `homelab` AppProject (permissive: all repos, all namespaces)
- **Repository Secret**:
  - Creates Kubernetes Secret (`argocd-repo-ssh-key`) with ArgoCD label
  - Contains private SSH key for Git repository authentication

**`bootstrap_applications`** - Onboards applications via ArgoCD manifests (used by `setup-applications.py`)
- Reads ArgoCD Application manifests from `argocd_applications/` directory
- Uploads manifests to Kubernetes using `kubernetes.core.k8s` module
- Example: `argocd_applications/prometheus/` contains Prometheus deployment manifests
- Applications are GitOps-managed after initial upload
- **Workflow**: Developer creates manifests → `setup-applications.py` uploads → ArgoCD syncs from Git

**`bootstrap_cephfs_storage_class`** - CephFS dynamic storage provisioning (optional, enabled via `ENABLE_CEPH=true`)
- Installs Ceph CSI driver via Helm chart from ceph.github.io/csi-charts
- Creates Secret with Ceph authentication keys:
  - Uses Ansible `b64encode` filter for userID/adminID (plain text → base64)
  - Passes through pre-encoded Ceph keys (already base64)
- Configures Helm values:
  - `csiConfig`: Ceph monitor addresses and cluster ID
  - `cephconf`: Injects `mon_host` configuration into ceph.conf
  - `provisioner.replicaCount`: Dynamic scaling (1 for single-node, 2+ for multi-node clusters)
  - `nodeplugin`: DaemonSet for mounting volumes on all nodes
- Creates two StorageClasses:
  - `cephfs` (default): Delete reclaim policy for dynamic cleanup
  - `cephfs-retain`: Retain policy for persistent data
- **Prerequisites**: Requires ceph kernel module (configured by `setup_os` role)
- **Version**: Uses v3.9.0 by default (compatible with older x86-64 CPUs)

**`bootstrap_nvidia_device_plugin`** - NVIDIA GPU device plugin deployment (optional, enabled via `ENABLE_CUDA=true`)
- Deploys NVIDIA device plugin as DaemonSet in `kube-system` namespace
- Uses `nodeSelector: compute: cuda` to target only GPU nodes
- Exposes GPU resources to Kubernetes scheduler as `nvidia.com/gpu`
- Adds node labels for workload targeting:
  - `accelerator: nvidia-gpu`
  - `gpu-type: gtx-1060`
- Waits for device plugin pods to be running before proceeding
- Verifies GPU capacity is advertised on nodes
- **Prerequisites**: NVIDIA drivers and Container Toolkit installed by `setup_os` role
- **Version**: Configurable via `NVIDIA_DEVICE_PLUGIN_VERSION` (default: v0.14.5)

**`bootstrap_prometheus`** - Monitoring stack deployment (optional, deprecated)
- Legacy role for direct Prometheus deployment
- Consider using ArgoCD ApplicationSets instead

#### Developer Tools
**`setup_localhost`** - Installs CLI tools on control machine
- kubectl (configured with cluster kubeconfig)
- Helm (Kubernetes package manager)
- Cilium CLI (network policy debugging, connectivity tests)
- Hubble CLI (network flow visualization)

#### Utility Roles
**`install_repo`** - Manages package repository configuration

### Execution Flow

The main playbook (`setup_cluster.yaml`) orchestrates roles in this sequence:

1. **Localhost Setup** (`test_ansible_runner`, `setup_localhost`)
   - Validates environment, installs CLI tools

2. **Infrastructure Provisioning** (`provision_infra`)
   - Creates/uploads autoinstall ISO, provisions VMs

3. **Control Plane Setup** (`setup_cluster_master`)
   - Initializes Kubernetes, installs Cilium, fetches kubeconfig

4. **Worker Setup** (`setup_cluster_node`)
   - Joins nodes to cluster

5. **Network Configuration** (`bootstrap_cillium`)
   - Configures LoadBalancer IP pools, L2 announcements

6. **Storage Configuration** (`bootstrap_cephfs_storage_class`)
   - Deploys CephFS CSI driver if enabled

7. **GPU Device Plugin** (`bootstrap_nvidia_device_plugin`)
   - Deploys NVIDIA device plugin if CUDA enabled

8. **GitOps Installation** (`bootstrap_argocd`)
   - Deploys ArgoCD for application management

## Networking Architecture

### Cilium CNI Details
- **Data Path**: eBPF-based kube-proxy replacement (better performance than iptables)
- **Overlay**: VXLAN tunnels between nodes (requires UDP 8472)
- **Encryption**: WireGuard tunnels for pod-to-pod traffic (requires UDP 51871)
- **Load Balancing**: L2 network announcements (ARP/NDP) for LoadBalancer services
- **Ingress**: 
  - Gateway API mode: Modern routing with Envoy proxy
  - Ingress Controller mode: Traditional Ingress with shared LoadBalancer IP
- **Observability**: Hubble for network flow visualization and policy debugging

### IP Addressing
- **Pod CIDR**: Assigned by Cilium IPAM (typically 10.0.0.0/16)
- **Service CIDR**: Default Kubernetes service network (10.96.0.0/12)
- **LoadBalancer Pool**: User-defined via `CILIUM_LOADBALANCER_IPPOOL` (e.g., 192.168.1.100-192.168.1.150)

## ArgoCD GitOps Integration

### SSH Key Management Architecture

The project implements a fully idempotent SSH key management system for ArgoCD repository access:

#### Flow Diagram
```
┌─────────────────────────────────────────────────────────────┐
│ 1. Check if argocd-ssh-public-key ConfigMap exists          │
└─────────────────┬───────────────────────────────────────────┘
                  │
         ┌────────┴─────────┐
         │                  │
    YES  │                  │  NO
         ▼                  ▼
┌──────────────────┐  ┌──────────────────────┐
│ Read Public Key  │  │ Include              │
│ from ConfigMap   │  │ manage_ssh_keys.yaml │
└────────┬─────────┘  └──────────┬───────────┘
         │                       │
         │            ┌──────────▼────────────┐
         │            │ Generate 4096-bit RSA │
         │            │ key at /tmp/argocd    │
         │            └──────────┬────────────┘
         │                       │
         │            ┌──────────▼────────────┐
         │            │ Store public key in   │
         │            │ ConfigMap for reuse   │
         │            └──────────┬────────────┘
         └───────────────────────┘
                     │
         ┌───────────▼───────────────────────────────────┐
         │ 2. Parse REPOSITORY_SSH_URL:                  │
         │    - Extract repository_host (e.g. gitlab.com)│
         │    - Extract project_path (user/repo)         │
         │    - URL-encode path (/ → %2F)                │
         └───────────┬───────────────────────────────────┘
                     │
         ┌───────────▼───────────────────────────────────┐
         │ 3. If 'gitlab' detected in host:              │
         │    - GET /api/v4/projects/{path}/deploy_keys  │
         │    - Compare public key fingerprints          │
         │    - POST only if key not found               │
         └───────────┬───────────────────────────────────┘
                     │
         ┌───────────▼───────────────────────────────────┐
         │ 4. Create argocd-repo-ssh-key Secret          │
         │    (ArgoCD auto-discovers via label)          │
         └───────────────────────────────────────────────┘
```

#### Key Features

1. **Idempotent Key Generation**
   - Public key stored in ConfigMap (not Secret - it's public!)
   - Subsequent runs read existing key instead of regenerating
   - Prevents multiple deploy key registrations

2. **Smart Deploy Key Registration**
   - Auto-detects Git hosting provider from URL
   - GitLab: Uses API to check existing keys before registering
   - GitHub: Prepared for future implementation
   - Handles API errors gracefully (404, auth failures)

3. **URL Encoding**
   - GitLab API requires project path with `%2F` instead of `/`
   - Example: `dsynapalos/cluster-setup` → `dsynapalos%2Fcluster-setup`

4. **Security Best Practices**
   - Private key only stored in Kubernetes Secret with `no_log: true`
   - Public key in ConfigMap (appropriate for non-sensitive data)
   - Read-only deploy key (cannot push to repository)

### Configuration Files

- **`roles/bootstrap_argocd/tasks/main.yaml`**: Orchestration and key detection
- **`roles/bootstrap_argocd/tasks/manage_ssh_keys.yaml`**: Key generation and storage
- **ConfigMap**: `argocd-ssh-public-key` (namespace: argocd)
- **Secret**: `argocd-repo-ssh-key` (namespace: argocd, label: `argocd.argoproj.io/secret-type: repository`)



## Troubleshooting

### Common Issues

**DNS Resolution Failures**
- Ensure VXLAN (8472/udp) and WireGuard (51871/udp) are allowed in firewall
- Check Cilium pod status: `kubectl get pods -n kube-system -l k8s-app=cilium`

**Pods Not Starting**
- Verify CRI-O is running: `systemctl status crio`
- Check kubelet logs: `journalctl -u kubelet -f`
- Ensure swap is disabled: `swapon -s` (should be empty)

**VM Provisioning Fails**
- Verify Proxmox API credentials in `.env`
- Check ISO exists in Proxmox storage
- Ensure sufficient resources (disk, memory) on Proxmox node

**kubeconfig Not Found**
- Check `/etc/kubernetes/new_cluster_admin.conf` exists on control plane
- Verify SSH connectivity to control plane node

**GPU Issues**
- **GPU not detected**: Ensure RuntimeClass `nvidia` exists and pod has `runtimeClassName: nvidia`
- **CRI-O nvidia runtime**: Check `/etc/crio/crio.conf.d/99-nvidia.conf` exists with proper monitor_path
- **nvidia-container-runtime**: Verify `/etc/nvidia-container-runtime/config.toml` has full paths to runc/crun
- **DCGM metrics missing**: Ensure DCGM exporter pod has GPU allocation and runtimeClassName: nvidia
- **Duplicate GPU metrics**: Remove pod-level Prometheus annotations, only scrape service endpoint
- **Dashboard shows multiple time series**: Add `max() by (gpu, Hostname)` aggregation to queries

## GPU Stress Testing & Monitoring

### Running CUDA Stress Test

To validate GPU monitoring and thermal performance:

```bash
# Create a CUDA stress test pod
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gpu-stress
  namespace: default
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
  - name: cuda-container
    image: nvidia/cuda:12.2.0-devel-ubuntu22.04
    command: ["/bin/bash", "-c"]
    args:
      - |
        cat > stress.cu <<'EOC'
        #include <cuda_runtime.h>
        #include <stdio.h>
        #include <stdlib.h>

        #define N 5000
        #define BLOCK_SIZE 16

        __global__ void matrixMul(float *a, float *b, float *c, int n) {
            int row = blockIdx.y * blockDim.y + threadIdx.y;
            int col = blockIdx.x * blockDim.x + threadIdx.x;
            
            if (row < n && col < n) {
                float sum = 0.0f;
                for (int k = 0; k < n; k++) {
                    sum += a[row * n + k] * b[k * n + col];
                }
                c[row * n + col] = sum;
            }
        }

        int main() {
            size_t bytes = N * N * sizeof(float);
            float *d_a, *d_b, *d_c;
            
            cudaMalloc(&d_a, bytes);
            cudaMalloc(&d_b, bytes);
            cudaMalloc(&d_c, bytes);
            
            dim3 blocks(N/BLOCK_SIZE + 1, N/BLOCK_SIZE + 1);
            dim3 threads(BLOCK_SIZE, BLOCK_SIZE);
            
            printf("Starting GPU stress test (5000x5000 matrix multiplication loop)\n");
            printf("Monitor Grafana dashboard for GPU metrics\n");
            
            while(1) {
                matrixMul<<<blocks, threads>>>(d_a, d_b, d_c, N);
                cudaDeviceSynchronize();
            }
            
            return 0;
        }
        EOC
        nvcc stress.cu -o stress
        ./stress
    resources:
      limits:
        nvidia.com/gpu: 1
EOF

# View in Grafana dashboard
# Open: "NVIDIA GPU Dashboard"
```

### Expected Thermal Performance (Water-Cooled GTX 1060 6GB)

Based on actual stress testing with sustained 100% GPU utilization:

| Time | Temperature | GPU Utilization | Power Draw | Status |
|------|-------------|-----------------|------------|--------|
| Idle | 30-35°C | 0% | ~10W | Baseline |
| 0 min | 41°C | 100% | 98.7W | Initial load |
| 16 min | 60°C | 100% | 98.7W | Thermal equilibrium |

**Analysis**:
- **Heat soak rate**: ~1.2°C/minute initially, stabilizing at 60°C
- **Safety margin**: 23°C below throttle point (83°C)
- **Rating**: Excellent for water cooling (optimal: 45-60°C under load)
- **Stock air cooling comparison**: Would typically reach 70-80°C under same load

**Interpretation**:
- GPU at 60°C indicates water cooling loop has reached thermal equilibrium
- Radiator successfully dissipating 98.7W continuously
- If CPU were also stressed simultaneously, expect additional 5-10°C rise (shared loop)
- This thermal profile provides ample headroom for sustained compute workloads

**Cleanup**:
```bash
kubectl delete pod gpu-stress
```

## References
- [Cilium Documentation](https://docs.cilium.io/)
- [kubeadm Cluster Creation](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [Ubuntu Autoinstall](https://ubuntu.com/server/docs/install/autoinstall)
- [Proxmox API](https://pve.proxmox.com/pve-docs/api-viewer/)
- [ArgoCD Getting Started](https://argo-cd.readthedocs.io/en/stable/getting_started/)