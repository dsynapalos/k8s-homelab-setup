# Thanos

## What It Does

Thanos extends Prometheus with long-term metric storage, global querying, and data compaction. In this environment, it receives metrics from Prometheus via remote write, stores them in Rook-Ceph S3-compatible object storage, and provides a unified query interface that Grafana uses as its primary datasource.

## Why It's Here

Prometheus alone has limitations for a homelab that you want to study over time:

- **Short retention**: Prometheus is configured with 200h (~8 days) of local storage on `emptyDir` — pod restarts lose all data
- **No durable storage**: Without Thanos, all historical metrics vanish when the Prometheus pod recycles
- **Single query point**: Thanos Query provides a single endpoint that merges real-time data from Prometheus with historical data from object storage

Thanos solves this by shipping metrics to Rook-Ceph's S3 bucket, compacting them over time, and serving them back through a Prometheus-compatible API.

## Architecture

> **⚠️ Prerequisite: Prometheus `remote_write` not yet configured**: Thanos Receive is deployed and listening on port 19291, but Prometheus does not currently have a `remote_write` section in its configuration. Until you add `remote_write` to `prometheus.yml` targeting `http://thanos-receive.monitoring.svc.cluster.local:19291/api/v1/receive`, Thanos will have no data. Grafana queries Thanos Query as its datasource, so dashboards will show metrics only from Prometheus's local 200h retention (served directly through Thanos Query's store fanout) until remote write is enabled.

Thanos is deployed as four distinct components:

### Thanos Receive (StatefulSet)
- Accepts remote write from Prometheus (port 19291)
- Labels data with `receive_replica` and `receive_cluster="homelab"`
- Ships TSDB blocks to S3 object storage
- Short local retention (2h) — just enough to buffer before upload
- Storage: `rook-ceph-block` PVC (10Gi)

### Thanos Store (StatefulSet)
- Gateway to historical data in S3 object storage
- Serves old metric blocks to Thanos Query via gRPC
- Caches block metadata locally
- Storage: `rook-ceph-block` PVC (5Gi)

### Thanos Query (Deployment)
- Unified PromQL query endpoint (port 9090)
- Fans out queries to both Thanos Receive (real-time) and Thanos Store (historical)
- Deduplicates by `receive_replica` and `prometheus_replica` labels
- This is what Grafana connects to as its datasource
- Exposed via Cilium Ingress at `thanos.k8s.local`

### Thanos Compactor (StatefulSet)
- Background process that compacts and downsamples old data in S3
- Retention policies: 30 days (raw), 90 days (5m resolution), 180 days (1h resolution)
- Runs with `--wait` flag (continuous operation, not one-shot)
- Storage: `rook-ceph-block` PVC (5Gi)

## Object Storage Integration

Thanos uses an `ObjectBucketClaim` to dynamically provision an S3-compatible bucket from Rook-Ceph:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: thanos-bucket
spec:
  generateBucketName: thanos-metrics
  storageClassName: rook-ceph-bucket
```

Rook provisions the bucket and populates:
- **ConfigMap** `thanos-bucket`: `BUCKET_NAME`, `BUCKET_HOST`, `BUCKET_PORT`
- **Secret** `thanos-bucket`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

Each Thanos component uses an init container to assemble `objstore.yml` from these values at startup.

## Data Flow

```
Prometheus → remote write → Thanos Receive → S3 bucket (Rook-Ceph RGW)
  (⚠️ not yet configured)       ↓                    ↓
                          Thanos Query ← Thanos Store (reads from S3)
                                ↓
                             Grafana
                                
                          Thanos Compactor → compacts/downsamples in S3
```

> The first arrow (Prometheus → Thanos Receive) requires adding `remote_write` to Prometheus's configuration. All other connections are already operational.

## Retention Policy

| Resolution | Retention | Use Case |
|-----------|-----------|----------|
| Raw (15s) | 30 days | Recent debugging, detailed analysis |
| 5-minute | 90 days | Medium-term trends |
| 1-hour | 180 days | Long-term capacity planning |

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Prometheus](prometheus.md) | Sends metrics via remote write to Thanos Receive |
| [Grafana](grafana.md) | Queries Thanos Query as its primary datasource (`uid: prometheus`) |
| [Rook-Ceph Cluster](../storage/rook-cluster.md) | S3 bucket for metric storage, block PVCs for local data |

## Troubleshooting

```bash
# Check all Thanos components
kubectl get pods -n monitoring -l app.kubernetes.io/part-of=thanos

# Check Thanos Receive (is it accepting remote writes?)
kubectl logs -n monitoring -l app=thanos-receive --tail=30

# Check Thanos Query (can it see all stores?)
kubectl port-forward -n monitoring svc/thanos-query 9090:9090
# Then open http://localhost:9090 → Status → Stores

# Check Thanos Store (can it read from S3?)
kubectl logs -n monitoring -l app=thanos-store --tail=30

# Check Thanos Compactor
kubectl logs -n monitoring -l app=thanos-compactor --tail=30

# Verify S3 bucket exists (ObjectBucketClaim)
kubectl get objectbucketclaim thanos-bucket -n monitoring
kubectl get configmap thanos-bucket -n monitoring -o yaml
kubectl get secret thanos-bucket -n monitoring

# Check PVCs
kubectl get pvc -n monitoring | grep thanos
```

**Thanos has no data / dashboards empty**: Most likely Prometheus does not have `remote_write` configured. Add `remote_write` to `prometheus.yml` pointing to `http://thanos-receive.monitoring.svc.cluster.local:19291/api/v1/receive`.

**Thanos Query shows no stores**: Check that Receive and Store pods are running. Query discovers stores via `--store` flags — verify the service DNS names resolve correctly.

**S3 bucket errors**: Ensure the Rook-Ceph Object Store (RGW) is running: `kubectl get pods -n rook-ceph -l app=rook-ceph-rgw`. Check that the `thanos-bucket` ConfigMap and Secret have valid credentials.

**Compactor failing**: Check for lock conflicts (only one compactor should run). Verify the `rook-ceph-block` PVC is bound and writable.

## Links

- [Thanos Documentation](https://thanos.io/tip/thanos/getting-started.md/)
- [Thanos Components Overview](https://thanos.io/tip/thanos/design.md/)
- [Thanos Object Storage Configuration](https://thanos.io/tip/thanos/storage.md/)
- [Thanos Compactor](https://thanos.io/tip/components/compact.md/)
