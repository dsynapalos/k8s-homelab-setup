# Matrix Synapse

## What It Does

Matrix Synapse is a self-hosted Matrix homeserver that provides the messaging infrastructure for cluster notifications. It hosts the `#alerts` chat room where Alertmanager alert notifications and Harbor vulnerability scan reports appear, accessible from any Matrix client like Element on mobile or desktop. Authentication is handled via Keycloak OIDC — users sign in with their cluster identity.

## Why It's Here

The alerting stack needs a destination that supports push notifications to your phone. Rather than relying on email (slow, easy to miss) or Slack/Discord (external dependency), Matrix provides a self-hosted, open-protocol solution. You install the Element app, join the `#alerts` room, and get instant push notifications when GPU temperatures spike, pods crash, or Critical vulnerabilities are found in cluster images.

## How It's Configured

**Deployment**: Deployment (`matrixdotorg/synapse:v1.147.1`) backed by a CloudNativePG-managed PostgreSQL database. Uses `rook-ceph-block` PVC (10Gi) for Synapse data (signing keys, media, generated config). The Deployment uses `Recreate` strategy since the PVC is RWO.

### CloudNativePG Database

PostgreSQL is managed by the CloudNativePG operator via a `Cluster` CRD in `database.yaml`. The operator handles:

- **Provisioning**: Creates a single PostgreSQL instance with the `synapse` database, `C` locale (required by Synapse), and owner user
- **Credentials**: Auto-generates the `matrix-db-app` Secret containing `host`, `port`, `dbname`, `username`, `password`, `uri`, and `jdbc-uri`
- **Persistent storage**: Uses `rook-ceph-block` StorageClass (2Gi) for durable data (requires `ENABLE_ROOK=true`)
- **TLS**: Server certificate issued by cert-manager's `homelab-ca-issuer`, covering the `-rw`, `-ro`, and `-r` service DNS names
- **Metrics**: Prometheus annotations propagated to pods/services via `inheritedMetadata` for OTel Collector scraping (port 9187, path `/metrics`)

### Synapse Configuration

Synapse loads three config files via multiple `--config-path` arguments (later files override earlier ones):

| Config file | Source | Purpose |
|---|---|---|
| `/data/homeserver.yaml` | Generated on first boot by `/start.py generate` | Base Synapse settings (server name, signing keys, `registration_shared_secret`) |
| `/data/database-override.yaml` | Written by init container every startup | PostgreSQL connection (credentials from CNPG `matrix-db-app` Secret) |
| `/config/oidc.yaml` | Mounted from `matrix-oidc-config` ConfigMap | Keycloak OIDC provider configuration |

The init container writes the database override on every startup so credential rotations from CNPG are picked up automatically. The OIDC config is managed declaratively via Kustomize's `configMapGenerator` — changes to `synapse-oidc.yaml` trigger a ConfigMap hash change → StatefulSet rollout.

### OIDC Integration (Keycloak)

Synapse uses its native `oidc_providers` config to authenticate users via Keycloak:

- **Client**: `synapse` in the `homelab` realm (defined in `homelab-realm.json`)
- **Redirect URI**: `https://matrix.k8s.local/_synapse/client/oidc/callback`
- **Back-channel logout**: Enabled — Keycloak notifies Synapse when users log out
- **User mapping**: `preferred_username` → Matrix localpart, `name` → display name
- **Endpoints**: All OIDC endpoints use the external Keycloak URL (`https://keycloak.k8s.local/...`). In-cluster resolution works via a CoreDNS rewrite rule (see [Networking](../../infrastructure/networking.md#coredns-rewrite)) that maps `*.k8s.local` to the Cilium Ingress ClusterIP. TLS verification uses the homelab CA certificate from [trust-manager](../../security/trust-manager.md) (mounted via `homelab-ca-bundle` ConfigMap with `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` env vars).

Public registration is disabled. Users authenticate via Keycloak SSO. The `alertbot` user is created by the bootstrap job using the admin registration API (`registration_shared_secret`), which operates independently of the public registration setting.

**Server name**: `matrix.k8s.local`

**Bootstrap job** (sync-wave 3, PostSync hook):
- Waits for Synapse to be healthy
- Checks if `matrix-bot` Secret already exists (idempotent)
- Extracts `registration_shared_secret` from `homeserver.yaml` inside the running pod (requires `apps/deployments` get permission in RBAC to resolve `deploy/matrix`)
- Registers a bot user (`alertbot-TIMESTAMP`) using HMAC-SHA1 admin registration
- Creates public `#alerts` room with world-readable history
- Saves all credentials to `matrix-bot` Secret (used by [matrix-bridge](matrix-bridge.md))

**Access**: Exposed via Cilium Ingress at `https://matrix.k8s.local` with cert-manager TLS. Synapse listens on port 8008 internally; TLS is terminated at the ingress.

### Sync Wave Ordering

| Wave | Resource | Purpose |
|------|----------|---------|
| 0 | `matrix-db-server` Certificate | cert-manager issues TLS cert for PostgreSQL server |
| 1 | `matrix-db` CNPG Cluster | Provisions PostgreSQL, generates `matrix-db-app` Secret |
| 2 | Deployment | Synapse starts (reads database creds from CNPG Secret, OIDC config from ConfigMap) |
| 3 | `matrix-bootstrap` Job (PostSync) | Creates bot user, `#alerts` room, `matrix-bot` Secret |

The ArgoCD Application lives in the services tier of the app-of-app-of-apps hierarchy (`argocd_applications/cluster-apps/services/matrix.yaml`), which deploys only after all platform-tier apps (including CNPG operator and cert-manager) are Healthy. Uses `ServerSideApply=true` and retry settings to handle CRD availability timing.

### Secret Management

All database credentials are auto-generated by CloudNativePG — no manual Secret creation or init Jobs needed.

| Secret | Source | Purpose |
|--------|--------|---------|
| `matrix-db-app` | CNPG operator | Database connection credentials for Synapse |
| `matrix-db-server-tls` | cert-manager | PostgreSQL server TLS certificate |
| `matrix-bot` | Bootstrap Job | Bot user credentials for matrix-bridge |

## Connecting as a User

1. Open Element (or any Matrix client)
2. Set homeserver to `https://matrix.k8s.local`
3. Click "Sign in" → SSO button → authenticate via Keycloak
4. Join `#alerts:matrix.k8s.local` — this is the room where Alertmanager notifications and Harbor CVE reports are posted

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Keycloak](../../security/keycloak.md) | OIDC provider — `synapse` client in `homelab` realm |
| [Matrix Bridge](matrix-bridge.md) | Uses `matrix-bot` Secret to post alert and vulnerability scan messages |
| [CloudNativePG](../../storage/cloudnative-pg.md) | Manages PostgreSQL database lifecycle and credentials |
| [trust-manager](../../security/trust-manager.md) | Distributes homelab CA certificate for Keycloak OIDC TLS verification |
| [Rook-Ceph Block Storage](../storage/rook-cluster.md) | Persistent storage for both Synapse data PVC and CNPG database PVC |
| [cert-manager](../../security/cert-manager.md) | Issues TLS certs for Ingress and PostgreSQL server |

## Troubleshooting

```bash
# Check Synapse pod status
kubectl get pods -n monitoring -l app=matrix
kubectl logs -n monitoring -l app=matrix -c synapse --tail=30

# Check init container logs (config generation + database override)
kubectl logs -n monitoring -l app=matrix -c generate-config

# Check CNPG database cluster status
kubectl get clusters.postgresql.cnpg.io -n monitoring
kubectl get pods -n monitoring -l cnpg.io/cluster=matrix-db

# Check CNPG credentials Secret
kubectl get secret matrix-db-app -n monitoring

# Check bootstrap job status
kubectl get jobs -n monitoring -l app=matrix-bootstrap
kubectl logs -n monitoring -l job-name=matrix-bootstrap --tail=50

# Check matrix-bot Secret (created by bootstrap)
kubectl get secret matrix-bot -n monitoring

# Test Synapse health
kubectl exec -n monitoring deploy/matrix -c synapse -- curl -s http://localhost:8008/_matrix/client/versions

# Verify OIDC config is loaded
kubectl exec -n monitoring deploy/matrix -c synapse -- cat /config/oidc.yaml
```

**OIDC login fails with TLS errors**: Synapse uses external HTTPS URLs for all OIDC endpoints, resolved internally via CoreDNS rewrite. Verify the `homelab-ca-bundle` ConfigMap is mounted and the CA certificate is valid: `kubectl exec -n monitoring deploy/matrix -c synapse -- cat /etc/ssl/certs/homelab/ca-certificates.crt | openssl x509 -text -noout`. Also verify CoreDNS can resolve: `kubectl exec -n monitoring deploy/matrix -c synapse -- curl -s https://keycloak.k8s.local/realms/homelab/.well-known/openid-configuration`.

**Bootstrap job fails with RBAC error**: If the job pod logs show `Error from server (Forbidden): deployments.apps ... is forbidden`, the job's Role is missing `apps/deployments` get permission. This permission is required because the bootstrap script uses `kubectl exec deploy/matrix` to extract the registration shared secret. Verify the Role grants `get` on `deployments` in the `apps` API group.

**Bootstrap job fails with `M_USER_IN_USE`**: The bot user already exists from a previous run. Delete the stale resources and re-sync:
```bash
kubectl delete secret matrix-bot -n monitoring
kubectl delete job matrix-bootstrap -n monitoring
```
Then re-sync the Matrix application in ArgoCD.

**CNPG cluster not ready**: Check the operator logs: `kubectl logs -n cnpg-system deploy/cnpg-controller-manager --tail=30`. Ensure cert-manager has issued the `matrix-db-server-tls` certificate: `kubectl get certificate matrix-db-server -n monitoring`.

**Existing deployment migration**: If upgrading from the old StatefulSet + PostgreSQL sidecar setup, delete the old StatefulSet and its PVC (`kubectl delete statefulset matrix -n monitoring && kubectl delete pvc data-matrix-0 -n monitoring`) before syncing. The new Deployment creates a standalone `matrix-data` PVC. The old sidecar used `emptyDir` storage, so no database data was persisted across restarts anyway.

**Element client SSO not showing**: Ensure Synapse can reach the Keycloak JWKS endpoint. Check that the `synapse-oidc.yaml` ConfigMap is correctly mounted at `/config/oidc.yaml`.

## Links

- [Matrix Synapse Documentation](https://element-hq.github.io/synapse/latest/)
- [Synapse OIDC Configuration](https://element-hq.github.io/synapse/latest/openid.html)
- [CloudNativePG Documentation](https://cloudnative-pg.io/docs/)
- [Matrix Specification](https://spec.matrix.org/)
- [Element Client](https://element.io/)
