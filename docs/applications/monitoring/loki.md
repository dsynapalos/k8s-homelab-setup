# Loki

## What It Does

Grafana Loki is a horizontally-scalable, multi-tenant log aggregation system. Unlike traditional log backends, Loki only indexes label metadata (not log contents), making it lightweight and cost-effective. It uses LogQL — a Prometheus-style query language — for filtering and querying logs.

## Why It's Here

Provides centralized log aggregation for all cluster workloads. Loki complements the existing metrics pipeline (OTel Collector → Thanos) by adding a logs dimension, enabling correlation between metrics and logs in Grafana dashboards.

## How It's Configured

### Deployment Mode

Monolithic (`-target=all`) — all components run in a single process. Suitable for homelab volumes (up to ~20GB/day). Deployed as a StatefulSet with 1 replica.

### Storage

Filesystem-based storage under `/loki/`:
- **Chunks**: `/loki/chunks` — compressed log data
- **Rules**: `/loki/rules` — alerting/recording rules
- **Index**: TSDB with v13 schema, 24h period
- **Compactor**: `/loki/compactor` — retention enforcement

Currently using `emptyDir` — data does not persist across pod restarts. For durable storage, replace with a PVC backed by the Rook-Ceph StorageClass.

### Key Settings

| Setting | Value | Notes |
|---------|-------|-------|
| `auth_enabled` | `false` | Single-tenant homelab |
| `replication_factor` | `1` | Single replica |
| `chunk_encoding` | `snappy` | Fast compression |
| `retention_period` | `168h` | 7-day log retention |
| `allow_structured_metadata` | `true` | Enables structured metadata in log entries |
| `embedded_cache` | `100MB` | In-memory query result cache |

### Image

Pulled from Harbor cache: `harbor.k8s.local/dockerhub-cache/grafana/loki:<LOKI_VERSION>`

Version pinned via `LOKI_VERSION` in `.env`.

## Integration Points

- **Grafana**: Loki datasource provisioned automatically via ConfigMap (`loki-datasource.yaml`). Accessible at `http://loki.monitoring.svc.cluster.local:3100`.
- **OTel Collector**: Receives logs from three dedicated pipelines via OTLP HTTP on port 3100:

  | Pipeline | Receiver | Scope | Log Source | Loki Labels |
  |----------|----------|-------|------------|-------------|
  | `logs` | `filelog` | Node-local | Container log files from `/var/log/pods` | `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name` |
  | `logs/k8s-events` | `k8s_events` | Leader-elected | Kubernetes Events API (scheduling, OOM, scaling) | `k8s.namespace.name`, `k8s.object.kind`, `k8s.object.name` |
  | `logs/k8s-objects` | `k8sobjects` | Leader-elected | Kubernetes resource changes (pods, deployments, events) | `k8s.namespace.name`, `k8s.resource.name` |

  All pipelines export via `otlp_http` to Loki’s native OTLP endpoint (`/otlp`). Loki automatically maps OTel resource attributes to index labels — no `resource/loki` processor is needed.

- **Ingress**: Available at `https://loki.k8s.local` via Cilium ingress with TLS from cert-manager.
- **Prometheus scraping**: Service annotated with `prometheus.io/scrape: "true"` for Loki's own metrics.

## Troubleshooting

### Loki pod not starting

```bash
kubectl -n monitoring describe statefulset loki
kubectl -n monitoring logs loki-0
```

Common causes:
- Config syntax error in `loki-config.yaml` — check `kubectl -n monitoring describe configmap loki-config`
- Harbor image pull failure — verify `harbor.k8s.local` is reachable from the node

### Grafana cannot reach Loki

```bash
# Verify Loki is ready
kubectl -n monitoring get pods -l app=loki
kubectl -n monitoring exec -it loki-0 -- wget -qO- http://localhost:3100/ready

# Verify service resolves
kubectl -n monitoring run --rm -it --restart=Never dns-test --image=busybox -- nslookup loki.monitoring.svc.cluster.local
```

### No logs appearing in Grafana

The OTel Collector ships logs to Loki via three pipelines. Verify:
1. OTel Collector pods are running: `kubectl get pods -n monitoring -l app=otel-collector`
2. Loki is receiving data: `kubectl -n monitoring exec -it loki-0 -- wget -qO- http://localhost:3100/metrics | grep loki_distributor_bytes_received_total`
3. Check OTel Collector logs for Loki exporter errors: `kubectl logs -n monitoring -l app=otel-collector --tail=50 | grep -i loki`
4. Query logs in Grafana Explore: `{k8s_namespace_name="monitoring"}` (container logs) or `{k8s_object_kind="Event"}` (K8s events)

### Checking retention

```bash
# Verify compactor is running (embedded in monolithic mode)
kubectl -n monitoring exec -it loki-0 -- wget -qO- http://localhost:3100/compactor/ring
```

## Links

- [Loki documentation](https://grafana.com/docs/loki/latest/)
- [LogQL reference](https://grafana.com/docs/loki/latest/query/)
- [Loki Helm chart](https://github.com/grafana/loki/tree/main/production/helm/loki)
- [Deployment modes](https://grafana.com/docs/loki/latest/get-started/deployment-modes/)
