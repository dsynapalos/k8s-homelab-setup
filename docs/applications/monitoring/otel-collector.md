# OpenTelemetry Collector

## What It Does

The OpenTelemetry Collector uses a two-tier DaemonSet architecture to collect, aggregate, and export all telemetry signals:

### Node Agent (`otel-collector-local`) — all nodes

A DaemonSet running on every node (including control-plane and tainted infra/platform nodes) that collects node-local signals and forwards them via OTLP to the platform gateway:

- **Prometheus** (metrics): Scrapes kubelet, cAdvisor, annotated pods, and annotated service endpoints on the local node
- **kubeletstats** (metrics): Queries the local kubelet `/stats/summary` for container/pod/node/volume resource metrics
- **filelog** (logs): Tails container log files from `/var/log/pods`, parses CRI-O/containerd format, extracts K8s metadata
- **OTLP receiver** (traces): Accepts OTLP traces from application pods on the same node

All signals exit the node agent through a single `otlp/gateway` exporter pointing at the `otel-collector-cluster` Service. Light batching (1024/2048/5s) reduces per-node network chatter without adding significant latency.

### Platform Gateway (`otel-collector-cluster`) — platform nodes only

A DaemonSet running only on platform-tainted nodes (`node-role.kubernetes.io/role: platform`) that receives forwarded signals from node agents, runs cluster-scoped receivers, and exports to backends:

**Forwarded signals (from node agents → backends)**:
- **metrics/forwarded** → Thanos Receive (prometheusremotewrite)
- **logs/forwarded** → Loki (otlp_http)
- **traces** → Jaeger (otlp)

**Cluster-scoped receivers**:
- **Prometheus/apiserver** (metrics): Scrapes the Kubernetes API server
- **k8s_cluster** (metrics): Collects cluster-level metrics — deployment replicas, pod phases, node conditions, resource quotas, HPA status. Uses the `k8s_leader_elector` extension (Kubernetes Lease) so only one gateway pod actively collects.
- **k8s_events** (logs): Watches the Kubernetes Events API for scheduling, scaling, OOM, and other cluster events
- **k8sobjects** (logs): Watches Kubernetes resources (events, pods, deployments) for audit/change tracking

Heavy batching (4096/8192/10s for metrics, 2000/4000/10s for logs/traces) at the gateway level amortizes backend write cost across all nodes.

### Signal flow

```
┌─────────────────────────────────────────────────────────┐
│  Application Pods (any node)                            │
│  traces ──► otel-collector-local:4317 (node-local)      │
└───────────────────────┬─────────────────────────────────┘
                        │ internalTrafficPolicy: Local
┌───────────────────────▼─────────────────────────────────┐
│  Node Agent DaemonSet (every node)                      │
│  prometheus scrape ──┐                                  │
│  kubeletstats ───────┤  all signals                     │
│  filelog ────────────┤──► otlp/gateway exporter          │
│  otlp receiver ──────┘                                  │
└───────────────────────┬─────────────────────────────────┘
                        │ OTLP gRPC → otel-collector-cluster:4317
┌───────────────────────▼─────────────────────────────────┐
│  Platform Gateway DaemonSet (platform nodes only)       │
│  otlp receiver (forwarded) ──┐                          │
│  prometheus/apiserver ───────┤                          │
│  k8s_cluster (leader) ──────┤──► backends               │
│  k8s_events ─────────────────┤                          │
│  k8sobjects ─────────────────┘                          │
└────────┬──────────────┬─────────────────┬───────────────┘
         │              │                 │
    Thanos Receive    Loki            Jaeger
    (metrics)         (logs)          (traces)
```

## Why It's Here

The previous Prometheus deployment had two limitations:

- **No remote write**: Prometheus was not configured with `remote_write`, so Thanos Receive had no data and dashboards relied on Prometheus's ephemeral 200h `emptyDir` storage
- **Single-purpose**: Prometheus only handles metrics — adding traces or logs requires separate tooling

The OTel Collector solves both problems:

- Its `prometheusremotewrite` exporter sends every scraped metric to Thanos Receive immediately, closing the remote-write gap
- The collector's pipeline architecture is extensible — native K8s receivers (`k8s_events`, `k8s_cluster`, `k8sobjects`, `kubeletstats`) now provide deep cluster observability alongside Prometheus scraping, the filelog receiver ships container logs to Loki, and the OTLP receiver accepts traces for Jaeger

The two-tier architecture separates concerns: node agents handle local collection and OTLP ingestion with `internalTrafficPolicy: Local` guaranteeing zero-hop delivery, while the platform gateway handles aggregation, cluster-scoped receivers, and backend export

## How It's Configured

### Workloads

**Node Agent DaemonSet** (`otel-collector-local`): One pod per node (`otel/opentelemetry-collector-contrib:0.145.0`) in the `monitoring` namespace. Uses `tolerations: [operator: Exists]` to run on all nodes including control-plane and tainted infra/platform nodes. Exposes OTLP gRPC (4317), OTLP HTTP (4318), and internal metrics (8888).

**Platform Gateway DaemonSet** (`otel-collector-cluster`): Runs only on platform-tainted nodes via `nodeSelector: node-role.kubernetes.io/role: platform` and `tolerations: role=platform:NoSchedule`. Same image. Exposes the same ports. Receives OTLP from node agents and runs cluster-scoped receivers.

### Services

| Service | Selector | Traffic Policy | Purpose |
|---------|----------|---------------|---------|
| `otel-collector-local` | `app: otel-collector-local` | `internalTrafficPolicy: Local` | OTLP ingestion from application pods — always routes to the node-local agent |
| `otel-collector-cluster` | `app: otel-collector-cluster` | Default (Cluster) | Node agents forward all signals here. Cilium Socket LB picks a random gateway pod per TCP connection |

The `internalTrafficPolicy: Local` on the node-local service guarantees that OTLP traffic from application pods never leaves the node. This is safe because the DaemonSet ensures every node has one pod. If the local pod is down, sends fail (no cross-node failover) — OTel SDK retry buffers handle transient agent restarts.

### Modular configuration

The collector loads multiple config files via explicit `--config=file:/etc/otelcol/<name>.yaml` flags. The collector deep-merges every file at startup — map keys (`receivers`, `processors`, `exporters`, `service.pipelines.<name>`) from different files are combined into a single effective config. Each pipeline lives in its own self-contained file.

**Critical merge rule**: `service.extensions` is an array — arrays are **replaced** (last writer wins), not appended. This is why the node agent and gateway have separate base config files that each declare their own `service.extensions` list.

All config files live in the `configs/` subdirectory.

#### Node agent config files

| File | ConfigMap | Contains |
|------|-----------|----------|
| `configs/otel-node-base-config.yaml` | `otel-node-base-config` | Extensions (health_check), OTLP receiver (4317/4318), `otlp/gateway` exporter (→ `otel-collector-cluster:4317`), telemetry, `service.extensions` |
| `configs/otel-node-metrics-pipeline.yaml` | `otel-node-metrics-pipeline` | Node-local metrics: `receivers.prometheus` (kubelet, cAdvisor, pods, services), `processors` (batch, filter), `service.pipelines.metrics` → `otlp/gateway` |
| `configs/otel-node-kubeletstats-pipeline.yaml` | `otel-node-kubeletstats-pipeline` | Node-local kubelet stats: `receivers.kubeletstats`, `service.pipelines.metrics/kubeletstats` → `otlp/gateway` |
| `configs/otel-node-logs-pipeline.yaml` | `otel-node-logs-pipeline` | Node-local container logs: `receivers.filelog`, `processors` (batch/logs), `service.pipelines.logs` → `otlp/gateway` |
| `configs/otel-node-traces-pipeline.yaml` | `otel-node-traces-pipeline` | Node-local traces: `processors.batch/traces`, `service.pipelines.traces` (otlp → `otlp/gateway`) |

#### Platform gateway config files

| File | ConfigMap | Contains |
|------|-----------|----------|
| `configs/otel-gateway-base-config.yaml` | `otel-gateway-base-config` | Extensions (health_check, k8s_leader_elector/k8s_cluster, k8s_leader_elector/k8s_events, k8s_leader_elector/k8s_objects), OTLP receiver (4317/4318), telemetry, `service.extensions` |
| `configs/otel-gateway-metrics-pipeline.yaml` | `otel-gateway-metrics-pipeline` | Forwarded metrics pipeline: `batch/forwarded-metrics`, `prometheusremotewrite/thanos`, `service.pipelines.metrics/forwarded` |
| `configs/otel-gateway-logs-pipeline.yaml` | `otel-gateway-logs-pipeline` | Forwarded logs pipeline: `batch/forwarded-logs`, `otlp_http/loki`, `service.pipelines.logs/forwarded` |
| `configs/otel-gateway-traces-pipeline.yaml` | `otel-gateway-traces-pipeline` | Forwarded traces pipeline: `batch/forwarded-traces`, `otlp/jaeger`, `service.pipelines.traces` |
| `configs/otel-gateway-apiserver-pipeline.yaml` | `otel-gateway-apiserver-pipeline` | API server metrics: `receivers.prometheus/apiserver`, `exporters.prometheusremotewrite/apiserver`, `service.pipelines.metrics/apiserver` |
| `configs/otel-gateway-k8s-cluster-pipeline.yaml` | `otel-gateway-k8s-cluster-pipeline` | Cluster metrics with `k8s_leader_elector`: `receivers.k8s_cluster`, `exporters.prometheusremotewrite/k8s-cluster`, `service.pipelines.metrics/k8s-cluster` |
| `configs/otel-gateway-k8s-events-pipeline.yaml` | `otel-gateway-k8s-events-pipeline` | K8s events: `receivers.k8s_events`, `exporters.otlp_http/loki-k8s-events`, `service.pipelines.logs/k8s-events` |
| `configs/otel-gateway-k8s-objects-pipeline.yaml` | `otel-gateway-k8s-objects-pipeline` | K8s objects: `receivers.k8sobjects`, `exporters.otlp_http/loki-k8s-objects`, `service.pipelines.logs/k8s-objects` |

Each ConfigMap is generated by Kustomize's `configMapGenerator` with `immutable: true`. Config changes produce a new ConfigMap name (hash suffix), which forces a pod rollout.

**Volume mounting**: Both DaemonSets use `projected` volumes with one `configMap` source per pipeline file. All files land in `/etc/otelcol/` and each is referenced by a `--config=file:` flag. The node agent additionally mounts `/var/log/pods` read-only for the filelog receiver.

**Node-local filtering**: The `K8S_NODE_NAME` and `K8S_HOST_IP` environment variables are injected from the downward API (`spec.nodeName` and `status.hostIP` respectively). The Prometheus receiver's `kubernetes-nodes`, `kubernetes-pods`, `kubernetes-services`, and `kubernetes-nodes-cadvisor` scrape jobs use `__meta_kubernetes_pod_node_name` / `__meta_kubernetes_node_name` / `__meta_kubernetes_endpointslice_endpoint_node_name` relabel rules to keep only targets on the local node. The `kubeletstats` receiver connects directly to the local kubelet at `https://${K8S_HOST_IP}:10250`.

**Node label relabeling**: The `kubernetes-nodes`, `kubernetes-nodes-cadvisor`, and `kubernetes-services` scrape jobs copy the Kubernetes node name into a `node` label on every metric. This is required for Grafana dashboard template variables — the `$node` variable (populated from `kube_node_info`) is used to filter node-level panels (CPU, memory, disk). Without this relabel, metrics from kubelet/cAdvisor would only have an `instance` label (which is the node name for kubelet but a pod IP for service endpoints).

**Regex dollar-sign escaping**: The OTel Collector's confmap resolver treats `$$` as an escaped literal `$`. In Prometheus receiver scrape configs, regex **replacement** fields (e.g., `replacement: $${1}:$${2}`) use `$$` because the `$1` is a regex capture group, not an environment variable. However, regex **match** fields that reference environment variables (e.g., `regex: ${env:K8S_NODE_NAME}`) use a single `$` — using `$$` would produce a literal string that never matches.

**Empty label mitigation**: The `kubernetes-nodes` and `kubernetes-nodes-cadvisor` jobs include a `labeldrop` metric_relabel_config that removes `node_role_kubernetes_io_*` and `node_kubernetes_io_*` labels. These labels are copied from Kubernetes node metadata via `labelmap` and may have empty values (e.g., `node-role.kubernetes.io/control-plane=""`), which Thanos rejects with HTTP 409.

**Leader election**: Each cluster-scoped receiver (`k8s_cluster`, `k8s_events`, `k8sobjects`) has its own dedicated `k8s_leader_elector` extension with a separate Kubernetes Lease in the `monitoring` namespace — `otel-gateway-k8s-cluster`, `otel-gateway-k8s-events`, and `otel-gateway-k8s-objects` respectively. This ensures only one gateway pod actively collects per receiver, while allowing different receivers to be led by different pods. Each extension is defined in the gateway base config and referenced by its receiver via `k8s_leader_elector: k8s_leader_elector/<name>`. The `prometheus/apiserver` receiver does not support leader election — each gateway pod independently scrapes, and Thanos deduplicates at query time.

### Receivers

| Receiver | Workload | Pipeline | Scope | Purpose |
|----------|----------|----------|-------|--------|
| `otlp` | Node agent | traces | Node-local | Accepts OTLP traces from application pods via `otel-collector-local` Service |
| `prometheus` | Node agent | metrics | Node-local | Scrapes kubelet, cAdvisor, annotated pods, and annotated service endpoints on the local node |
| `kubeletstats` | Node agent | metrics/kubeletstats | Node-local | Queries local kubelet `/stats/summary` for container, pod, node, and volume resource metrics |
| `filelog` | Node agent | logs | Node-local | Tails `/var/log/pods/*/*/*.log`, parses CRI-O/containerd format, extracts K8s metadata from file path |
| `otlp` | Gateway | metrics/forwarded, logs/forwarded, traces | Cluster | Receives all forwarded signals from node agents |
| `prometheus/apiserver` | Gateway | metrics/apiserver | Cluster | Scrapes the Kubernetes API server metrics endpoint |
| `k8s_cluster` | Gateway | metrics/k8s-cluster | Leader-elected | Cluster-level metrics: deployments, pods, nodes, DaemonSets, StatefulSets, ReplicaSets, HPA, resource quotas |
| `k8s_events` | Gateway | logs/k8s-events | Cluster | Watches K8s Events API — pod scheduling, image pulls, OOM kills, scaling events |
| `k8sobjects` | Gateway | logs/k8s-objects | Cluster | Watches K8s resources (events, pods, deployments) for change tracking/audit |

### Exporters

| Exporter | Workload | Pipeline(s) | Purpose |
|----------|----------|-------------|--------|
| `otlp/gateway` | Node agent | metrics, metrics/kubeletstats, logs, traces | Forwards all signals via OTLP gRPC to `otel-collector-cluster:4317` |
| `prometheusremotewrite/thanos` | Gateway | metrics/forwarded | Forwarded node metrics → Thanos Receive |
| `prometheusremotewrite/apiserver` | Gateway | metrics/apiserver | API server metrics → Thanos Receive |
| `prometheusremotewrite/k8s-cluster` | Gateway | metrics/k8s-cluster | Cluster-level metrics → Thanos Receive |
| `otlp_http/loki` | Gateway | logs/forwarded | Forwarded container logs → Loki |
| `otlp_http/loki-k8s-events` | Gateway | logs/k8s-events | K8s events → Loki |
| `otlp_http/loki-k8s-objects` | Gateway | logs/k8s-objects | K8s object changes → Loki |
| `otlp/jaeger` | Gateway | traces | Forwarded traces → Jaeger |

All `prometheusremotewrite` exporters are configured with:

- **`external_labels: {cluster: homelab}`** — stamps every metric with `cluster=homelab` for Grafana template variables
- **`target_info: enabled: false`** — prevents the `target_info` time series with empty label values that cause Thanos 409 rejections
- **`retry_on_failure: enabled: false`** — fail-fast; pod restarts are preferred over unbounded retry memory growth

### Processors

| Processor | Workload | Pipeline(s) | Purpose |
|-----------|----------|-------------|--------|
| `batch` | Node agent | metrics | Light batch for scraped metrics (1024/2048/5s) |
| `batch/kubeletstats` | Node agent | metrics/kubeletstats | Light batch for kubelet stats (1024/2048/5s) |
| `batch/logs` | Node agent | logs | Light batch for container logs (1024/2048/5s) |
| `batch/traces` | Node agent | traces | Light batch for traces (1024/2048/5s) |
| `filter` | Node agent | metrics | Drops noisy cAdvisor metrics (`container_tasks_state`, `container_memory_failures_total`, `container_blkio_device_usage_total`) |
| `batch/forwarded-metrics` | Gateway | metrics/forwarded | Heavy batch for forwarded metrics (4096/8192/10s) |
| `batch/forwarded-logs` | Gateway | logs/forwarded | Heavy batch for forwarded logs (2000/4000/10s) |
| `batch/forwarded-traces` | Gateway | traces | Heavy batch for forwarded traces (2000/4000/10s) |
| `batch/apiserver` | Gateway | metrics/apiserver | Batch for API server metrics (2000/4000/10s) |
| `batch/k8s-cluster` | Gateway | metrics/k8s-cluster | Batch for cluster metrics (2000/4000/10s) |
| `batch/k8s-events` | Gateway | logs/k8s-events | Batch for K8s events (500/1000/5s) |
| `batch/k8s-objects` | Gateway | logs/k8s-objects | Batch for K8s objects (500/1000/5s) |
| `transform/drop-empty-labels` | Gateway | metrics/forwarded | Strips empty-valued data-point and resource attributes that cause Thanos 409 rejections |

### Pipelines

```
Node Agent (every node):
  metrics:           prometheus → batch → filter → otlp/gateway
  metrics/kubeletstats: kubeletstats → batch/kubeletstats → otlp/gateway
  logs:              filelog → batch/logs → otlp/gateway
  traces:            otlp → batch/traces → otlp/gateway

Platform Gateway (platform nodes):
  metrics/forwarded:   otlp → transform/drop-empty-labels → batch/forwarded-metrics → prometheusremotewrite/thanos
  metrics/apiserver:   prometheus/apiserver → batch/apiserver → prometheusremotewrite/apiserver
  metrics/k8s-cluster: k8s_cluster (leader) → batch/k8s-cluster → prometheusremotewrite/k8s-cluster
  logs/forwarded:      otlp → batch/forwarded-logs → otlp_http/loki
  logs/k8s-events:     k8s_events (leader) → batch/k8s-events → otlp_http/loki-k8s-events
  logs/k8s-objects:    k8sobjects (leader) → batch/k8s-objects → otlp_http/loki-k8s-objects
  traces:              otlp → batch/forwarded-traces → otlp/jaeger
```

### Ports

| Port | Name | Workload | Purpose |
|------|------|----------|---------|
| 4317 | otlp-grpc | Both | OTLP gRPC receiver |
| 4318 | otlp-http | Both | OTLP HTTP receiver |
| 8888 | metrics | Both | Collector's own internal telemetry metrics |
| 13133 | — | Both | Health check endpoint (liveness/readiness probes) |

### RBAC

The collector uses its own `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding` (`otel-collector`) with:

- **Core**: `nodes`, `nodes/proxy`, `nodes/stats`, `pods`, `pods/status`, `services`, `events`, `namespaces`, `namespaces/status`, `replicationcontrollers`, `replicationcontrollers/status`, `resourcequotas` (get/list/watch)
- **discovery.k8s.io**: `endpointslices` (get/list/watch)
- **apps**: `deployments`, `daemonsets`, `replicasets`, `statefulsets` (get/list/watch)
- **batch**: `jobs`, `cronjobs` (get/list/watch)
- **autoscaling**: `horizontalpodautoscalers` (get/list/watch)
- **extensions/networking.k8s.io**: `ingresses` (get/list/watch)
- **policy**: `poddisruptionbudgets` (get/list/watch)
- **storage.k8s.io**: `storageclasses`, `volumeattachments` (get/list/watch)
- **coordination.k8s.io**: `leases` (create/get/update — for `k8s_leader_elector` extension leader election)
- **nonResourceURLs**: `/metrics`, `/metrics/cadvisor` (get)

**ArgoCD sync-wave**: 2 (deploys alongside other core monitoring components).

## Expanding the Configuration

Each pipeline context is a single YAML file containing its own `receivers`, `processors`, `exporters`, and `service.pipelines.<type>` section. The collector's config loading deep-merges all files — no file needs to reference another.

### Adding a new node-local pipeline

Node agent pipelines collect data locally and forward via the shared `otlp/gateway` exporter (defined in the node base config).

**1. Create the pipeline file** in `configs/` (e.g., `configs/otel-node-new-pipeline.yaml`) with receivers, processors, and pipeline wiring. Use `otlp/gateway` as the exporter — it's already defined in the base config.

**2. Add a `configMapGenerator` entry** in `kustomization.yaml` pointing to `configs/<file>`.

**3. Add a `projected.sources` entry** and `--config=file:` arg in `node-daemonset.yaml`.

### Adding a new gateway pipeline

Gateway pipelines either receive forwarded signals (via OTLP) or run cluster-scoped receivers. They export directly to backends.

**1. Create the pipeline file** in `configs/` (e.g., `configs/otel-gateway-new-pipeline.yaml`) with its own receivers, processors, exporters, and pipeline wiring.

**2. Add a `configMapGenerator` entry** in `kustomization.yaml` pointing to `configs/<file>`.

**3. Add a `projected.sources` entry** and `--config=file:` arg in `gateway-daemonset.yaml`.

### Rules for pipeline files

- Each file is self-contained: it declares every receiver, processor, and exporter it needs (or references shared ones from the base config)
- If two files define the same component key (e.g., both define `processors.memory_limiter`), the last file in load order wins — avoid collisions by using unique names or named instances (`otlp/jaeger`, `batch/traces`)
- Base configs own `service.extensions` — pipeline files must not redefine this array (it would replace, not append)
- Pipeline files must not redefine `service.telemetry` — that belongs to the base
- For cluster-scoped receivers that support leader election (`k8s_cluster`, `k8s_events`, `k8sobjects`), reference a dedicated `k8s_leader_elector/<name>` extension (defined in the gateway base config) to avoid redundant work across gateway pods
- Node-local receivers should use `${env:K8S_NODE_NAME}` for scoping

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Thanos](thanos.md) | Receives all metrics via gateway `prometheusremotewrite` exporters on port 19291 |
| [Loki](loki.md) | Receives all logs via gateway `otlp_http` exporters on port 3100 |
| [Jaeger](jaeger.md) | Receives traces via gateway `otlp/jaeger` exporter on port 4317 |
| [Grafana](grafana.md) | Queries metrics through Thanos Query, logs through Loki, traces through Jaeger |
| [Node Exporter](node-exporter.md) | Scraped for host-level metrics via `kubernetes-services` and `kubernetes-pods` annotation-based discovery |
| [kube-state-metrics](kube-state-metrics.md) | Scraped for Kubernetes object state metrics via annotation-based discovery |
| [DCGM Exporter](dcgm-exporter.md) | Scraped for GPU metrics via pod annotation discovery (when `ENABLE_CUDA=true`) |
| [Alertmanager](alertmanager.md) | Not directly connected — alert rule evaluation is handled by [Thanos Ruler](thanos.md#thanos-ruler-statefulset) |
| [Harbor](../infrastructure/harbor.md) | Container images pulled through Harbor proxy cache (`harbor.k8s.local`) |

## Troubleshooting

```bash
# Check node agent pods
kubectl get pods -n monitoring -l app=otel-collector-local
kubectl logs -n monitoring -l app=otel-collector-local --tail=50

# Check gateway pods
kubectl get pods -n monitoring -l app=otel-collector-cluster
kubectl logs -n monitoring -l app=otel-collector-cluster --tail=50

# Check collector internal metrics (node agent)
kubectl port-forward -n monitoring svc/otel-collector-local 8888:8888
curl -s http://localhost:8888/metrics | head -30

# Check gateway internal metrics
kubectl port-forward -n monitoring svc/otel-collector-cluster 8888:8888
curl -s http://localhost:8888/metrics | head -30

# Verify RBAC
kubectl get clusterrole otel-collector
kubectl get clusterrolebinding otel-collector

# Check ConfigMaps (one per pipeline + base per workload)
kubectl get configmap -n monitoring | grep otel-

# Verify forwarding: node agent → gateway
kubectl logs -n monitoring -l app=otel-collector-local --tail=100 | grep -i "otlp\|gateway\|error"

# Verify backend export: gateway → Thanos/Loki/Jaeger
kubectl logs -n monitoring -l app=otel-collector-cluster --tail=100 | grep -i "remote\|thanos\|loki\|jaeger\|error"
```

**Scrape targets not discovered**: Check that target services have `prometheus.io/scrape: "true"` annotation. Verify the collector's ServiceAccount has the `otel-collector` ClusterRole bound.

**Thanos has no data**: Check gateway logs for remote write errors. Verify `thanos-receive` service is reachable: `kubectl get svc thanos-receive -n monitoring`. Check node agent logs for OTLP export errors to the gateway.

**Thanos 409 "label set contains a label with empty name or value"**: An exported metric has an empty label name or value. Common causes: (1) `target_info` metric with empty `server_port` — fix by setting `target_info: enabled: false` on the PRW exporter. (2) `labelmap` copying Kubernetes node labels with empty values (e.g., `node-role.kubernetes.io/control-plane=""`) — fix by adding a `labeldrop` metric_relabel_config.

**Grafana dashboards show "No data"**: The dashboards use template variables (`$cluster`, `$node`, `$instance`) populated from label queries. If metrics lack the expected labels: (1) Verify `cluster=homelab` exists: `curl -s 'http://thanos-query:9090/api/v1/query?query=kube_node_info' | jq '.data.result[0].metric.cluster'`. (2) Verify `node` label exists on cAdvisor/kubelet metrics: check the relabel rules in the `kubernetes-nodes` and `kubernetes-nodes-cadvisor` scrape jobs. (3) Verify `$instance` resolves: the k8s-views-nodes dashboard resolves `$instance` via `node_cpu_seconds_total{node="$node"}`, not `node_uname_info{nodename}`.

**OTLP send failures (node agent)**: If node agent logs show errors sending to `otel-collector-cluster:4317`, check that gateway pods are running on platform nodes and the `otel-collector-cluster` Service endpoints are populated: `kubectl get endpoints otel-collector-cluster -n monitoring`.

**Traces not reaching Jaeger**: Verify the full path: app → node agent (OTLP receiver) → gateway (OTLP receiver) → Jaeger. Check node agent logs for export errors, then gateway logs. Verify `jaeger` Service exists in monitoring namespace.

**Pod OOMKilled**: The node agent memory limit is 2 GiB, the gateway is 1 GiB. If scraping many targets, increase `resources.limits.memory` in the respective DaemonSet. There is no `memory_limiter` processor — OOM restart is preferred over silently dropping metrics.

**Config changes not applied**: The ConfigMaps are immutable. Kustomize generates a new ConfigMap with a different hash suffix on each change, which triggers a pod rollout. If the old pod is still running, check that ArgoCD has synced the latest manifests.

## Links

- [OpenTelemetry Collector Documentation](https://opentelemetry.io/docs/collector/)
- [OpenTelemetry Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)
- [Prometheus Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver)
- [Kubernetes Cluster Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/k8sclusterreceiver)
- [Kubernetes Events Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/k8seventsreceiver)
- [Kubernetes Objects Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/k8sobjectsreceiver)
- [Kubelet Stats Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/kubeletstatsreceiver)
- [Filelog Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/filelogreceiver)
- [Prometheus Remote Write Exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/prometheusremotewriteexporter)
- [Collector Deployment Patterns](https://opentelemetry.io/docs/collector/deploy/)
