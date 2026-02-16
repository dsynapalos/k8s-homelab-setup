# CloudNativePG

## What It Does

CloudNativePG is a Kubernetes operator that manages the full lifecycle of PostgreSQL database clusters. It handles provisioning, automated failover, backup/restore, certificate management, and monitoring — all through a declarative `Cluster` CRD. Unlike raw StatefulSets or sidecar patterns, CloudNativePG manages its own PVCs directly (no StatefulSet dependency) and uses PostgreSQL's native streaming replication for high availability.

In this cluster, CloudNativePG runs as an operator in the `cnpg-system` namespace. Applications that need PostgreSQL (e.g., Keycloak) create a `Cluster` CR in their own namespace, and the operator provisions and manages the database instance automatically. Database credentials are auto-generated and stored in Kubernetes Secrets following a predictable naming convention (`{cluster-name}-app`).

## Why It's Here

Before CloudNativePG, PostgreSQL was deployed as a raw sidecar container with `emptyDir` volumes — data was lost on every pod reschedule, and there was no automated backup, TLS, or monitoring. CloudNativePG provides:

- **Persistent storage** — PVCs backed by Rook-Ceph block storage survive pod restarts
- **cert-manager integration** — PostgreSQL server TLS certificates managed by the homelab CA chain
- **Built-in metrics** — Prometheus-compatible exporter on port 9187, auto-discovered by OTel Collector
- **Declarative database management** — database, owner, and credentials defined in YAML
- **Automatic credential management** — app secrets with connection strings generated automatically
- **CNCF Sandbox project** — community-maintained, cloud-native PostgreSQL standard

## How It's Configured

### Installation

The operator is deployed from the upstream static install manifest via Kustomize:

```
argocd_applications/storage/cloudnative-pg/
├── kustomization.yaml                # References upstream cnpg manifest + monitoring patch
└── operator-monitoring-patch.yaml    # Adds Prometheus annotations for OTel scraping
```

The ArgoCD Application uses `ServerSideApply=true` (required for large CRDs) and retry policy (10 retries, exponential backoff) because the operator webhook may take a moment to become ready.

### Sync Wave Ordering

| Wave | Resource | Purpose |
|------|----------|---------|
| 1 | CloudNativePG operator | CRDs, controller, webhook — must be running before any `Cluster` CR |

The operator deploys at sync wave 1 in the platform tier of the app-of-app-of-apps hierarchy, alongside cert-manager and trust-manager. Applications that create CNPG `Cluster` resources (Keycloak, Matrix) deploy at later sync waves or in the services tier.

### Version Management

The CloudNativePG version is pinned in the Kustomize resource URL (`kustomization.yaml`). To upgrade, update the version in the URL:

```yaml
resources:
  - https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.28.1/cnpg-1.28.1.yaml
```

### Operator Metrics

The operator deployment is patched with Prometheus annotations for OTel Collector auto-discovery:

| Annotation | Value | Purpose |
|------------|-------|---------|
| `prometheus.io/scrape` | `true` | Enables annotation-based discovery |
| `prometheus.io/port` | `8080` | Operator metrics port |
| `prometheus.io/path` | `/metrics` | Metrics endpoint path |

### Creating a PostgreSQL Cluster

Applications create a `Cluster` CR in their namespace. The operator provisions the database, generates credentials, and creates services automatically:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: myapp-db
  namespace: myapp
spec:
  instances: 1
  storage:
    storageClass: rook-ceph-block
    size: 2Gi
  bootstrap:
    initdb:
      database: myapp
      owner: myapp
```

The operator creates these resources automatically:

| Resource | Name Pattern | Purpose |
|----------|-------------|---------|
| Secret | `{name}-app` | App credentials: `username`, `password`, `host`, `port`, `dbname`, `uri`, `jdbc-uri` |
| Service | `{name}-rw` | Read-write endpoint (points to primary) |
| Service | `{name}-ro` | Read-only endpoint (points to replicas) |
| Service | `{name}-r` | Any instance endpoint |
| PVC | `{name}-{n}` | Persistent volume for each instance |

### cert-manager Integration

CNPG Cluster instances can use cert-manager for PostgreSQL server TLS by referencing a cert-manager `Certificate` resource:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: myapp-db-server
spec:
  secretName: myapp-db-server-tls
  issuerRef:
    name: homelab-ca-issuer
    kind: ClusterIssuer
  usages: [server auth]
  dnsNames:
    - myapp-db-rw.myapp.svc
    - myapp-db-rw.myapp.svc.cluster.local
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
spec:
  certificates:
    serverTLSSecret: myapp-db-server-tls
    serverCASecret: myapp-db-server-tls
```

The same cert-manager Secret is used for both `serverTLSSecret` (reads `tls.crt` and `tls.key`) and `serverCASecret` (reads `ca.crt`), since cert-manager includes the CA certificate in the generated Secret.

### Rook-Ceph Integration

CNPG Cluster instances use the `rook-ceph-block` StorageClass for persistent volumes. This requires Rook to be enabled (`ENABLE_ROOK=true`) and the CephCluster to be healthy:

```yaml
spec:
  storage:
    storageClass: rook-ceph-block
    size: 2Gi
```

If Rook is not enabled, change `storageClass` to match your cluster's available storage or omit it to use the default StorageClass.

### OTel Metrics Scraping

PostgreSQL instances expose metrics on port 9187 at `/metrics`. Enable auto-discovery by the OTel Collector via `inheritedMetadata` annotations on the `Cluster` CR:

```yaml
spec:
  inheritedMetadata:
    annotations:
      prometheus.io/scrape: "true"
      prometheus.io/port: "9187"
      prometheus.io/path: "/metrics"
```

These annotations propagate to all objects generated by the operator (pods, services, PVCs), enabling the OTel Collector's annotation-based pod and service scrape jobs to discover and scrape PostgreSQL metrics.

## Integration Points

| Direction | Target | Purpose |
|-----------|--------|---------|
| ← ArgoCD | CloudNativePG | Deploys and syncs operator via Application CR |
| → Kubernetes API | `Cluster` CRDs | Watches for Cluster CRs in all namespaces |
| → Rook-Ceph | Storage | PVCs provisioned via `rook-ceph-block` StorageClass |
| → cert-manager | TLS | Server certificates for PostgreSQL connections |
| → OTel Collector | Metrics | Prometheus annotations for auto-discovery |
| → Keycloak | Database | Provisions PostgreSQL for Keycloak IAM |

### Current Database Consumers

| Application | Cluster CR | Namespace | Database |
|-------------|-----------|-----------|----------|
| Keycloak | `keycloak-db` | `security` | `keycloak` |
| Matrix Synapse | `matrix-db` | `monitoring` | `synapse` |

## Troubleshooting

```bash
# Check operator pods are running
kubectl get pods -n cnpg-system

# Check operator logs
kubectl logs -n cnpg-system -l app.kubernetes.io/name=cloudnative-pg --tail=50

# List all CNPG clusters across namespaces
kubectl get clusters.postgresql.cnpg.io -A

# Check a specific cluster's status
kubectl describe cluster keycloak-db -n security

# Check cluster instances (pods)
kubectl get pods -n security -l cnpg.io/cluster=keycloak-db

# Check auto-generated secrets
kubectl get secret -n security keycloak-db-app -o yaml

# Check PVC status
kubectl get pvc -n security -l cnpg.io/cluster=keycloak-db

# Check PostgreSQL logs
kubectl logs -n security -l cnpg.io/cluster=keycloak-db --tail=50

# Verify metrics endpoint
kubectl exec -n security keycloak-db-1 -- curl -s http://localhost:9187/metrics | head -20

# Check webhook is responding
kubectl get validatingwebhookconfigurations | grep cnpg
```

**Cluster stuck in `Creating`**: Check that the StorageClass exists (`kubectl get sc rook-ceph-block`) and that Rook-Ceph is healthy. PVCs will stay `Pending` until the CSI provisioner is ready.

**Operator not starting**: Check the webhook certificate. The operator manages its own webhook TLS by default. If pods are stuck in `CrashLoopBackOff`, check logs for certificate errors.

**Connection refused from application**: Verify the CNPG services exist (`kubectl get svc -n security -l cnpg.io/cluster=keycloak-db`) and that the primary instance is running. The `-rw` service only points to a healthy primary.

**Credentials not available**: The operator creates the `{cluster}-app` Secret only after the database is fully bootstrapped. If the Secret doesn't exist, the cluster is still initializing. Check cluster status.

## Links

- [CloudNativePG Documentation](https://cloudnative-pg.io/docs/)
- [CloudNativePG Architecture](https://cloudnative-pg.io/docs/1.26/architecture/)
- [Cluster CRD API Reference](https://cloudnative-pg.io/docs/1.26/cloudnative-pg.v1/)
- [cert-manager Integration](https://cloudnative-pg.io/docs/1.26/certificates/)
- [Monitoring with Prometheus](https://cloudnative-pg.io/docs/1.26/monitoring/)
- [CNCF Landscape — CloudNativePG](https://landscape.cncf.io/?selected=cloud-native-pg)
- [GitHub Repository](https://github.com/cloudnative-pg/cloudnative-pg)
