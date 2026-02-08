# Metrics Server

## What It Does

Metrics Server is a cluster-wide aggregator of resource usage data. It collects CPU and memory metrics from kubelets and exposes them via the Kubernetes Metrics API (`metrics.k8s.io`), enabling `kubectl top nodes`, `kubectl top pods`, and Horizontal Pod Autoscaling (HPA).

## Why It's Here

Without Metrics Server, the Kubernetes API has no concept of actual resource consumption. It's a prerequisite for:

- `kubectl top` commands (basic operational troubleshooting)
- Horizontal Pod Autoscaler (HPA) — scales workloads based on CPU/memory usage
- Vertical Pod Autoscaler (VPA) — recommends resource requests/limits
- Kubernetes Dashboard resource views

## How It's Configured

**Deployment**: Single replica (`registry.k8s.io/metrics-server/metrics-server:v0.7.2`) in the `kube-system` namespace with `system-cluster-critical` priority class.

**RBAC**: Dedicated ServiceAccount with a ClusterRole (`system:metrics-server`) granting access to `nodes/metrics`, `pods`, and `nodes`. Additional bindings for API aggregation: `system:auth-delegator` ClusterRoleBinding and `extension-apiserver-authentication-reader` RoleBinding in `kube-system`.

**API aggregation**: Registers `v1beta1.metrics.k8s.io` APIService pointing to the metrics-server Service in the `kube-system` namespace. This extends the Kubernetes API so that `kubectl top` and HPA controllers can query resource metrics natively.

**Flags**:
- `--kubelet-insecure-tls` — skips kubelet certificate verification (homelab/self-signed certs)
- `--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname` — resolves kubelets by internal IP first
- `--metric-resolution=15s` — scrape interval for kubelet metrics
- `--secure-port=10250` — HTTPS serving port
- `--cert-dir=/tmp` — self-signed TLS certs written to ephemeral volume

**Port**: `10250` (HTTPS) — serves the Metrics API and health probes.

**Security**: Runs as non-root (UID 1000), read-only root filesystem, all capabilities dropped. TLS cert directory uses an emptyDir volume.

## Integration Points

| Component | Relationship |
|-----------|-------------|
| Kubernetes API Server | Metrics Server registers as an API aggregation layer (`metrics.k8s.io`) |
| kubelet | Scraped every 15s for node and pod resource usage via `/metrics/resource` |
| HPA controller | Reads pod CPU/memory from the Metrics API to make scaling decisions |
| `kubectl top` | Queries the Metrics API to display resource consumption |

## Troubleshooting

```bash
# Check Deployment status (should have 1/1 ready)
kubectl get deploy -n kube-system metrics-server
kubectl get pods -n kube-system -l app.kubernetes.io/name=metrics-server

# Check logs for kubelet connectivity or TLS errors
kubectl logs -n kube-system -l app.kubernetes.io/name=metrics-server --tail=20

# Verify the APIService is available
kubectl get apiservice v1beta1.metrics.k8s.io

# Test that metrics are flowing
kubectl top nodes
kubectl top pods -A
```

**Pod stuck in CrashLoopBackOff**: Check logs for TLS errors. The `--kubelet-insecure-tls` flag must be present if kubelets use self-signed certificates. Verify the ServiceAccount and auth-delegator binding exist.

**APIService shows `False` availability**: The metrics-server pod must be running and passing health checks. Check pod logs and ensure port `10250` is reachable from the API server.

**`kubectl top` returns "Metrics not available"**: The APIService must report `Available=True`. If the pod just started, wait 30-60 seconds for the first scrape cycle. Check `kubectl get apiservice v1beta1.metrics.k8s.io -o yaml` for conditions.

**Metrics stale or missing for some nodes**: Check kubelet connectivity — metrics-server must be able to reach each node's kubelet on port 10250. Verify `--kubelet-preferred-address-types` matches your node addressing.

## Links

- [Metrics Server Documentation](https://github.com/kubernetes-sigs/metrics-server)
- [Kubernetes Metrics API](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/)
- [Horizontal Pod Autoscaler](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
