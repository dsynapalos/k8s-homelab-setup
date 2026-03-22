# Grafana

## What It Does

Grafana is the visualization frontend for the monitoring stack. It queries metrics from Thanos (which federates Prometheus data) and renders them as interactive dashboards for cluster health, resource usage, and GPU performance.

## Why It's Here

Prometheus stores metrics and Thanos provides long-term queryability, but neither has a good UI for exploring data. Grafana turns raw time-series into actionable dashboards — letting you see at a glance whether the cluster is healthy, GPUs are under load, or nodes are running hot.

## How It's Configured

**Deployment**: Single replica (`grafana/grafana:12.3.3`) in the `monitoring` namespace.

**Datasource**: Three datasources are provisioned:

- **Thanos Query** at `http://thanos-query.monitoring.svc.cluster.local:9090` — the default Prometheus-compatible datasource with `uid: prometheus`. Named “Thanos” in the Grafana UI but uses the `prometheus` type and UID — all metric dashboards reference `uid: prometheus` even though they’re querying Thanos.
- **Loki** at `http://loki.monitoring.svc.cluster.local:3100` — log aggregation datasource provisioned via `loki-datasource.yaml`. Used for container logs, K8s events, and object change tracking in Grafana Explore.
- **Jaeger** at `http://jaeger.monitoring.svc.cluster.local:16686` — distributed tracing datasource provisioned via `jaeger-datasource.yaml` with `uid: jaeger`. Used for trace search and detail views in Grafana Explore.

> **If Thanos has no data**: Grafana will show empty dashboards because its datasource points to Thanos Query, not Prometheus directly. See the [Thanos doc](thanos.md) for the `remote_write` prerequisite.

**Provisioned dashboards** (auto-loaded from ConfigMaps):

| Dashboard | File | Description |
|-----------|------|-------------|
| K8s Cluster | `k8s-cluster-dashboard.json` | Cluster-wide resource utilization |
| K8s Views / Global | `k8s-views-global.json` | Global overview with resource counts, CPU, memory, network |
| K8s Views / Namespaces | `k8s-views-namespaces.json` | Per-namespace resource usage and object counts |
| K8s Views / Nodes | `k8s-views-nodes.json` | Per-node CPU, memory, disk, network with `$node` selector |
| K8s Views / Pods | `k8s-views-pods.json` | Per-pod resource usage with container breakdown |
| NVIDIA GPU | `nvidia-gpu-dashboard.json` | 8 GPU panels (utilization, temp, power, memory, clocks, PCIe) |

**Dashboard provider**: Configured to read from `/etc/grafana/provisioning/dashboards/`, with `disableNameSuffixHash: false` in Kustomize. This means ConfigMap names include a content hash (e.g., `grafana-k8s-dashboard-abc123`). When you update a dashboard JSON file, the hash changes, generating a new ConfigMap name. The Deployment references ConfigMaps by name, so this **automatically triggers a Grafana pod restart** to pick up dashboard changes — no manual rollout needed.

**Access**: Exposed via Cilium Ingress at `https://grafana.k8s.local` (HTTP requests are redirected to HTTPS via `ingress.cilium.io/force-https`).

**Authentication**: Keycloak OIDC via the `grafana` client in the `homelab` realm. Configured through `GF_AUTH_GENERIC_OAUTH_*` environment variables in the Deployment. Role mapping uses a JMESPath expression on the `roles` claim:

| Keycloak Role | Grafana Org Role |
|---------------|------------------|
| `cluster-admins` | Admin |
| `cluster-users` | Editor |
| `cluster-reviewers` | Viewer |

The default local admin (`admin`/`admin`) no longer works as a fallback — the login form is disabled (`GF_AUTH_DISABLE_LOGIN_FORM`) and OIDC auto-login is enabled (`GF_AUTH_GENERIC_OAUTH_AUTO_LOGIN`). TLS verification for backend OIDC calls uses the homelab CA certificate distributed by [trust-manager](../security/trust-manager.md) (mounted from the `homelab-ca-bundle` ConfigMap at `/etc/ssl/certs/homelab/ca-certificates.crt`).

## Dashboard Notes

- GPU dashboard queries use `max() by (gpu, Hostname)` aggregation to deduplicate per-pod time series from DCGM Exporter
- All dashboards reference datasource `uid: prometheus` which points to Thanos Query
- **KSM v2.x metric compatibility**: Dashboards use KSM v2.x metric names (`kube_namespace_created`, `kube_deployment_created`, `kube_statefulset_created`, `kube_daemonset_created`, `kube_horizontalpodautoscaler_info`, `kube_networkpolicy_created`, `kube_endpointslice_info`). The v1.x `*_labels` metrics were removed in KSM 2.0.
- **k8s-views-nodes `$instance` variable**: Resolved via `node_cpu_seconds_total{node="$node", cluster="$cluster"}` instead of `node_uname_info{nodename}`. The OTel Collector adds a `node` label to all metrics from the prometheus scrape jobs (kubelet, cAdvisor, service endpoints). This avoids dependency on `hostNetwork` for hostname resolution.
- **`$cluster` template variable**: Dashboards populate `$cluster` from `label_values(kube_node_info, cluster)`. The OTel Collector's `prometheusremotewrite` exporters stamp `external_labels: {cluster: homelab}` on every metric to satisfy this.

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Thanos](thanos.md) | Default datasource — Grafana queries Thanos Query for all metrics |
| [Loki](loki.md) | Log datasource — Grafana queries Loki for container logs, K8s events, and object changes |
| [Jaeger](jaeger.md) | Trace datasource — Grafana queries Jaeger for distributed trace search and detail views |
| [Prometheus](prometheus.md) | Indirect — metrics flow through Thanos |
| [DCGM Exporter](dcgm-exporter.md) | GPU metrics rendered in NVIDIA GPU Dashboard |
| [Node Exporter](node-exporter.md) | Host metrics rendered in K8s Cluster Dashboard |
| [Keycloak](../security/keycloak.md) | OIDC provider — SSO authentication via `grafana` client in homelab realm |
| [trust-manager](../security/trust-manager.md) | Distributes homelab CA certificate for OIDC TLS verification |
| [Harbor](../infrastructure/harbor.md) | Container images pulled through Harbor proxy cache (`harbor.k8s.local`) |

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n monitoring -l app=grafana
kubectl logs -n monitoring -l app=grafana --tail=50

# Check datasource provisioning
kubectl logs -n monitoring -l app=grafana | grep -i datasource

# Check dashboard provisioning
kubectl logs -n monitoring -l app=grafana | grep -i dashboard

# Verify datasource ConfigMap has correct uid
kubectl get configmap -n monitoring -l app=grafana -o yaml | grep uid

# Access Grafana UI
kubectl port-forward -n monitoring svc/grafana 3000:3000
# Then open http://localhost:3000
```

**Datasource not found**: Ensure the Prometheus datasource ConfigMap has `uid: prometheus` — all dashboards reference this UID. The datasource points to Thanos Query, not Prometheus directly.

**Dashboard shows "No data"**: If Thanos has no data, all dashboards will be empty. See [Thanos troubleshooting](thanos.md#troubleshooting). If only specific panels are empty, check template variable resolution: the `$cluster` variable depends on metrics having a `cluster` label (set by OTel Collector `external_labels`), and the `$node`/`$instance` variables depend on the `node` label being present on kubelet/cAdvisor/service endpoint metrics. See [OTel Collector troubleshooting](otel-collector.md#troubleshooting) for label diagnostics. For GPU dashboards specifically, ensure DCGM Exporter is running with `runtimeClassName: nvidia`.

**Dashboard not updating after JSON change**: Kustomize uses `disableNameSuffixHash: false`, so ConfigMap name changes trigger a pod restart automatically. If the pod didn't restart, check that the ConfigMap name actually changed.

## Links

- [Grafana Documentation](https://grafana.com/docs/grafana/latest/)
- [Grafana Provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Grafana Dashboard Library](https://grafana.com/grafana/dashboards/)
