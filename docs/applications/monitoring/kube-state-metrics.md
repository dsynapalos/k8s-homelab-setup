# kube-state-metrics

## What It Does

kube-state-metrics listens to the Kubernetes API server and generates metrics about the state of Kubernetes objects — deployments, pods, nodes, jobs, cronjobs, services, PVCs, and more. Unlike cAdvisor (container resource usage) or Node Exporter (host-level hardware), kube-state-metrics exposes the *desired vs actual* state of the cluster: how many replicas are requested vs ready, which pods are pending, what conditions nodes report, and whether jobs succeeded or failed.

## Why It's Here

Without kube-state-metrics, the monitoring stack has no visibility into Kubernetes object lifecycle. You can see CPU and memory consumption, but not whether a Deployment is stuck at 0/3 ready replicas, a CronJob is failing, or a PVC is stuck in Pending state. These are the metrics that power capacity planning, rollout monitoring, and alerting on workload health.

## How It's Configured

**Deployment**: Single replica (`registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.18.0`) in the `monitoring` namespace.

**RBAC**: Dedicated ServiceAccount with a ClusterRole granting read-only (`list`, `watch`) access to all standard Kubernetes resource types. This is required because kube-state-metrics needs cluster-wide visibility to generate accurate counts and status metrics.

**Ports**:
- `8080` — primary metrics endpoint (`/metrics`), exposes all kube_* metrics
- `8081` — self-telemetry endpoint, exposes internal process metrics and readiness probe

**Metrics endpoint**: Port 8080, discovered by the OTel Collector via the catch-all `kubernetes-services` scrape job using the `prometheus.io/scrape: "true"` annotation on the Service.

**Security**: Runs as non-root (UID 65534), read-only root filesystem, all capabilities dropped.

## Key Metrics

| Metric | Description |
|--------|-------------|
| `kube_deployment_status_replicas_available` | Available replicas per Deployment |
| `kube_pod_status_phase` | Pod phase (Pending, Running, Succeeded, Failed, Unknown) |
| `kube_node_status_condition` | Node conditions (Ready, MemoryPressure, DiskPressure) |
| `kube_job_status_succeeded` | Whether a Job completed successfully |
| `kube_persistentvolumeclaim_status_phase` | PVC binding state (Bound, Pending, Lost) |
| `kube_daemonset_status_desired_number_scheduled` | Desired vs scheduled DaemonSet pods |
| `kube_pod_container_resource_requests` | CPU/memory requests per container |
| `kube_pod_container_resource_limits` | CPU/memory limits per container |

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [OTel Collector](otel-collector.md) | Scraped via `kubernetes-services` catch-all job (annotation-based discovery) |
| [Thanos](thanos.md) | Metrics shipped via OTel Collector's remote write exporter |
| [Grafana](grafana.md) | Metrics rendered in K8s Cluster Dashboard (deployment health, pod status) |

## Troubleshooting

```bash
# Check Deployment status (should have 1/1 ready)
kubectl get deploy -n monitoring kube-state-metrics
kubectl get pods -n monitoring -l app.kubernetes.io/name=kube-state-metrics

# Check logs for API access errors
kubectl logs -n monitoring -l app.kubernetes.io/name=kube-state-metrics --tail=20

# Test metrics endpoint
kubectl port-forward -n monitoring svc/kube-state-metrics 8080:8080
curl -s http://localhost:8080/metrics | grep kube_deployment_status_replicas | head -5

# Verify OTel Collector is scraping kube-state-metrics
kubectl port-forward -n monitoring svc/otel-collector 8889:8889
curl -s http://localhost:8889/metrics | grep kube_pod_status_phase | head -5
```

**Pod stuck in Pending**: Check ServiceAccount and ClusterRoleBinding exist. kube-state-metrics needs API access at startup.

**Metrics missing in Thanos/Grafana**: Verify the kube-state-metrics Service has `prometheus.io/scrape: "true"` and `prometheus.io/port: "8080"` annotations. Check OTel Collector logs for scrape errors targeting `kube-state-metrics`.

**High cardinality**: kube-state-metrics generates one time series per Kubernetes object. In large clusters, this can be significant. The default configuration exposes all resource types; disable unused collectors via `--resources` flag if needed.

## Links

- [kube-state-metrics Documentation](https://github.com/kubernetes/kube-state-metrics/tree/main/docs)
- [kube-state-metrics GitHub](https://github.com/kubernetes/kube-state-metrics)
- [Exposed Metrics Reference](https://github.com/kubernetes/kube-state-metrics/tree/main/docs#exposed-metrics)
