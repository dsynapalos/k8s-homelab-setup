# Rook-Ceph Cluster

## What It Does

Defines the actual Ceph storage cluster running inside Kubernetes — the monitors, managers, OSDs, metadata servers, and RADOS gateway. It also creates the storage pools and StorageClasses that workloads use to request persistent volumes.

## Why It's Here

When `ENABLE_ROOK=true`, the cluster needs persistent storage without relying on external infrastructure. Rook-Ceph turns raw block devices attached to infra-role worker VMs into a fully functional distributed storage system, providing block, filesystem, and object storage — all managed through Kubernetes CRDs.

This is what actually stores data for Matrix (Synapse DB), Thanos (metric blocks), and any other stateful workload.

## How It's Configured

**Ceph version**: v19.2.3 (Squid stable)

**Multi-node replication**: All pools use 2-way replication with `failureDomain: host`, ensuring data survives the loss of an entire worker node. MON quorum (3 instances) and MGR standby provide control-plane redundancy for Ceph itself.

### Core Components

| Component | Count | Purpose |
|-----------|-------|---------|
| **MON** (Monitor) | 3 | Maintains cluster map and consensus (quorum) |
| **MGR** (Manager) | 2 (active + standby) | Metrics, dashboard, pg_autoscaler module |
| **OSD** (Object Storage Daemon) | Per-disk | One per raw block device, stores actual data |
| **MDS** (Metadata Server) | 1 active, 1 standby | Required for CephFS filesystem access |
| **RGW** (RADOS Gateway) | 2 | S3-compatible API for object storage |

### Storage Discovery

```yaml
storage:
  useAllNodes: true
  useAllDevices: false
  deviceFilter: "^sd[b-z]"
```

Discovery runs on all nodes, but placement (see below) restricts OSD pods to infra-role nodes. The `deviceFilter` matches `/dev/sdb`, `/dev/sdc`, etc. (excludes `/dev/sda` OS disk). Only infra-role nodes have secondary disks attached during VM provisioning, so platform nodes have no matching devices.

### Config Override

```ini
[global]
osd_pool_default_size = 2
osd_pool_default_min_size = 1
```

2-way replication by default. `min_size = 1` allows degraded I/O when one replica is temporarily unavailable (e.g., during OSD restart), preventing a single node outage from blocking all writes.

### Dashboard

Enabled on HTTP (no SSL) for local access:
```bash
kubectl port-forward -n rook-ceph svc/rook-ceph-mgr-dashboard 8443:8443
```

## Storage Classes

Three StorageClasses are created for different workload needs:

### Block Storage (RBD)
| Property | Value |
|----------|-------|
| StorageClass | `rook-ceph-block` |
| Pool | `replicapool` |
| Filesystem | ext4 |
| Reclaim | Delete |
| Expansion | Yes |
| **Use case** | Database volumes, StatefulSet data (Matrix, Thanos) |

### Filesystem Storage (CephFS)
| Property | Value |
|----------|-------|
| StorageClass | `rook-cephfs` |
| Filesystem | `cephfs` |
| Access | ReadWriteMany |
| Reclaim | Delete |
| Expansion | Yes |
| **Use case** | Shared storage across pods, log aggregation |

### Object Storage (S3)
| Property | Value |
|----------|-------|
| StorageClass | `rook-ceph-bucket` |
| Object Store | `rook-ceph-rgw` |
| Protocol | S3-compatible HTTP API (port 80) |
| Reclaim | Delete |
| **Use case** | Thanos metric blocks, backups, artifacts |

## Consumers in This Cluster

| Application | StorageClass | Size | Purpose |
|-------------|-------------|------|---------|
| [Matrix](../monitoring/matrix.md) | `rook-ceph-block` | 10Gi | Synapse data directory |
| [Thanos Receive](../monitoring/thanos.md) | `rook-ceph-block` | 10Gi | TSDB buffer before S3 upload |
| [Thanos Store](../monitoring/thanos.md) | `rook-ceph-block` | 5Gi | Block metadata cache |
| [Thanos Compactor](../monitoring/thanos.md) | `rook-ceph-block` | 5Gi | Compaction working directory |
| [Thanos](../monitoring/thanos.md) | `rook-ceph-bucket` | Dynamic | Long-term metric storage (S3) |

## Placement

All Ceph components are placed on infra-role worker nodes using both node affinity and taint tolerations:

```yaml
placement:
  all:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: role
            operator: In
            values:
            - infra
    tolerations:
    - key: role
      operator: Equal
      value: infra
      effect: NoSchedule
    - key: node.kubernetes.io/not-ready
      operator: Exists
      effect: NoSchedule
    - key: node.kubernetes.io/unreachable
      operator: Exists
      effect: NoSchedule
```

The `not-ready` and `unreachable` tolerations ensure Ceph pods stay running during node disruptions (important for storage availability). The `CephObjectStore` and `CephFilesystem` CRs have matching placement to keep RGW and MDS pods on infra nodes as well.

## Expected Health Warnings

These are normal and expected:
- `MON_CLOCK_SKEW` — minor clock drift between nodes (resolve with NTP if persistent)

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Rook Operator](rook-operator.md) | Operator reconciles this CephCluster CR |
| [Matrix](../monitoring/matrix.md) | Block PVC for Synapse data |
| [Thanos](../monitoring/thanos.md) | Block PVCs for local data + S3 bucket for long-term storage |
| `setup_localhost` role | Provisions secondary disks on Proxmox for infra-role VMs that become Ceph OSDs |

## Troubleshooting

```bash
# Check CephCluster status
kubectl get cephcluster -n rook-ceph
kubectl describe cephcluster rook-ceph -n rook-ceph | tail -20

# Ceph health from tools pod
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph status
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph health detail
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd tree

# Check individual components
kubectl get pods -n rook-ceph -l app=rook-ceph-mon
kubectl get pods -n rook-ceph -l app=rook-ceph-mgr
kubectl get pods -n rook-ceph -l app=rook-ceph-osd
kubectl get pods -n rook-ceph -l app=rook-ceph-mds
kubectl get pods -n rook-ceph -l app=rook-ceph-rgw

# Check pool status
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd pool ls detail
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph mds stat

# Dashboard access
kubectl port-forward -n rook-ceph svc/rook-ceph-mgr-dashboard 8443:8443

# Check StorageClasses
kubectl get storageclass | grep rook

# Verify block devices on worker node
ssh <worker-node> "lsblk"
```

**CephCluster stuck in "Creating"**: Initial OSD creation takes 5–10 minutes. Check operator logs: `kubectl logs -n rook-ceph -l app=rook-ceph-operator --tail=50`. If stuck longer, verify disks are raw on the worker node.

**OSD not starting**: Check that `lsblk` on the worker shows `/dev/sdb`, `/dev/sdc`, etc. matching the `deviceFilter: "^sd[b-z]"` pattern. Any leftover LVM signatures prevent OSD creation.

**`POOL_NO_REDUNDANCY` warning**: Expected for single-node clusters — pools have `size=1`.

**`MDS_UP_LESS_THAN_MAX` warning**: Expected — no standby MDS is configured for the homelab.

**PVC stuck in Pending**: Check that the corresponding CSI provisioner pod is running and the pool has available space: `kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph df`.

## Links

- [Rook Ceph Cluster CRD](https://rook.io/docs/rook/latest/CRDs/Cluster/ceph-cluster-crd/)
- [Rook Block Storage](https://rook.io/docs/rook/latest/Storage-Configuration/Block-Storage-RBD/block-storage/)
- [Rook Shared Filesystem](https://rook.io/docs/rook/latest/Storage-Configuration/Shared-Filesystem-CephFS/filesystem-storage/)
- [Rook Object Storage](https://rook.io/docs/rook/latest/Storage-Configuration/Object-Storage-RGW/object-storage/)
- [Ceph Documentation](https://docs.ceph.com/en/latest/)
