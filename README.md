# Kubernetes Cluster Automation

End-to-end Kubernetes cluster provisioning on Proxmox — from VM creation to a fully configured cluster with Cilium networking, ArgoCD GitOps, and optional storage, GPU, and service mesh support.

> **This is a homelab/learning project**, not a production-ready deployment. It's designed for studying Kubernetes, virtualization, Linux, and general homelab experimentation. Expect single-replica components, relaxed security defaults, and configuration tuned for a single Proxmox host.

## What It Does

Python scripts drive all automation through [Ansible Runner](https://ansible-runner.readthedocs.io/) (no Ansible CLI required):

```bash
python3 setup-clusters.py       # Full cluster build (~26 min)
python3 setup-applications.py   # Application-only deploy (seconds)
python3 cleanup-clusters.py     # Teardown (destroys VMs, wipes storage)
python3 expose-ca.py            # Re-display root CA trust scripts
```

A single `.env` file controls everything — node IPs, versions, feature flags. Optional features are off by default and enabled with `ENABLE_*` flags.

## What Gets Built

| Layer | Components |
|-------|-----------|
| **VMs** | Ubuntu autoinstall ISO, Proxmox provisioning, static IP, SSH hardening |
| **Kubernetes** | kubeadm, CRI-O, Cilium CNI (eBPF, WireGuard encryption, L2 load balancing) |
| **GitOps** | ArgoCD with automated SSH deploy key registration |
| **Monitoring** | Prometheus, Grafana, Thanos (long-term storage), Node Exporter |
| **Alerting** | Alertmanager → Matrix Synapse → Element mobile notifications |
| **Storage** | CephFS CSI driver or Rook-Ceph in-cluster storage *(optional)* |
| **GPU** | NVIDIA PCI passthrough, LTS drivers, DCGM Exporter *(optional)* |
| **Service Mesh** | Istio Ambient mode — sidecar-less mTLS via ztunnel *(optional)* |

## Quick Start

```bash
# 1. Initialize environment (Python venv + dependencies)
sudo chmod +x init.sh && ./init.sh

# 2. Configure
cp example.env .env    # Edit with your values

# 3. Deploy
python3 setup-clusters.py
```

See **[Getting Started](docs/getting-started.md)** for full prerequisites, `.env` reference, DNS setup, and verification steps.

## Documentation

All documentation lives in [`docs/`](docs/README.md). The [Documentation Guide](docs/README.md) is the full index with a searchable catalog, conventions, and navigation for both humans and AI agents.

### Quick navigation

| I want to… | Go to |
|---|---|
| Set up from scratch | [Getting Started](docs/getting-started.md) |
| Understand the project structure | [Architecture](docs/infrastructure/architecture.md) |
| Configure features and `.env` variables | [Configuration](docs/infrastructure/configuration.md) |
| Learn how the automation pipeline works | [Ansible Pipeline](docs/cicd/ansible-pipeline.md) |
| Understand how apps are deployed via Git | [ArgoCD GitOps](docs/cicd/gitops.md) |
| Debug a problem | [Troubleshooting](docs/infrastructure/troubleshooting.md) |
| Read about a specific application | [Application Docs](docs/README.md#applications-applications) *(one doc per component)* |

### Infrastructure & platform

- [Networking](docs/infrastructure/networking.md) — Cilium CNI, Istio Ambient, ingress
- [Storage](docs/infrastructure/storage.md) — CephFS CSI and Rook-Ceph options
- [GPU Support](docs/infrastructure/gpu-support.md) — NVIDIA passthrough, drivers, monitoring