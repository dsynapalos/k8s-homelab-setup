# Kubernetes Cluster Automation with Ansible

Automated end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured clusters with Cilium networking and ArgoCD GitOps.

> **Note**: This is not production-ready, just a resource for studying Kubernetes, virtualization, Linux, and generic homelab goodness.

## Features

- **Automated VM Provisioning**: Creates Ubuntu VMs on Proxmox with custom autoinstall ISO
- **Container Runtime**: CRI-O runtime with version matching
- **CNI**: Cilium with eBPF data path, WireGuard encryption, and L2 load balancer announcements
- **Ingress**: Gateway API or Ingress Controller modes
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
   ```
   
   **GitLab/Repository Integration** (optional for ArgoCD GitOps):
   1. Create a Personal Access Token in your Git hosting provider:
      - **GitLab**: Settings → Access Tokens → Create token with `api` scope
      - **GitHub**: Settings → Developer settings → Personal access tokens → Generate with `repo` scope (W)
   2. Add `REPOSITORY_SSH_URL` and `REPOSITORY_TOKEN` to your `.env` file
   3. The automation will automatically:
      - Generate a 4096-bit RSA SSH key pair for ArgoCD
      - Store the public key in a Kubernetes ConfigMap for idempotency
      - Register the public key as a read-only deploy key (GitLab only - auto-detects from URL)
      - Create an ArgoCD repository secret with the private key

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

6. **GitOps Installation** (`bootstrap_argocd`)
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
│ 1. Check if argocd-ssh-public-key ConfigMap exists         │
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
         │ 3. If 'gitlab' detected in host:             │
         │    - GET /api/v4/projects/{path}/deploy_keys │
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

## References
- [Cilium Documentation](https://docs.cilium.io/)
- [kubeadm Cluster Creation](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/)
- [Ubuntu Autoinstall](https://ubuntu.com/server/docs/install/autoinstall)
- [Proxmox API](https://pve.proxmox.com/pve-docs/api-viewer/)
- [ArgoCD Getting Started](https://argo-cd.readthedocs.io/en/stable/getting_started/)