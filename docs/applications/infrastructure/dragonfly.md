# Dragonfly P2P Image Distribution

## What It Does

Dragonfly is a CNCF-graduated peer-to-peer (P2P) file distribution system that accelerates container image pulls across the cluster. A DaemonSet client (`dfdaemon`) runs on every node and intercepts CRI-O image pulls via a local proxy on port 4001. When a node pulls an image, the client contacts the scheduler, which coordinates P2P transfer from the seed client or peer nodes that already have the layers cached. The seed client pulls from Harbor's proxy cache (which in turn pulls from upstream registries), so the full chain is:

```
CRI-O → Dragonfly client (127.0.0.1:4001) → Scheduler → Seed client → Harbor → Upstream registry
          ↕ (P2P transfer between peers)
```

Once any node has downloaded an image layer, other nodes can pull it directly from that peer instead of going back to Harbor or upstream, reducing bandwidth and speeding up multi-node deployments.

## Why It's Here

- **Bandwidth reduction** — Image layers are downloaded once from Harbor and distributed peer-to-peer across all 6 nodes
- **Faster pulls** — Concurrent piece-based downloads from multiple peers instead of sequential pulls from a single registry
- **Harbor integration** — Preheat support pushes images into the P2P cache proactively when Harbor proxy cache projects receive new images
- **Resilience** — If the P2P network is unavailable, clients fall back to pulling directly from the upstream registry (back-to-source enabled)
- **Prefetching** — Clients speculatively download related layers before they're needed

## How It's Configured

### Deployment

- **Delivery**: ArgoCD Application (infra tier, sync-wave 1) via Helm chart `dragonfly` from `dragonflyoss.github.io/helm-charts/`
- **Chart version**: 1.6.14
- **Namespace**: `dragonfly-system`
- **Feature flag**: `ENABLE_DRAGONFLY=true` in `.env`
- **Sveltos dependencies**: `cert-manager`, `trust-manager`, `rook-ceph-cluster` (Dragonfly deploys only after all three are healthy)

### Architecture

| Component | Type | Replicas | Image | Node Placement | Purpose |
|-----------|------|----------|-------|---------------|---------|
| Manager | Deployment | 1 | `dragonflyoss/manager:v2.4.2` | `role: infra` | REST/gRPC API, manages scheduler/client registration, preheat jobs |
| Scheduler | Deployment | 1 | `dragonflyoss/scheduler:v2.4.2` | `role: infra` | Coordinates P2P piece distribution, assigns peers to downloads |
| Seed Client | StatefulSet | 1 | `dragonflyoss/client:v1.2.11` | `role: infra` | Super-seed peer — pulls from Harbor and distributes to all clients |
| Client | DaemonSet | All nodes | `dragonflyoss/client:v1.2.11` | All nodes | Local proxy (port 4001), intercepts CRI-O pulls, participates in P2P |
| MySQL | StatefulSet | 1 | `bitnamilegacy/mysql` | `role: infra` | Persistent backend storage for the manager |
| Redis | StatefulSet | 1 | `bitnamilegacy/redis` | `role: infra` | Caching and scheduling state for the scheduler |

The client (`dfdaemon`) is written in Rust (v1.2.11+), while the manager and scheduler are Go services (v2.4.2). The client DaemonSet uses `hostNetwork: true` to bind directly on port 4001 on every node.

### Persistent Storage

All PVCs use the `rook-ceph-block` StorageClass:

| Component | Size | Purpose |
|-----------|------|---------|
| Seed client | 50Gi | Cached image layers for P2P distribution |
| MySQL | 5Gi | Manager database |
| Redis | 2Gi | Scheduler state cache |

### CRI-O Mirror Integration

The `distribute_pki` Ansible role configures CRI-O on all nodes with registry mirrors that route pulls through the local Dragonfly client. This is transparent to all workloads — no image reference changes needed in manifests.

The mirror configuration at `/etc/containers/registries.conf.d/harbor-mirror.conf`:

| Upstream Registry | Mirror Location | Harbor Project |
|-------------------|----------------|----------------|
| `docker.io` | `127.0.0.1:4001/dockerhub-cache` | `dockerhub-cache` |
| `quay.io` | `127.0.0.1:4001/quay-cache` | `quay-cache` |
| `registry.k8s.io` | `127.0.0.1:4001/k8s-registry-cache` | `k8s-registry-cache` |
| `nvcr.io` | `127.0.0.1:4001/nvcr-cache` | `nvcr-cache` |

The path prefix encodes the Harbor proxy-cache project name, so Dragonfly knows which Harbor project to fetch from. Each mirror entry is marked `insecure = true` (localhost HTTP), and CRI-O automatically falls back to the upstream registry if Dragonfly is unreachable.

### Proxy Rules

Both the seed client and regular client use the same proxy configuration:

```yaml
proxy:
  rules:
    - regex: 'blobs/sha256.*'     # Only proxy blob (layer) downloads
  registryMirror:
    addr: https://harbor.k8s.local
    cert: /etc/dragonfly-certs/root-ca.crt
    enableTaskIDBasedBlobDigest: true
  disableBackToSource: false       # Fall back to upstream if P2P fails
  prefetch: true                   # Speculatively download related layers
  prefetchBandwidthLimit: 10GB     # No practical bandwidth cap on prefetch
```

The `registryMirror.cert` field points to the homelab root CA certificate (mounted from the `dragonfly-ca-cert` Secret), enabling TLS verification when Dragonfly pulls from Harbor over HTTPS.

### Client Tuning

The DaemonSet client includes additional performance and lifecycle settings:

| Setting | Value | Purpose |
|---------|-------|---------|
| `download.protocol` | `tcp` | Transfer protocol between peers |
| `download.pieceTimeout` | `360s` | Timeout per piece download |
| `download.concurrentPieceCount` | `16` | Parallel piece downloads |
| `upload.server.port` | `4000` | Port for serving pieces to peers |
| `gc.interval` | `900s` (15 min) | Garbage collection frequency |
| `gc.policy.taskTTL` | `720h` (30 days) | How long cached tasks persist |
| `gc.policy.distHighThresholdPercent` | `90` | Start GC when cache is 90% full |
| `gc.policy.distLowThresholdPercent` | `70` | GC until cache drops to 70% |
| `scheduler.scheduleTimeout` | `3h` | Max scheduling wait time |

### TLS Trust

Dragonfly components need to trust the homelab self-signed CA to communicate with Harbor over HTTPS:

1. The `bootstrap_pki_secret` Ansible role pre-creates the `dragonfly-system` namespace and a `dragonfly-ca-cert` Secret containing the root CA certificate (conditional on `ENABLE_DRAGONFLY=true`)
2. The manager, seed client, and client pods mount this Secret at `/etc/dragonfly-certs/`
3. The manager uses the CA cert for preheat job TLS verification (`job.preheat.tls.caCert`)
4. The seed client and client use it for Harbor registry mirror TLS (`registryMirror.cert`)

### Harbor Preheat

When both Dragonfly and Harbor are deployed, the Harbor bootstrap Job automatically configures preheat integration:

1. **Preheat instance**: Registers Dragonfly as a preheat provider at `http://dragonfly-manager.dragonfly-system.svc.cluster.local:65003`
2. **Preheat policies**: Creates event-based `dragonfly-preheat` policies on all 4 proxy cache projects (`dockerhub-cache`, `quay-cache`, `k8s-registry-cache`, `nvcr-cache`)
3. **Trigger**: When a new image is pushed or pulled through a proxy cache project, Harbor notifies the Dragonfly manager, which instructs seed clients to pre-cache the layers

The preheat configuration is conditional — the bootstrap Job DNS-checks for `dragonfly-manager.dragonfly-system.svc.cluster.local` and skips preheat setup if Dragonfly is not deployed. See [Harbor — Dragonfly P2P Preheat](harbor.md#dragonfly-p2p-preheat-conditional) for details.

### Tolerations

| Component | Tolerations | Reason |
|-----------|------------|--------|
| Manager, Scheduler, Seed client, MySQL, Redis | `role=infra:NoSchedule` | Infra-tier applications |
| Client (DaemonSet) | `role=*:NoSchedule` (Exists) + `control-plane:NoSchedule` (Exists) | Must run on every node including control plane |

### Important: Rust Client (v1.2.11+)

The Dragonfly client image `v1.2.11` uses the Rust-based `dfdaemon`, which has different CLI flags than the older Go-based client. Specifically:
- `verbose: false` must be set — the Rust client does not support the `--verbose` flag (use `log.level: info` instead)
- The registry mirror field is `manager.addr` (singular), not `manager.addrs` (plural) — chart version 1.6.14+ handles this correctly

## Integration Points

| Direction | Target | Purpose |
|-----------|--------|---------|
| ← CRI-O | All nodes | Registry mirrors route image pulls through local Dragonfly client (port 4001) |
| → Harbor | `harbor.k8s.local` | Seed client and clients pull images through Harbor proxy cache over HTTPS |
| ← Harbor | Manager | Harbor sends preheat webhooks when proxy cache projects receive new images |
| → Rook-Ceph | `rook-ceph-block` | Persistent volumes for seed client cache, MySQL, and Redis |
| → cert-manager | `homelab-ca-issuer` | Root CA certificate used for TLS trust (via `dragonfly-ca-cert` Secret) |
| ← Scheduler | Clients | Coordinates P2P piece distribution across all nodes |
| ← Sveltos | ClusterProfile | Deployment ordering — waits for cert-manager, trust-manager, and rook-ceph-cluster |

## Troubleshooting

### Pods not starting

```bash
# Check all Dragonfly pods
kubectl get pods -n dragonfly-system

# Check PVC status (needs rook-ceph-block StorageClass)
kubectl get pvc -n dragonfly-system

# Check CA Secret exists
kubectl get secret dragonfly-ca-cert -n dragonfly-system

# Client DaemonSet status (should match node count)
kubectl get ds -n dragonfly-system
```

### Image pulls not going through Dragonfly

```bash
# Verify CRI-O mirror config on a node
ssh <node> cat /etc/containers/registries.conf.d/harbor-mirror.conf

# Check if client is listening on port 4001
ssh <node> ss -tlnp | grep 4001

# Check client logs for proxy activity
kubectl logs -n dragonfly-system -l app=dragonfly,component=client --tail=50

# Test a pull manually and watch client logs
crictl pull docker.io/library/alpine:latest
```

### Client CrashLoopBackOff

```bash
# Check client logs for startup errors
kubectl logs -n dragonfly-system -l app=dragonfly,component=client --previous

# Common issues:
# - "unexpected argument '--verbose'" → Set verbose: false in Helm values
# - "relative URL without a base" → Upgrade chart to 1.6.14+ (addr vs addrs fix)
# - Port 4001 already in use → Check for conflicting hostNetwork services
```

### Seed client not caching

```bash
# Check seed client logs
kubectl logs -n dragonfly-system -l app=dragonfly,component=seed-client --tail=50

# Verify Harbor connectivity from seed client
kubectl exec -n dragonfly-system -it $(kubectl get pod -n dragonfly-system -l app=dragonfly,component=seed-client -o name) -- \
  wget -q --spider https://harbor.k8s.local/api/v2.0/health

# Check PVC is bound
kubectl get pvc -n dragonfly-system -l app=dragonfly,component=seed-client
```

### Preheat not working

```bash
# Check preheat instance in Harbor
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/p2p/preheat/instances | jq .

# Check preheat policies on a project
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/projects/dockerhub-cache/preheat/policies | jq .

# Check manager can reach Harbor (TLS trust)
kubectl logs -n dragonfly-system -l app=dragonfly,component=manager --tail=50 | grep -i preheat

# Re-run Harbor bootstrap to recreate preheat config
kubectl delete job harbor-bootstrap -n harbor --ignore-not-found
# Then sync the Harbor ArgoCD application
```

## Links

- [Dragonfly Documentation](https://d7y.io/docs/)
- [Dragonfly Helm Charts](https://github.com/dragonflyoss/helm-charts)
- [Dragonfly GitHub](https://github.com/dragonflyoss/Dragonfly2)
- [CRI-O Registry Mirrors](https://github.com/containers/image/blob/main/docs/containers-registries.conf.5.md)
- [Harbor Preheat (P2P) Documentation](https://goharbor.io/docs/latest/administration/p2p-preheat/)
