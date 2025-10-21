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
   ```

3. **Run automation**
   ```bash
   python3 setup-clusters.py
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
**`bootstrap_argocd`** - Installs ArgoCD for declarative cluster management
- Creates `argocd` namespace
- Installs ArgoCD controllers and UI
- Installs ArgoCD CLI for local management

**`bootstrap_prometheus`** - Monitoring stack deployment (optional)
- Deploys Prometheus server with manifests
- Configures ingress for metrics access

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