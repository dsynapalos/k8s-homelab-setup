# ArgoCD GitOps

## What It Does

ArgoCD watches a Git repository and continuously reconciles the Kubernetes resources defined in it with what's actually running in the cluster. In this environment, it manages all application deployments — Prometheus, Grafana, Alertmanager, Matrix, Thanos, Rook-Ceph, and everything else in the `argocd_applications/` directory.

## Why It's Here

Without GitOps, deploying applications means running `kubectl apply` manually or through ad-hoc scripts. ArgoCD makes the Git repository the single source of truth:

- Push a manifest change to Git → ArgoCD detects it and syncs to the cluster
- Drift detection: if someone modifies a resource manually, ArgoCD can auto-heal it
- Audit trail: every change is a Git commit
- The `setup-applications.py` fast path only works because ArgoCD takes over lifecycle management after the initial manifest upload

## How It's Configured

### Installation

The `bootstrap_argocd` role installs ArgoCD from upstream manifests (version controlled by `ARGOCD_VERSION`), creates the `argocd` namespace, and sets up Ingress with TLS:

- **TLS termination**: cert-manager provisions a certificate for `argocd.k8s.local` via the `homelab-ca-issuer` ClusterIssuer
- **Insecure mode**: ArgoCD server runs with `server.insecure: "true"` (serves HTTP behind the TLS-terminating Cilium Ingress)
- **Access**: `https://argocd.k8s.local`

During initial bootstrap, cert-manager is not yet running (it deploys as an ArgoCD Application). ArgoCD is accessible via HTTP until cert-manager starts and provisions the TLS certificate.

An AppProject called `homelab` is created with permissive settings — all source repos, all destination namespaces, all resource types. This keeps the homelab simple; production would lock this down.

### SSH Key Management

The automation implements a fully idempotent system for Git repository authentication:

```
┌──────────────────────────────────────────────────────────────┐
│ 1. Check: does argocd-ssh-public-key ConfigMap exist?        │
└──────────────────┬───────────────────────────────────────────┘
                   │
          ┌────────┴─────────┐
          │                  │
     YES  │                  │  NO
          ▼                  ▼
┌──────────────────┐  ┌──────────────────────┐
│ Read existing    │  │ Generate 4096-bit    │
│ public key       │  │ RSA keypair          │
└────────┬─────────┘  └──────────┬───────────┘
         │                       │
         │            ┌──────────▼────────────┐
         │            │ Store public key in   │
         │            │ ConfigMap for reuse   │
         │            └──────────┬────────────┘
         └───────────────────────┘
                     │
         ┌───────────▼────────────────────────────────────┐
         │ 2. Parse REPOSITORY_SSH_URL                    │
         │    Extract host (gitlab.com) and path (user/repo) │
         │    URL-encode path: / → %2F for GitLab API     │
         └───────────┬────────────────────────────────────┘
                     │
         ┌───────────▼────────────────────────────────────┐
         │ 3. Register deploy key (GitLab only)           │
         │    GET existing keys → compare fingerprints    │
         │    POST new key only if not already registered │
         └───────────┬────────────────────────────────────┘
                     │
         ┌───────────▼────────────────────────────────────┐
         │ 4. Create argocd-repo-ssh-key Secret           │
         │    Labeled for ArgoCD auto-discovery           │
         └────────────────────────────────────────────────┘
```

**Why a ConfigMap for the public key?** Public keys aren't secrets — storing them in a ConfigMap lets the automation check on every run whether a key already exists without touching any sensitive data. The private key lives in a Kubernetes Secret with `no_log: true`.

**Key generation implementation**: Uses the `community.crypto.openssh_keypair` module with `force: false` (only generates if missing). Keys are temporarily written to `/tmp/argocd` (private) and `/tmp/argocd.pub` (public), then stored in Kubernetes resources and cleaned up.

**Task file separation**: The role's `main.yaml` orchestrates the flow and conditionally includes `manage_ssh_keys.yaml` (key generation + ConfigMap/Secret creation) only when the ConfigMap doesn't already exist.

**Why check existing keys?** GitLab's deploy key API doesn't deduplicate by content. Without checking existing keys before registration, every run would create a duplicate deploy key. The automation queries existing keys via the API and compares key content to avoid duplicates.

### Deploy Key Registration

The automation auto-detects the Git provider from the SSH URL:

- **GitLab**: Fully automated via `/api/v4/projects/{path}/deploy_keys`
  - Project path is URL-encoded (`/` → `%2F`) for the API
  - Key registered as read-only (cannot push)
  - Requires `REPOSITORY_TOKEN` with `api` scope
- **GitHub**: Prepared for future implementation

### Kubernetes Resources Created

| Resource | Namespace | Purpose |
|----------|-----------|---------|
| ConfigMap `argocd-ssh-public-key` | argocd | Stores public key for idempotent re-runs |
| Secret `argocd-repo-ssh-key` | argocd | Private key with `stringData` (type: git, url, sshPrivateKey), labeled `argocd.argoproj.io/secret-type: repository` for ArgoCD auto-discovery |
| AppProject `homelab` | argocd | Permissive project for all applications |

## Application Deployment Flow

Once ArgoCD is running, applications are deployed through the `bootstrap_applications` role:

1. Developer creates Kustomize manifests in `argocd_applications/<category>/<app>/`
2. Developer creates an ArgoCD Application manifest in `roles/bootstrap_applications/files/<app>_manifest.yaml`
3. Running `setup-applications.py` uploads all `*_manifest.yaml` files to Kubernetes
4. ArgoCD reads each Application CR, syncs from the Git repository, and manages the resources

**Implementation detail**: The role uses `kubernetes.core.k8s` with `definition: "{{ lookup('file', item) }}"` and iterates via `loop: "{{ lookup('fileglob', role_path + '/files/*_manifest.yaml', wantlist=True) }}"`. This means any file matching `*_manifest.yaml` in the role's `files/` directory is automatically applied — no explicit task needed per application.

### Application Manifest Structure

```
argocd_applications/
├── monitoring/
│   ├── prometheus/        ← Kustomize manifests
│   ├── grafana/
│   ├── alertmanager/
│   ├── matrix/
│   ├── thanos/
│   └── ...
├── storage/
│   ├── cloudnative-pg/
│   ├── rook-operator/
│   └── rook-cluster/
├── security/
│   ├── cert-manager/      ← TLS certificate automation
│   ├── trust-manager/     ← CA trust bundle distribution
│   ├── keycloak/           ← Identity & access management
│   └── argocd-oidc/        ← ArgoCD OIDC + RBAC config (patches argocd-cm)
└── infrastructure/
    └── harbor/             ← Container registry & proxy cache

roles/bootstrap_applications/files/
├── prometheus_manifest.yaml     ← ArgoCD Application CRs
├── grafana_manifest.yaml
├── cert-manager_manifest.yaml
├── cloudnative-pg_manifest.yaml
├── keycloak_manifest.yaml
├── trust-manager_manifest.yaml
├── argocd-oidc_manifest.yaml
├── harbor_manifest.yaml
├── alertmanager_manifest.yaml
├── matrix_manifest.yaml
├── thanos_manifest.yaml
└── ...
```

### Sync Wave Ordering

Applications deploy in a specific order via ArgoCD sync waves to respect dependencies:

| Wave | Applications | Why This Order |
|------|-------------|---------------|
| 1 | cert-manager (CA issuer), trust-manager (CA distribution), CloudNativePG (CRDs + operator) | Infrastructure services must be running before dependent resources |
| 2 | Harbor (registry + proxy cache) | Container registry and proxy cache — must be operational before apps with `harbor.k8s.local` image references deploy |
| 3 | Keycloak (identity provider), ArgoCD OIDC config | Keycloak after Harbor (image pulled from `harbor.k8s.local/quay-cache`). ArgoCD OIDC patches `argocd-cm` + `argocd-rbac-cm` |
| 6 | Alertmanager, Prometheus, Thanos, Grafana, Matrix, OTel Collector, kube-state-metrics, node-exporter, dcgm-exporter, metrics-server | Monitoring and application stack, after platform services are ready |
| 8 | Matrix Bridge | Reads `matrix-bot` Secret created by the Matrix bootstrap job |

## Configuration

See [Configuration](../infrastructure/configuration.md#argocd-gitops) for environment variables and setup steps.

## Troubleshooting

```bash
# Check ArgoCD pods
kubectl get pods -n argocd
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server --tail=30

# Check application sync status
kubectl get applications -n argocd
kubectl describe application <app-name> -n argocd | tail -30

# Check SSH key registration
kubectl get configmap argocd-ssh-public-key -n argocd
kubectl get secret argocd-repo-ssh-key -n argocd
```

**Application out of sync**:
- Check ArgoCD UI at `https://argocd.k8s.local`
- Force sync from the UI, or delete the application and re-run `setup-applications.py`
- Check for manifest errors in the application events tab

**SSH deploy key not registered**:
- Check ConfigMap exists: `kubectl get configmap argocd-ssh-public-key -n argocd`
- Delete ConfigMap to force regeneration: `kubectl delete configmap argocd-ssh-public-key -n argocd`
- Re-run `setup-clusters.py` to regenerate and re-register

**Repository not accessible**:
- Verify `REPOSITORY_SSH_URL` format: `git@gitlab.com:user/repo.git`
- Check `REPOSITORY_TOKEN` has `api` scope (needed for deploy key registration)
- Test SSH: `ssh -i /tmp/argocd git@gitlab.com` (should show "Welcome to GitLab")

## Links

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/en/stable/)
- [ArgoCD Application CRD](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/)
- [ArgoCD Sync Waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)
- [GitLab Deploy Keys API](https://docs.gitlab.com/ee/api/deploy_keys.html)
