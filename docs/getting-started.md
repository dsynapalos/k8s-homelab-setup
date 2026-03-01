# Getting Started

> **This is a homelab/learning project**, not a production-ready deployment. It's designed for studying Kubernetes, virtualization, Linux, and general homelab experimentation. Expect single-replica components, relaxed security defaults, and configuration tuned for a small number of Proxmox hosts.

## What This Guide Covers

Everything you need to go from a fresh checkout to a running Kubernetes cluster. By the end, you'll have VMs provisioned on Proxmox, a kubeadm cluster initialized with Cilium networking, and ArgoCD managing your applications via GitOps.

## Prerequisites

You need three things before starting:

1. **One or more Proxmox hosts** (or standalone Ubuntu VMs if you're skipping VM provisioning)
2. **A control machine** running Ubuntu, WSL, or any Linux with Python 3 and SSH
3. **SSH access** to each Proxmox host and the network where VMs will live

## Installation

### 1. Initialize the Environment

The init script creates a Python virtual environment (`.venv`) and installs all required dependencies:

```bash
sudo chmod +x init.sh && ./init.sh
```

This installs system packages (`python3`, `python3-pip`, `python3-venv`) via apt, then pip-installs into the venv:
- **`ansible`** + **`ansible-runner`** — playbook execution engine
- **`python-dotenv`** — loads `.env` into the environment for Ansible
- **`kubernetes`** — Python client used by the `kubernetes.core` Ansible module

### 2. Configure Environment Variables

Copy the example and fill in your values:

```bash
cp example.env .env
```

The `.env` file is the single source of truth for all configuration. At minimum, you need:

```bash
# Node IPs and SSH access
K8S_CONTROL_1_IP=192.168.1.10
K8S_NODE_1_IP=192.168.1.11
K8S_SSH_USER=k8s
K8S_SSH_KEY=~/.ssh/id_rsa
K8S_SSH_PUB_KEY=~/.ssh/id_rsa.pub

# Kubernetes and runtime versions
K8S_VERSION=1.35
CRIO_VERSION=1.34
CILIUM_VERSION=1.18.7
CILIUM_LOADBALANCER_IPPOOL=192.168.1.193/27

# Proxmox connection (one entry per host, shared credentials)
PROXMOX_API_USER=root@pam
PROXMOX_API_PASSWORD=yourpassword
PROXMOX_API_HOST_1=192.168.1.1      # Primary Proxmox host
PROXMOX_API_HOST_2=192.168.1.2      # Secondary Proxmox host (optional)
PROXMOX_LOCAL_STORAGE=local
PROXMOX_NODE_1=pve                   # Primary Proxmox node name
PROXMOX_NODE_2=pve2                  # Secondary Proxmox node name (optional)

# API Server HA (kube-vip)
K8S_VIP=192.168.1.210               # Floating VIP for API server HA, must be outside DHCP range and LB pool
KUBE_VIP_VERSION=0.8.7

# VM networking
VM_GATEWAY=192.168.1.1
VM_NAMESERVER=192.168.1.1
VM_NET_BRIDGE=vmbr0
VM_NET_MODEL=virtio

# VM resources — primary nodes (on PROXMOX_NODE_1)
K8S_CONTROL_1_MEM_MB=4096
K8S_CONTROL_1_DISK_GB=32
K8S_CONTROL_1_CPU=2
K8S_NODE_1_MEM_MB=8192
K8S_NODE_1_DISK_GB=64
K8S_NODE_1_CPU=4

# VM resources — secondary nodes (on PROXMOX_NODE_2, optional)
K8S_CONTROL_2_IP=192.168.1.12
K8S_CONTROL_2_MEM_MB=8192
K8S_CONTROL_2_DISK_GB=25
K8S_CONTROL_2_CPU=1
K8S_NODE_2_IP=192.168.1.13
K8S_NODE_2_MEM_MB=16384
K8S_NODE_2_DISK_GB=125
K8S_NODE_2_CPU=4

# VM CPU emulation
VM_CPU_TYPE=host              # 'host' exposes full CPU features (required for Istio); 'kvm64' for baseline x86-64

# Ansible behavior
ANSIBLE_HOST_KEY_CHECKING=False
ANSIBLE_VERBOSITY=0           # 0 = minimal output, 1-4 = increasing debug detail

# Ubuntu ISO
UBUNTU_RELEASE_VERSION=24.04.3

# Storage (Rook-Ceph is enabled by default in example.env)
ENABLE_ROOK=true
```

The entry scripts (`setup-clusters.py`, `setup-applications.py`, `cleanup-clusters.py`) use `python-dotenv` to load `.env` into the environment before launching Ansible Runner. This means every Ansible variable using `{{ lookup("env", "VAR") }}` reads from this file.

For optional features (GPU passthrough, Istio mesh, CephFS, Rook-Ceph, ArgoCD), see [Configuration](infrastructure/configuration.md).

### 3. Run the Automation

**Full cluster build** — provisions VMs, installs Kubernetes, deploys everything:

```bash
python3 setup-clusters.py    # ~26 minutes
```

**Application-only deployment** — uploads ArgoCD manifests to an existing cluster:

```bash
python3 setup-applications.py    # Seconds
```

**Teardown** — destroys VMs, wipes storage, removes kubeconfig:

```bash
python3 cleanup-clusters.py
```

For details on what each entry point does, see [Ansible Pipeline](cicd/ansible-pipeline.md).

### 4. Configure DNS for Ingress Access

The cluster exposes services via Cilium Ingress using `*.k8s.local` hostnames. These are not real DNS names — you need to add them to your workstation's `/etc/hosts` file, pointing to a LoadBalancer IP from your `CILIUM_LOADBALANCER_IPPOOL` range:

```bash
# Add to /etc/hosts (replace IP with one from your CILIUM_LOADBALANCER_IPPOOL)
192.168.1.193  argocd.k8s.local grafana.k8s.local prometheus.k8s.local thanos.k8s.local hubble.k8s.local matrix.k8s.local
```

To find the actual LoadBalancer IP assigned to the Ingress:

```bash
kubectl get ingress -A
```

### 5. Trust the Homelab Root CA

At the end of the `setup-clusters.py` run, the final play prints the root CA certificate and copy-paste ready import scripts for Windows (PowerShell), Linux, and macOS. Copy the script for your OS and paste it into a terminal to import the certificate. Restart your browser afterwards.

If you missed the output, re-run the utility script to display the instructions again:

```bash
python3 expose-ca.py
```

Or extract the CA manually:

```bash
kubectl get secret -n cert-manager homelab-ca-secret -o jsonpath='{.data.ca\.crt}' | base64 -d > homelab-ca.crt
sudo cp homelab-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
```

### 6. Log In to ArgoCD

ArgoCD generates a random admin password on first install. Retrieve it with:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

Then open `https://argocd.k8s.local` and log in with username `admin` and the password above.

### 7. Verify the Cluster

```bash
kubectl get nodes
cilium status
hubble observe
```

## What Happens During a Full Run

The first run takes approximately 26 minutes, most of which is spent downloading and remastering the Ubuntu ISO (~3 GB) and waiting for VMs to boot from the autoinstall media. Subsequent runs are faster because the ISO is cached on Proxmox.

| Phase | Duration | What Happens |
|-------|----------|-------------|
| Local setup | ~2 min | Installs kubectl, Helm, Cilium CLI on your machine |
| VM provisioning | ~8 min | Creates VMs, boots from autoinstall ISO, configures networking |
| Kubernetes init | ~3 min | kubeadm init/join, Cilium CNI, node labeling |
| Platform services | ~4 min | ArgoCD, storage drivers, GPU plugin, app-of-apps hierarchy |

### Subsequent Runs and Partial Execution

| Task | Entry Point | Duration | Infra Changes | Use Case |
|------|-------------|----------|---------------|----------|
| Full cluster | `setup-clusters.py` | ~26 min | VM create/destroy | New deployment, add nodes |
| Applications only | `setup-applications.py` | Seconds | None | App updates, GitOps changes |
| Cluster reset | `setup-clusters.py` | ~26 min | VM recreate | Major version upgrades |
| Teardown | `cleanup-clusters.py` | ~2 min | VM destroy, storage wipe | Starting over |

The `cleanup-clusters.py` script destroys all VMs on every configured Proxmox host, removes secondary storage pools and LVM structures per cluster, wipes disk signatures, and deletes the local kubeconfig. It iterates over the `proxmox_cluster` map so each host is cleaned independently. It uses the same Ansible Runner pattern but runs `cleanup_cluster.yaml`.

## Next Steps

- [Configuration](infrastructure/configuration.md) — enable optional features
- [Architecture](infrastructure/architecture.md) — understand the role structure
- [Ansible Pipeline](cicd/ansible-pipeline.md) — how the playbooks and roles work
- [Troubleshooting](infrastructure/troubleshooting.md) — when things go wrong
