# Kubernetes Cluster Automation Project

This project automates end-to-end Kubernetes cluster provisioning using Ansible, from VM creation on Proxmox to fully configured K8s clusters with Cilium networking and ArgoCD.

## Architecture Overview

**Execution Flow**: 
- **Full cluster**: `setup-clusters.py` → `setup_cluster.yaml` → role-based automation
- **Applications only**: `setup-applications.py` → `setup_applications.yaml` → `bootstrap_argocd` + `bootstrap_applications`

- **Infrastructure**: Proxmox VM provisioning with Ubuntu autoinstall ISO modification
- **K8s Stack**: kubeadm + CRI-O runtime + Cilium CNI + ArgoCD GitOps
- **Networking**: Cilium with WireGuard encryption, L2 load balancer announcements

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

- `setup_cluster.yaml`: Main playbook orchestrating 6-phase deployment
- `setup_applications.yaml`: Application playbook with 2 phases (ArgoCD + applications)
- `inventory/k8s.yaml`: Environment-driven inventory with extensive variable templating
- `inventory/localhost.yaml`: Localhost-specific inventory for ArgoCD and application tasks

### Role Structure Pattern
```
roles/
├── provision_infra/        # Proxmox VM creation, ISO handling
├── setup_localhost/        # CLI tools installation (kubectl, helm, cilium-cli, hubble-cli)
├── setup_os/              # OS preparation (packages, firewall, CRI-O)
├── setup_cluster_master/  # kubeadm init, kubeconfig generation
├── setup_cluster_node/    # kubeadm join workers
├── bootstrap_cillium/     # Helm-based Cilium deployment, L2 announcements
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
- **Critical vars**: IP addresses, SSH keys, Proxmox credentials, version specifications, repository URLs
- **Pattern**: All inventory uses `{{ lookup("env", "VAR_NAME") }}` for configuration
- **Git Integration**: `REPOSITORY_SSH_URL` and `REPOSITORY_TOKEN` for ArgoCD deploy key automation

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

### SSH Key Management (ArgoCD)
- **Idempotency**: Public key stored in ConfigMap (`argocd-ssh-public-key`), not regenerated
- **Security**: Private key in Secret with `no_log: true`, public key in ConfigMap
- **Deploy Keys**: GitLab API integration checks existing keys before registering
- **URL Encoding**: Project paths encoded with `%2F` for GitLab API compatibility
- **Task separation**: `main.yaml` (orchestration) + `manage_ssh_keys.yaml` (generation)
- **Conditional execution**: Only include `manage_ssh_keys.yaml` when ConfigMap missing

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

### Best Practices for Modifications
- **Avoid `is defined` checks**: Ansible handles undefined variables in `when` clauses
- **Use `when` not `failed_when`**: Let tasks naturally skip when conditions not met
- **ConfigMaps for public data**: Use ConfigMaps for non-sensitive data like public keys
- **Secrets for private data**: Use Secrets with `no_log: true` for sensitive information
- **Include patterns**: Use `include_tasks` with conditionals for optional task sets
- **URL encoding**: Use `replace('/', '%2F')` for GitLab API paths, not `urlencode`

When modifying roles, maintain the delegation patterns for K8s operations and preserve environment variable templating for flexibility across deployments.