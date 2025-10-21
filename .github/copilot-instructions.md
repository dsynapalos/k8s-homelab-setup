# Kubernetes Cluster Automation Project

This project automates end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured K8s clusters with Cilium networking and ArgoCD.

## Architecture Overview

**Execution Flow**: `setup-clusters.py` → `setup_cluster.yaml` → role-based automation
- **Infrastructure**: Proxmox VM provisioning with Ubuntu autoinstall ISO modification
- **K8s Stack**: kubeadm + CRI-O runtime + Cilium CNI + ArgoCD GitOps
- **Networking**: Cilium with WireGuard encryption, L2 load balancer announcements

## Key Components

### Main Entry Points
- `setup-clusters.py`: Python wrapper using ansible-runner for execution
- `setup_cluster.yaml`: Main playbook orchestrating 6-phase deployment
- `inventory/k8s.yaml`: Environment-driven inventory with extensive variable templating

### Role Structure Pattern
```
roles/
├── provision_infra/     # Proxmox VM creation, ISO handling
├── setup_cluster_master/ # kubeadm init, kubeconfig generation
├── setup_cluster_node/   # kubeadm join workers
├── bootstrap_cillium/    # Helm-based Cilium deployment
└── bootstrap_argocd/     # ArgoCD installation
```

### Environment Configuration
- **Required**: Copy `example.env` to `.env` with actual values
- **Critical vars**: IP addresses, SSH keys, Proxmox credentials, version specifications
- **Pattern**: All inventory uses `{{ lookup("env", "VAR_NAME") }}` for configuration

## Development Workflows

### Initial Setup
```bash
sudo chmod +x init.sh && ./init.sh  # Python venv + Ansible dependencies
cp example.env .env                  # Configure environment variables
python3 setup-clusters.py           # Full automation run (~17 min)
```

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

## Critical Conventions

### Inventory Patterns
- **Host groups**: `k8s-control`, `k8s-nodes`, `proxmox` with inherited `k8s` parent
- **Variable precedence**: Environment variables override defaults via lookup functions
- **Connection vars**: SSH keys and users configured per environment

### Kubernetes Operations
- **Kubeconfig**: Generated as `/etc/kubernetes/new_cluster_admin.conf` on control plane
- **Module usage**: `kubernetes.core.k8s` and `kubernetes.core.helm` for declarative operations
- **Resource management**: YAML definitions embedded in task files, not separate manifests

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

When modifying roles, maintain the delegation patterns for K8s operations and preserve environment variable templating for flexibility across deployments.