# Harbor Container Registry

## What It Does

Harbor is a CNCF-graduated container registry that acts as a **pull-through proxy cache** for all upstream registries used by the cluster. Every image pull goes through Harbor, which downloads from the upstream on first request and serves cached copies thereafter.

## Why It's Here

- **Bandwidth savings** — Images cached locally after first pull; subsequent pulls never leave the network
- **Availability** — Cluster continues pulling cached images even when upstream registries are unreachable
- **Vulnerability scanning** — Trivy scans all proxied images automatically
- **Unified identity** — Keycloak OIDC authentication for the Harbor UI/API
- **Audit trail** — Centralized view of all images consumed by the cluster

## How It's Configured

### Deployment

- **Delivery**: ArgoCD Application (sync-wave 2) via Helm chart `harbor` from `helm.goharbor.io`
- **Namespace**: `harbor`
- **Storage**: All PVCs use `rook-ceph-block` StorageClass (registry 50Gi, database 5Gi, redis 1Gi, jobservice 1Gi, trivy 5Gi)
- **TLS**: Ingress with `cert-manager.io/cluster-issuer: homelab-ca-issuer` annotation
- **FQDN**: `harbor.k8s.local`
- **Internal TLS**: Disabled (single-replica homelab, ingress handles TLS termination)

### Proxy Cache Projects

Created automatically by the bootstrap Job after Harbor is up:

| Project | Upstream Registry | Harbor Adapter Type | Used By |
|---------|------------------|--------------------|---------|
| `dockerhub-cache` | `hub.docker.com` | `docker-hub` | alertmanager, grafana, prometheus, synapse, postgres, busybox, alpine, otel-collector, node-exporter, alertmanager-matrix-bridge |
| `quay-cache` | `quay.io` | `quay` | thanos (×5), keycloak |
| `k8s-registry-cache` | `registry.k8s.io` | `docker-registry` | kube-state-metrics, metrics-server |
| `nvcr-cache` | `nvcr.io` | `docker-registry` | dcgm-exporter |

> **Adapter types matter.** Harbor's API requires the correct adapter type per upstream (e.g., `docker-hub` for Docker Hub, `quay` for Quay.io). Using the generic `docker-registry` type for registries that have a dedicated adapter will return HTTP 400.

### Image Reference Format

Most image references in `argocd_applications/` are rewritten to pull through Harbor. **Exception:** images involved in bootstrapping Harbor itself (e.g., the bootstrap Job's alpine image, the Rook Ceph image) use direct upstream references to avoid a circular dependency — Harbor must be running before it can proxy pulls.

```
# Docker Hub (library/ namespace for official images)
harbor.k8s.local/dockerhub-cache/library/alpine:3.19
harbor.k8s.local/dockerhub-cache/prom/alertmanager:v0.27.0

# Quay.io
harbor.k8s.local/quay-cache/thanos/thanos:v0.36.1

# Kubernetes registry
harbor.k8s.local/k8s-registry-cache/metrics-server/metrics-server:v0.7.2

# NVIDIA registry
harbor.k8s.local/nvcr-cache/nvidia/k8s/dcgm-exporter:3.3.5-3.4.1-ubuntu22.04
```

### Artifact Indexing (Harbor Bug #21454 Workaround)

Multi-arch images pulled through proxy cache repos don't register artifact metadata in Harbor's database — repos show pull counts but 0 artifacts, and the UI shows nothing to scan. This is [Harbor bug #21454](https://github.com/goharbor/harbor/issues/21454).

**Workaround**: The `harbor-artifact-indexer` CronJob (hourly) discovers all Harbor-proxied images running in the cluster and forces Harbor to index them using a two-step manifest approach:

1. Request the manifest list (get all platform digests)
2. Extract the `amd64/linux` platform digest
3. Request that specific manifest by digest — this triggers Harbor to register the artifact

The CronJob also triggers **SBOM generation** (Trivy) and **vulnerability scans** for any artifact that is missing them.

### Vulnerability Scanning & Maintenance Schedules

Configured by the bootstrap Job on first deploy:

| Schedule | Cron | Description |
|----------|------|-------------|
| Scan-all | `0 0 3 * * *` (daily 03:00 UTC) | Trivy vulnerability scan of all artifacts |
| Garbage collection | `0 0 2 * * 0` (weekly Sunday 02:00 UTC) | Removes untagged blobs, 1 worker |
| Tag retention | `0 0 0 * * *` (daily midnight) | Retains tags pulled within last 90 days |
| Artifact indexer | `0 * * * *` (hourly) | Discovers and indexes proxy cache artifacts |

### Tag Retention Policy

Each proxy cache project has a 90-day retention policy: tags that haven't been pulled in 90 days are eligible for deletion. The bootstrap Job updates the default retention policy (which Harbor creates automatically for proxy cache projects with a 7-day default) to 90 days.

### CRI-O Registry Mirrors

The `distribute_pki` Ansible role configures CRI-O on all nodes to use Harbor as a mirror for all upstream registries. This is configured at `/etc/containers/registries.conf.d/harbor-mirror.conf` and catches any images not explicitly rewritten in manifests (e.g., kubeadm system images).

When Harbor is unavailable (e.g., during initial bootstrap before Harbor deploys), CRI-O falls back to pulling directly from the upstream registry.

### Authentication

- **OIDC (primary)**: Keycloak is the sole human auth method, pre-configured at deploy time via `CONFIG_OVERWRITE_JSON` in the Harbor core container — no manual admin login needed
  - Client ID: `harbor` (registered in `homelab-realm.json`)
  - Client secret: `harbor-keycloak-secret`
  - Admin group: `cluster-admins` (Keycloak realm role → Harbor admin privilege)
  - Auto-onboard: enabled
  - Username claim: `preferred_username`
- **Admin account**: Local `admin` user exists (Harbor architectural requirement) but the password is auto-generated by Ansible (`bootstrap_harbor_secret` role), stored in a K8s Secret (`harbor-admin-password`), and never exposed to humans. Used only by the bootstrap Job and artifact indexer CronJob for headless API calls.

## Integration Points

- **cert-manager**: Issues TLS certificate via `homelab-ca-issuer` ClusterIssuer
- **trust-manager**: `homelab-ca-bundle` ConfigMap provides CA trust for pods communicating with Harbor
- **Rook-Ceph**: `rook-ceph-block` StorageClass for all persistent volumes
- **Keycloak**: OIDC authentication (client registered in homelab realm)
- **CRI-O**: Registry mirror configuration on all nodes
- **PKI chain**: Nodes trust Harbor's TLS cert via root CA installed by `distribute_pki` role

## Troubleshooting

### Harbor not reachable

```bash
# Check Harbor pods
kubectl get pods -n harbor

# Check Ingress
kubectl get ingress -n harbor

# Check TLS certificate
kubectl get certificate -n harbor

# Check PVC status (needs rook-ceph-block)
kubectl get pvc -n harbor
```

### Images not pulling through proxy cache

```bash
# Verify CRI-O registry config on node
ssh <node> cat /etc/containers/registries.conf.d/harbor-mirror.conf

# Check if proxy cache project exists (read admin password from K8s Secret)
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/projects | jq '.[].name'

# Test proxy pull manually
crictl pull harbor.k8s.local/dockerhub-cache/library/alpine:latest
```

### OIDC not working

```bash
# Check if Keycloak is up
curl -sk https://keycloak.k8s.local/realms/homelab/.well-known/openid-configuration

# OIDC is configured via CONFIG_OVERWRITE_JSON env var on the core container.
# To verify the current auth config:
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/configurations | jq '{auth_mode, oidc_endpoint, oidc_client_id, oidc_admin_group}'

# Note: CONFIG_OVERWRITE_JSON is immutable at runtime — changes require
# updating the Helm values and restarting the core pod.
```

### Bootstrap Job failed

```bash
# Check job logs
kubectl logs job/harbor-bootstrap -n harbor

# Common issues:
# - Harbor not ready yet → Job retries automatically
# - CA trust issue → Check homelab-ca-bundle ConfigMap in harbor namespace
# - Admin password Secret missing → Verify bootstrap_harbor_secret role ran
# - Registry endpoint 400 → Wrong adapter type (must use docker-hub, quay, etc.)
# - Image pull error on bootstrap pod → Must use direct upstream image,
#   not harbor.k8s.local (circular dependency)
```

### Artifact indexer not populating repos

```bash
# Check CronJob status
kubectl get cronjob harbor-artifact-indexer -n harbor

# Manual test run
kubectl create job --from=cronjob/harbor-artifact-indexer harbor-indexer-manual -n harbor
kubectl logs -f job/harbor-indexer-manual -n harbor

# Check artifact count for a repo
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/projects/dockerhub-cache/repositories | jq '.[] | {name, artifact_count, pull_count}'

# Common issues:
# - ClusterRole missing → kubectl apply -f argocd_applications/infrastructure/harbor/rbac.yaml
# - CA trust issue → Check homelab-ca-bundle ConfigMap mount
# - Pod discovery returns 0 → Check ClusterRoleBinding for harbor-bootstrap SA
```

## Links

- [Harbor Documentation](https://goharbor.io/docs/)
- [Harbor Helm Chart](https://github.com/goharbor/harbor-helm)
- [Proxy Cache Documentation](https://goharbor.io/docs/latest/administration/configure-proxy-cache/)
