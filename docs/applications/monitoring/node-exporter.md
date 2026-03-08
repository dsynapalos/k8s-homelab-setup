# Node Exporter

## What It Does

Prometheus Node Exporter exposes hardware and OS-level metrics from every node in the cluster — CPU usage, memory pressure, disk I/O, network traffic, filesystem utilization, and more. It's the standard way to get host-level visibility in a Prometheus stack.

## Why It's Here

Kubernetes metrics (from kubelet/cAdvisor) tell you about containers, but not about the underlying host. Node Exporter fills this gap. When a node runs out of disk space, hits memory pressure, or has a failing NIC, Node Exporter metrics are how you detect it.

## How It's Configured

**Deployment**: DaemonSet (`prom/node-exporter`) that runs on every node in the cluster.

> **Note**: Unlike most other applications in this stack, Node Exporter has no `kustomization.yaml` — the ArgoCD Application deploys it as raw YAML resources (directory type, not Kustomize).

**Host access**: Mounts `/sys` and `/` from the host (read-only) to collect OS metrics:
```yaml
volumes:
  - hostPath: /sys   → /host/sys
  - hostPath: /      → /host/root
```

**Disabled collectors** (to reduce noise on Kubernetes nodes):
- `wifi` — not relevant for VMs
- `hwmon` — hardware monitoring (handled by DCGM for GPUs)

**Ignored paths**: Docker and kubelet internal mounts are excluded from filesystem metrics to avoid false alerts.

**Metrics endpoint**: Port 9100, discovered by Prometheus via service annotation `prometheus.io/scrape: "true"` and EndpointSlice discovery.

## Key Metrics

| Metric | Description |
|--------|-------------|
| `node_cpu_seconds_total` | CPU time per mode (user, system, idle) |
| `node_memory_MemAvailable_bytes` | Available memory |
| `node_filesystem_avail_bytes` | Free disk space per mount |
| `node_disk_io_time_seconds_total` | Disk I/O utilization |
| `node_network_receive_bytes_total` | Network ingress |

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Prometheus](prometheus.md) | Scraped via `node-exporter` job (EndpointSlice discovery) |
| [Grafana](grafana.md) | Metrics rendered in K8s Cluster Dashboard |
| [Harbor](../infrastructure/harbor.md) | Container images pulled through Harbor proxy cache (`harbor.k8s.local`) |

## Troubleshooting

```bash
# Check DaemonSet status (should run on every node)
kubectl get ds -n monitoring node-exporter
kubectl get pods -n monitoring -l app=node-exporter -o wide

# Check logs for collector errors
kubectl logs -n monitoring -l app=node-exporter --tail=20

# Test metrics endpoint
kubectl port-forward -n monitoring svc/node-exporter 9100:9100
curl -s http://localhost:9100/metrics | grep node_cpu_seconds_total | head -5

# Verify Prometheus is scraping node-exporter
kubectl port-forward -n monitoring svc/prometheus 9090:9090
# Then check Status → Targets for node-exporter
```

**Pod not running on a node**: Check DaemonSet tolerations. Node Exporter should run on all nodes including the control plane.

**Filesystem metrics showing internal mounts**: Docker and kubelet paths are excluded via `--collector.filesystem.ignored-mount-points`. If you see unexpected mounts, add them to the ignore pattern.

**Metrics missing in Prometheus**: Verify the Service has `prometheus.io/scrape: "true"` annotation and the EndpointSlice discovery job is configured.

## Links

- [Node Exporter Documentation](https://prometheus.io/docs/guides/node-exporter/)
- [Node Exporter GitHub](https://github.com/prometheus/node_exporter)
- [Available Collectors](https://github.com/prometheus/node_exporter#collectors)
