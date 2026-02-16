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
| `dockerhub-cache` | `hub.docker.com` | `docker-hub` | alertmanager, grafana, prometheus, synapse, postgres, busybox, alpine, otel-collector, node-exporter, matrix-bridge |
| `quay-cache` | `quay.io` | `quay` | thanos (×5), keycloak, ceph |
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

### Signature Verification (Cosign)

The artifact indexer CronJob performs upstream signature verification using [Cosign](https://docs.sigstore.dev/cosign/overview/) (Sigstore, CNCF Graduated):

**Proxy cache images** — Verified against upstream Sigstore/Rekor transparency logs using keyless verification. Results are recorded as **Harbor labels** on each artifact:
- **`upstream-verified`** (green) — Valid Sigstore signature found from upstream publisher
- **`upstream-unverified`** (red) — No verifiable Sigstore signature (e.g., image signed with a different mechanism or not signed at all)

> **Note:** Harbor proxy cache projects are read-only — signatures cannot be pushed to them. Verification status is tracked via labels instead of co-signing.

**CI/CD project images** — Signed with a homelab Cosign keypair stored as K8s Secret `cosign-keypair` in the `harbor` namespace. The keypair is auto-generated on first CronJob run and reused thereafter. Signatures are pushed as OCI artifacts (cosign accessories) and uploaded without Rekor transparency log entries (`--tlog-upload=false`).

**Verification status of current images:**

| Status | Count | Examples |
|--------|-------|---------|
| Sigstore-verified | 4 | otel-collector, kube-state-metrics, metrics-server, matrix-alertmanager-receiver |
| No Sigstore signature | 9 | grafana, alpine, busybox, alertmanager, thanos, keycloak, etc. |

Most upstream images don't use Sigstore keyless signing — they may use other mechanisms (Docker Content Trust, GPG) or not be signed at all. The `upstream-unverified` label indicates absence of Sigstore signatures specifically.

### External Image Import (`cluster-images` project)

Images running in the cluster that aren't pulled through Harbor proxy cache (e.g., Cilium, CoreDNS, etcd — pulled directly during kubeadm bootstrap) are imported into a local writable project called `cluster-images` by the artifact indexer CronJob:

- **Discovery**: Queries the Kubernetes API for all pod images, splits into Harbor-proxied vs external
- **Normalization**: Resolves implicit Docker Hub references (e.g., `busybox` → `docker.io/library/busybox`)
- **Import**: Uses [crane](https://github.com/google/go-containerregistry/blob/main/cmd/crane/doc/crane.md) to copy `linux/amd64` images into `harbor.k8s.local/cluster-images/<registry>/<repo>:<tag>`
- **Auto-scan**: The project has `auto_scan: true` metadata, so Trivy scans images on push
- **Webhook**: A `SCANNING_COMPLETED` webhook policy is registered on the project, sending results to the matrix-bridge for Critical vulnerability notifications
- **Idempotent**: Skips images whose tags already exist in `cluster-images`

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
- **Matrix Bridge**: Webhook policies on each proxy cache project and the `cluster-images` project send `SCANNING_COMPLETED` events to `matrix-bridge.monitoring.svc.cluster.local:3001` for Critical vulnerability notifications

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

### Cosign verification / signing issues

```bash
# Check if Cosign keypair exists
kubectl get secret cosign-keypair -n harbor

# Check verification labels on an artifact
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" \
  "https://harbor.k8s.local/api/v2.0/labels?scope=g" | jq '.[] | {name, id}'

# Check labels on a specific artifact
curl -sk -u "admin:$HARBOR_PW" \
  "https://harbor.k8s.local/api/v2.0/projects/dockerhub-cache/repositories/library%2Falpine/artifacts?with_label=true&page_size=1" | \
  jq '.[0].labels'

# Manually verify an upstream image
cosign verify --certificate-identity-regexp='.*' \
  --certificate-oidc-issuer-regexp='.*' docker.io/otel/opentelemetry-collector-contrib:0.120.0

# Re-run verification (remove labels first to force re-check)
# Labels are idempotent — existing labels cause the CronJob to skip verification.
# To re-verify, remove labels from artifacts via Harbor UI or API, then trigger a run.

# Common issues:
# - "can not push artifact to a proxy project" → Expected; proxy cache is read-only
# - Keypair not found → Check RBAC allows secret create/update in harbor namespace
# - CA trust failure → Verify homelab-ca-bundle ConfigMap is mounted
```

## Links

- [Harbor Documentation](https://goharbor.io/docs/)
- [Harbor Helm Chart](https://github.com/goharbor/harbor-helm)
- [Proxy Cache Documentation](https://goharbor.io/docs/latest/administration/configure-proxy-cache/)
