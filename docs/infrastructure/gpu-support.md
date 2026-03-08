# GPU Support

## What It Does

Passes a physical NVIDIA GPU from the Proxmox host through to a worker VM via PCI passthrough, installs drivers, and makes the GPU available to Kubernetes pods. The full stack includes driver installation, container runtime configuration, a Kubernetes RuntimeClass for access isolation, and a device plugin that advertises GPU resources to the scheduler.

## Why It's Here

If you're running ML training, inference, or any CUDA workload in your homelab, the GPU needs to be visible inside both the VM and the container. This is a multi-layer problem — Proxmox PCI passthrough, kernel driver, container runtime integration, and Kubernetes scheduling all need to work together. The automation handles every layer.

## How It Works

### Layer 1: PCI Passthrough (Proxmox → VM)

During VM creation, the `provision_infra` role detects nodes with the `compute: cuda` label in inventory and configures PCI passthrough:

- Sets the VM machine type to Q35 (required for PCIe passthrough)
- Attaches the GPU via `hostpci0: {PCI_ADDRESS},pcie=1,x-vga=0`
- Includes all PCI functions (GPU + Audio device on the same slot)

**Prerequisite**: IOMMU must be enabled on the Proxmox host and the GPU must be bound to the `vfio-pci` driver.

### Layer 2: Driver Installation (VM OS)

The `setup_os` role's `configure_cuda.yaml` runs on nodes with `compute: cuda`:

- Queries `ubuntu-drivers list --gpgpu` for available LTS server drivers
- Selects the **second-latest** version (most battle-tested, not bleeding edge)
  - Available drivers sorted: e.g., `[535-server, 570-server, 580-server]` → selection: `[-2]` = `570-server`
  - If only one is available → installs that one (`[-1]`)
  - Fallback if none found: `nvidia-driver-535-server`
- Installs the NVIDIA Container Toolkit for CRI-O
- Creates CRI-O runtime handler config at `/etc/crio/crio.conf.d/99-nvidia.conf` with:
  - `runtime_path = "/usr/bin/nvidia-container-runtime"`
  - `runtime_type = "oci"`
  - `monitor_path = "/usr/libexec/crio/conmon"` (the CRI-O container monitor)
- Configures `/etc/nvidia-container-runtime/config.toml` with full runtime paths:
  - `runtimes = ["/usr/libexec/crio/runc", "/usr/libexec/crio/crun"]`
- Reboots the node if a new driver was installed
- **Post-reboot verification**: `nvidia-smi` is retried 3 times with 10-second delays to confirm the driver loaded successfully

**Idempotent**: Re-runs preserve the existing driver version. Drivers are never auto-upgraded.

### Layer 3: RuntimeClass (Kubernetes)

The `bootstrap_nvidia_device_plugin` role creates a Kubernetes `RuntimeClass` named `nvidia`. This is the security boundary — only pods that explicitly set `runtimeClassName: nvidia` get GPU library injection via the NVIDIA Container Toolkit.

The nvidia runtime is intentionally **not** set as the default CRI-O runtime. This prevents accidental GPU access.

### Layer 4: Device Plugin (Kubernetes)

A DaemonSet (`nvidia-device-plugin-daemonset`) in the `kube-system` namespace runs on nodes with `compute: cuda`:

- Uses `runtimeClassName: nvidia` itself (needs GPU access to discover devices)
- Advertises `nvidia.com/gpu` resources to the Kubernetes scheduler
- Pods request GPUs via `resources.limits."nvidia.com/gpu": 1`
- Adds node labels: `accelerator: nvidia-gpu`, `gpu-type: gtx-1060`
- **Security context**: `allowPrivilegeEscalation: false`, drops all capabilities
- **Tolerations**: Tolerates `nvidia.com/gpu` (device plugin taint) and all `role` taints via `operator: Exists` (GPU nodes have role taints like `role=platform:NoSchedule`)
- **`FAIL_ON_INIT_ERROR=false`**: The plugin continues running even if GPU initialization fails (useful during node startup races)

> **Note**: The `gpu-type: gtx-1060` label is currently hardcoded in the device plugin deployment, not auto-detected from the actual GPU hardware. If you have a different GPU, update the label in the DaemonSet manifest or remove it.

## Using GPUs in Pods

Both fields are required — without either, the pod won't get GPU access:

```yaml
spec:
  runtimeClassName: nvidia          # GPU library injection
  containers:
  - name: cuda
    image: nvidia/cuda:12.2.0-base-ubuntu22.04
    command: ["nvidia-smi"]
    resources:
      limits:
        nvidia.com/gpu: 1           # GPU scheduling
```

## Driver Upgrade Strategy

Drivers are selected once during initial provisioning and never auto-upgraded. To upgrade manually:

1. Test the new driver on a non-production node
2. Validate CUDA compatibility with your workloads
3. Remove the old driver: `apt remove nvidia-driver-XXX-server`
4. Re-run the playbook — it will select the current second-latest LTS
5. Reboot and validate with `nvidia-smi`

## GPU Monitoring

When `ENABLE_CUDA=true`, the monitoring stack includes GPU-specific components:

| Component | What It Does |
|-----------|-------------|
| [DCGM Exporter](../applications/monitoring/dcgm-exporter.md) | DaemonSet (v4.5.2-4.8.1) that collects GPU hardware metrics (temp, utilization, power, memory) |
| [Prometheus](../applications/monitoring/prometheus.md) | Scrapes DCGM metrics, evaluates `GPUHighTemperature` alert rule (>60°C for 5 min) |
| [Grafana](../applications/monitoring/grafana.md) | NVIDIA GPU Dashboard with 8 panels (utilization, temp, power, memory, clocks, PCIe) |
| [Alertmanager](../applications/monitoring/alertmanager.md) | Routes GPU temperature alerts to Matrix chat room |

**Metric deduplication**: DCGM adds pod/container labels when a GPU is allocated, creating duplicate time series. Dashboard queries use `max() by (gpu, Hostname)` aggregation, and Prometheus only scrapes the Service endpoint (not pod annotations).

## Stress Testing

To validate GPU monitoring and thermal performance, deploy a CUDA matrix multiplication loop:

```bash
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gpu-stress
spec:
  runtimeClassName: nvidia
  restartPolicy: Never
  containers:
  - name: cuda
    image: nvidia/cuda:12.2.0-devel-ubuntu22.04
    command: ["/bin/bash", "-c"]
    args:
      - |
        cat > stress.cu <<'EOC'
        #include <cuda_runtime.h>
        #include <stdio.h>
        #define N 5000
        #define BLOCK_SIZE 16
        __global__ void matrixMul(float *a, float *b, float *c, int n) {
            int row = blockIdx.y * blockDim.y + threadIdx.y;
            int col = blockIdx.x * blockDim.x + threadIdx.x;
            if (row < n && col < n) {
                float sum = 0.0f;
                for (int k = 0; k < n; k++) sum += a[row*n+k] * b[k*n+col];
                c[row*n+col] = sum;
            }
        }
        int main() {
            size_t bytes = N*N*sizeof(float);
            float *d_a, *d_b, *d_c;
            cudaMalloc(&d_a, bytes); cudaMalloc(&d_b, bytes); cudaMalloc(&d_c, bytes);
            dim3 blocks(N/BLOCK_SIZE+1, N/BLOCK_SIZE+1), threads(BLOCK_SIZE, BLOCK_SIZE);
            printf("Starting 5000x5000 matrix multiply loop — watch Grafana GPU dashboard\n");
            while(1) { matrixMul<<<blocks, threads>>>(d_a, d_b, d_c, N); cudaDeviceSynchronize(); }
        }
        EOC
        nvcc stress.cu -o stress && ./stress
    resources:
      limits:
        nvidia.com/gpu: 1
EOF
```

### Expected Thermal Profile (Water-Cooled GTX 1060 6GB)

| Time | Temperature | Utilization | Power | Notes |
|------|-------------|-------------|-------|-------|
| Idle | 30–35°C | 0% | ~10W | Baseline |
| Start | 41°C | 100% | 98.7W | Load applied |
| 16 min | 60°C | 100% | 98.7W | Thermal equilibrium |

The GPU stabilizes at 60°C — 23°C below the throttle point (83°C). Water cooling keeps temperatures well within safe range for sustained compute. Stock air cooling would typically reach 70–80°C under the same load.

```bash
# Cleanup
kubectl delete pod gpu-stress
```

## Integration Points

| Component | Relationship |
|-----------|-------------|
| `provision_infra` role | PCI passthrough during VM creation |
| `setup_os` role | Driver installation and CRI-O runtime config |
| `bootstrap_nvidia_device_plugin` role | RuntimeClass and device plugin DaemonSet |
| [DCGM Exporter](../applications/monitoring/dcgm-exporter.md) | GPU metric collection |
| [Prometheus alert rules](../applications/monitoring/prometheus.md) | `GPUHighTemperature` alert at 60°C |

## Troubleshooting

```bash
# Check GPU is visible inside the VM
ssh <gpu-node> "lspci | grep -i nvidia"
ssh <gpu-node> "nvidia-smi"

# Check driver version and GPU status
ssh <gpu-node> "nvidia-smi --query-gpu=name,driver_version,temperature.gpu,utilization.gpu --format=csv"

# Verify available drivers on the node
ssh <gpu-node> "ubuntu-drivers list --gpgpu"

# Check CRI-O nvidia runtime configuration
ssh <gpu-node> "cat /etc/crio/crio.conf.d/99-nvidia.conf"
ssh <gpu-node> "cat /etc/nvidia-container-runtime/config.toml"

# Verify CRI-O knows about the nvidia runtime
ssh <gpu-node> "crictl info | grep -A5 nvidia"

# Check RuntimeClass exists in Kubernetes
kubectl get runtimeclass nvidia

# Check device plugin is running and advertising GPUs
kubectl get pods -n kube-system -l app=nvidia-device-plugin
kubectl describe node <gpu-node> | grep -A5 nvidia.com/gpu
kubectl describe node <gpu-node> | grep -A5 'Allocated resources'

# Check DCGM Exporter
kubectl get pods -n monitoring -l app=dcgm-exporter
kubectl logs -n monitoring -l app=dcgm-exporter --tail=20

# Test GPU access from a pod
kubectl run gpu-test --rm -it --restart=Never \
  --overrides='{"spec":{"runtimeClassName":"nvidia","containers":[{"name":"test","image":"nvidia/cuda:12.2.0-base-ubuntu22.04","command":["nvidia-smi"],"resources":{"limits":{"nvidia.com/gpu":"1"}}}]}}' \
  --image=nvidia/cuda:12.2.0-base-ubuntu22.04

# Check kernel messages for GPU/driver issues
ssh <gpu-node> "dmesg | grep -i -E 'nvidia|vfio|iommu'"
```

**GPU not visible in VM (`lspci` shows nothing)**: IOMMU must be enabled on the Proxmox host and the GPU must be bound to `vfio-pci`. Check `dmesg | grep -i iommu` on the Proxmox host. Verify `GPU_PCI_ADDRESS` matches the output of `lspci -D | grep -i vga` on Proxmox.

**`nvidia-smi` not found after provisioning**: The node may need a reboot. The automation reboots after driver install, but check `dpkg -l | grep nvidia-driver` to confirm the driver package is installed.

**Pod can't access GPU ("nvidia.com/gpu" not in allocatable)**: Ensure the device plugin pod is Running. Check that the node has the `compute: cuda` label. Verify both `runtimeClassName: nvidia` and `resources.limits.nvidia.com/gpu` are set in the pod spec.

**DCGM Exporter pod CrashLoopBackOff**: The exporter must have `runtimeClassName: nvidia` and request `nvidia.com/gpu: 1`. If the device plugin hasn't registered the GPU yet, the pod can't be scheduled.

---

## Links

- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/overview.html)
- [NVIDIA Device Plugin for Kubernetes](https://github.com/NVIDIA/k8s-device-plugin)
- [Proxmox PCI Passthrough](https://pve.proxmox.com/wiki/PCI_Passthrough)
- [CUDA Toolkit Documentation](https://docs.nvidia.com/cuda/)
- [ubuntu-drivers Documentation](https://ubuntu.com/server/docs/nvidia-drivers-installation)
