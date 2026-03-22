# Jaeger

## What It Does

Jaeger is a distributed tracing backend that collects, stores, and queries request traces. Version 2 is built on the OpenTelemetry Collector framework and runs as a single binary with OTLP-native ingestion. Traces are stored in an embedded Badger key-value database backed by a PVC for persistence across restarts.

## Why It's Here

Adds distributed tracing to the observability stack alongside metrics (Thanos) and logs (Loki). Traces enable request-level latency analysis, dependency mapping, and root cause investigation across microservices — completing the three pillars of observability in Grafana.

## How It's Configured

### Deployment Mode

All-in-one — collector, query, and storage run in a single Deployment with 1 replica. Uses `strategy: Recreate` because Badger requires an exclusive file lock on its data directory.

### Storage

Badger embedded key-value store with PVC persistence:
- **Keys**: `/var/jaeger/badger/keys` — index data
- **Values**: `/var/jaeger/badger/values` — span data
- **PVC**: `jaeger-data` — 5Gi on `rook-ceph-block` StorageClass
- **TTL**: 168h (7-day span retention)
- **Ephemeral**: `false` — data survives container restarts via WAL replay

### Key Settings

| Setting | Value | Notes |
|---------|-------|-------|
| OTLP gRPC receiver | `0.0.0.0:4317` | Receives traces from OTel Collector |
| OTLP HTTP receiver | `0.0.0.0:4318` | Alternative HTTP ingestion |
| Query UI | `0.0.0.0:16686` | Jaeger UI and API |
| Metrics | `0.0.0.0:8888` | Prometheus metrics endpoint |
| Health check | `0.0.0.0:13133` | Readiness/liveness probes |
| Batch processor | 1000 send / 2000 max / 5s timeout | Batches spans before writing to Badger |

### Image

Pulled from Harbor cache: `harbor.k8s.local/dockerhub-cache/jaegertracing/jaeger:<JAEGER_VERSION>`

Version pinned via `JAEGER_VERSION` in `.env`.

## Integration Points

- **OTel Collector**: The gateway DaemonSet's traces pipeline exports OTLP gRPC to `jaeger.monitoring.svc.cluster.local:4317`. Applications emit traces to the node-agent DaemonSet (host ports 4317/4318), which forwards them via the gateway to Jaeger.
- **Grafana**: Jaeger datasource provisioned automatically via ConfigMap (`jaeger-datasource.yaml`). Accessible at `http://jaeger.monitoring.svc.cluster.local:16686`.
- **Ingress**: Available at `https://jaeger.k8s.local` via Cilium ingress with TLS from cert-manager.
- **Prometheus scraping**: Service annotated with `prometheus.io/scrape: "true"` on port 8888 for Jaeger's own metrics.

## Troubleshooting

### Jaeger pod not starting

```bash
kubectl -n monitoring describe deployment jaeger
kubectl -n monitoring logs -l app=jaeger
```

Common causes:
- PVC not bound — verify `kubectl -n monitoring get pvc jaeger-data` shows `Bound` status
- Config syntax error — check `kubectl -n monitoring describe configmap jaeger-config`
- Harbor image pull failure — verify `harbor.k8s.local` is reachable from the node

### No traces appearing in Jaeger UI

1. Verify Jaeger is healthy: `kubectl -n monitoring get pods -l app=jaeger`
2. Check OTel Collector traces pipeline is loaded: `kubectl -n monitoring logs -l app=otel-collector --tail=50 | grep -i traces`
3. Verify OTel Collector can reach Jaeger: `kubectl -n monitoring exec -it $(kubectl -n monitoring get pod -l app=otel-collector -o jsonpath='{.items[0].metadata.name}') -- wget -qO- http://jaeger.monitoring.svc.cluster.local:13133/status`
4. Ensure applications are instrumented with OpenTelemetry SDKs exporting to `localhost:4317` or `localhost:4318`

### Badger storage issues

**PVC permission denied**: Jaeger v2 runs as UID 10001. The pod spec must include `securityContext.fsGroup: 10001` so the PVC mount is group-writable. Without this, Badger fails with `mkdir /var/jaeger/badger/keys: permission denied`.

```bash
# Check disk usage on PVC
kubectl -n monitoring exec -it $(kubectl -n monitoring get pod -l app=jaeger -o jsonpath='{.items[0].metadata.name}') -- du -sh /var/jaeger/badger/

# Verify Badger metrics (compaction, LSM size)
kubectl -n monitoring exec -it $(kubectl -n monitoring get pod -l app=jaeger -o jsonpath='{.items[0].metadata.name}') -- wget -qO- http://localhost:8888/metrics | grep badger
```

### Grafana cannot query Jaeger

```bash
# Verify Jaeger query endpoint
kubectl -n monitoring exec -it $(kubectl -n monitoring get pod -l app=jaeger -o jsonpath='{.items[0].metadata.name}') -- wget -qO- http://localhost:16686/api/services

# Verify service resolves
kubectl -n monitoring run --rm -it --restart=Never dns-test --image=busybox -- nslookup jaeger.monitoring.svc.cluster.local
```

## Links

- [Jaeger Documentation](https://www.jaegertracing.io/docs/)
- [Jaeger v2 Migration Guide](https://www.jaegertracing.io/docs/2.0/migration-v2/)
- [Badger Configuration Reference](https://github.com/jaegertracing/jaeger/blob/main/cmd/jaeger/config-badger.yaml)
- [OpenTelemetry SDK Instrumentation](https://opentelemetry.io/docs/instrumentation/)
