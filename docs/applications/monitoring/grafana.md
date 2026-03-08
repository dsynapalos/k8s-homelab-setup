# Grafana

## What It Does

Grafana is the visualization frontend for the monitoring stack. It queries metrics from Thanos (which federates Prometheus data) and renders them as interactive dashboards for cluster health, resource usage, and GPU performance.

## Why It's Here

Prometheus stores metrics and Thanos provides long-term queryability, but neither has a good UI for exploring data. Grafana turns raw time-series into actionable dashboards — letting you see at a glance whether the cluster is healthy, GPUs are under load, or nodes are running hot.

## How It's Configured

**Deployment**: Single replica (`grafana/grafana:12.3.3`) in the `monitoring` namespace.

**Datasource**: Thanos Query at `http://thanos-query.monitoring.svc.cluster.local:9090` is configured as the default Prometheus-compatible datasource with `uid: prometheus`. The datasource is **named "Thanos" in the Grafana UI** but uses the `prometheus` type and UID — this means all dashboards reference `uid: prometheus` even though they're querying Thanos. This naming is intentional: Thanos Query speaks the Prometheus API, so Grafana treats it as a Prometheus datasource.

> **If Thanos has no data**: Grafana will show empty dashboards because its datasource points to Thanos Query, not Prometheus directly. See the [Thanos doc](thanos.md) for the `remote_write` prerequisite.

**Provisioned dashboards** (auto-loaded from ConfigMaps):

| Dashboard | File | Description |
|-----------|------|-------------|
| K8s Cluster | `k8s-cluster-dashboard.json` | Cluster-wide resource utilization |
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

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Thanos](thanos.md) | Default datasource — Grafana queries Thanos Query for all metrics |
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

**Dashboard shows "No data"**: If Thanos has no data, all dashboards will be empty. See [Thanos troubleshooting](thanos.md#troubleshooting). For GPU dashboards specifically, ensure DCGM Exporter is running with `runtimeClassName: nvidia`.

**Dashboard not updating after JSON change**: Kustomize uses `disableNameSuffixHash: false`, so ConfigMap name changes trigger a pod restart automatically. If the pod didn't restart, check that the ConfigMap name actually changed.

## Links

- [Grafana Documentation](https://grafana.com/docs/grafana/latest/)
- [Grafana Provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Grafana Dashboard Library](https://grafana.com/grafana/dashboards/)
