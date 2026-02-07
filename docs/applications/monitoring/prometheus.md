# Prometheus

> **⚠️ DEPRECATED**: Prometheus has been replaced by the [OpenTelemetry Collector](otel-collector.md) as the cluster's metrics collection engine. The OTel Collector uses a Prometheus receiver with the same scrape configuration and forwards metrics to Thanos Receive via `prometheusremotewrite` exporter — closing the remote-write gap that existed with standalone Prometheus. The Prometheus ArgoCD Application manifest is no longer deployed. These manifests remain in the repository for reference but are not actively maintained.

## What It Does

Prometheus is the core metrics collection and alerting engine for the cluster. It scrapes metrics from every layer of the Kubernetes stack — API server, nodes, pods, services — and stores them as time-series data. In this environment, Prometheus is the single source of truth for all operational metrics.

## Why It's Here

Every observable system needs a metrics backend. Prometheus was chosen because:

- It's the de facto standard for Kubernetes monitoring (native service discovery)
- It integrates natively with all other components in this stack (Grafana, Alertmanager, DCGM Exporter, Node Exporter, Thanos)
- Vanilla deployment (not Prometheus Operator) keeps complexity low and makes the setup easier to understand

## How It's Configured

**Deployment**: Single replica (`prom/prometheus:v2.48.0`) in the `monitoring` namespace, with `emptyDir` storage and 200h retention.

> **Storage is ephemeral**: Prometheus uses `emptyDir` — all metrics are lost when the pod restarts. The 200h retention setting is a maximum, not a guarantee. This is why Thanos is deployed alongside Prometheus: to provide durable, long-term metric storage. Until Prometheus `remote_write` is configured (see Integration Points below), restarting the Prometheus pod means starting with zero historical data.

**Scrape targets** (via `kubernetes_sd_configs`):
- Prometheus itself
- Kubernetes API server
- Kubernetes nodes (kubelet metrics)
- Kubernetes pods (annotation-based: `prometheus.io/scrape: "true"`)
- Kubernetes services (annotation-based discovery)
- cAdvisor (container resource metrics via kubelet proxy)
- Node Exporter (via EndpointSlice discovery)

**Alerting**: Forwards alerts to Alertmanager at `alertmanager.monitoring.svc.cluster.local:9093`. Alert rules are loaded from a ConfigMap (currently includes GPU temperature alerts).

**Access**: Exposed via Cilium Ingress at `prometheus.k8s.local`.

**ArgoCD sync-wave**: 2 (deploys alongside other core monitoring components).

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Alertmanager](alertmanager.md) | Receives firing alerts from Prometheus rule evaluation |
| [Grafana](grafana.md) | Queries Prometheus (via Thanos) as its primary datasource |
| [Node Exporter](node-exporter.md) | Scraped for host-level metrics |
| [DCGM Exporter](dcgm-exporter.md) | Scraped for GPU metrics (when `ENABLE_CUDA=true`) |
| [Thanos](thanos.md) | Thanos Receive is deployed and ready to accept remote write — see note below |

> **⚠️ Thanos integration gap**: The Thanos stack (Receive, Query, Store, Compactor) is fully deployed, but Prometheus is not yet configured to send data to it. To enable the data pipeline, add a `remote_write` section to `prometheus.yml` pointing to `http://thanos-receive.monitoring.svc.cluster.local:19291/api/v1/receive`. Without this, Thanos has no data and Grafana (which queries Thanos Query) will only show metrics from Prometheus's local 200h retention.

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n monitoring -l app=prometheus
kubectl logs -n monitoring -l app=prometheus --tail=50

# Access Prometheus UI
kubectl port-forward -n monitoring svc/prometheus 9090:9090
# Then open http://localhost:9090

# Check scrape targets (Status → Targets in UI)
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -E '"health|scrapeUrl'

# Check loaded alert rules
curl -s http://localhost:9090/api/v1/rules | python3 -m json.tool | head -40

# Verify RBAC (required for service discovery)
kubectl get clusterrole prometheus
kubectl get clusterrolebinding prometheus

# Check ConfigMap for scrape config
kubectl get configmap -n monitoring -l app=prometheus
```

**Targets showing as DOWN**: Check that the target service has `prometheus.io/scrape: "true"` annotation and the port annotation matches the actual metrics port.

**No data after pod restart**: Prometheus uses `emptyDir` storage — all metrics are lost on restart. This is expected. Enable `remote_write` to Thanos for durable storage.

**Alert rules not loading**: Verify the alert-rules ConfigMap exists and is mounted at the correct path. Check Prometheus logs for YAML parse errors in rule files.

## Links

- [Prometheus Documentation](https://prometheus.io/docs/introduction/overview/)
- [Prometheus Configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [PromQL Query Language](https://prometheus.io/docs/prometheus/latest/querying/basics/)
