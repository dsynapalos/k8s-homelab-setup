# DCGM Exporter

## What It Does

NVIDIA DCGM (Data Center GPU Manager) Exporter collects GPU hardware metrics and exposes them in Prometheus format. It provides real-time visibility into GPU utilization, temperature, power draw, memory usage, clock speeds, and PCIe throughput.

## Why It's Here

When `ENABLE_CUDA=true`, GPUs are passed through to worker VMs for compute workloads. Without DCGM Exporter, you'd have no visibility into whether the GPU is being used efficiently, overheating, or experiencing errors. This is the only way to get GPU metrics into the Prometheus/Grafana stack.

## How It's Configured

**Deployment**: DaemonSet (`nvcr.io/nvidia/k8s/dcgm-exporter:4.5.2-4.8.1-ubuntu22.04`) that runs only on nodes with:
- `nodeSelector: compute: cuda` — targets GPU nodes
- `runtimeClassName: nvidia` — required for GPU library injection
- `nvidia.com/gpu: 1` resource request — must allocate a GPU to read its metrics

**Security context**: Runs as root with `privileged: true` and `SYS_ADMIN` capability (required for direct GPU access via NVML).

**Metrics endpoint**: Port 9400, discovered by Prometheus via service annotations:
```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "9400"
```

**Key metrics exposed**:
- `DCGM_FI_DEV_GPU_TEMP` — GPU temperature (°C)
- `DCGM_FI_DEV_GPU_UTIL` — GPU utilization (%)
- `DCGM_FI_DEV_MEM_COPY_UTIL` — Memory utilization (%)
- `DCGM_FI_DEV_POWER_USAGE` — Power draw (W)
- `DCGM_FI_DEV_SM_CLOCK` — SM clock frequency (MHz)
- `DCGM_FI_DEV_FB_FREE` — Framebuffer memory free (MiB)

**Metric deduplication**: Prometheus scrapes only the Service endpoint (not pod annotations) to prevent duplicate time series. Grafana dashboards aggregate by hardware labels `max() by (gpu, Hostname)`.

## Prerequisites

- `ENABLE_CUDA=true` in `.env`
- NVIDIA drivers installed on node (handled by `setup_os` role)
- Kubernetes RuntimeClass `nvidia` exists (handled by `bootstrap_nvidia_device_plugin` role)

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Prometheus](prometheus.md) | Scrapes `/metrics` on port 9400 via service discovery |
| [Grafana](grafana.md) | NVIDIA GPU Dashboard visualizes DCGM metrics |
| [Alertmanager](alertmanager.md) | `GPUHighTemperature` alert fires on `DCGM_FI_DEV_GPU_TEMP > 60` |

## Troubleshooting

```bash
# Check DaemonSet and pod status
kubectl get ds -n monitoring dcgm-exporter
kubectl get pods -n monitoring -l app=dcgm-exporter -o wide
kubectl logs -n monitoring -l app=dcgm-exporter --tail=30

# Verify pod is on a GPU node
kubectl describe pod -n monitoring -l app=dcgm-exporter | grep -E 'Node:|nvidia.com/gpu'

# Test metrics endpoint directly
kubectl port-forward -n monitoring svc/dcgm-exporter 9400:9400
curl -s http://localhost:9400/metrics | head -20

# Check if Prometheus is scraping it
kubectl port-forward -n monitoring svc/prometheus 9090:9090
# Then check Status → Targets for dcgm-exporter
```

**Pod not scheduled**: The DaemonSet requires `nodeSelector: compute: cuda`, `runtimeClassName: nvidia`, and `nvidia.com/gpu: 1`. Ensure the device plugin has registered GPUs on the node: `kubectl describe node <gpu-node> | grep nvidia.com/gpu`.

**Pod CrashLoopBackOff**: Usually means the GPU isn't accessible. Verify `nvidia-smi` works on the node. See [GPU Support — Troubleshooting](../../infrastructure/gpu-support.md#troubleshooting).

**Metrics show in Prometheus but duplicated in Grafana**: Only the Service endpoint should be scraped (not pod annotations). Dashboard queries should use `max() by (gpu, Hostname)` aggregation.

## Links

- [DCGM Exporter Documentation](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html)
- [DCGM Metrics Reference](https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/overview.html)
