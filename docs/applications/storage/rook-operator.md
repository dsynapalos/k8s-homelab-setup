# Rook-Ceph Operator

## What It Does

The Rook Operator is a Kubernetes controller that automates the deployment, configuration, and lifecycle management of a Ceph storage cluster inside Kubernetes. It watches for Rook CRDs (like `CephCluster`, `CephBlockPool`, `CephFilesystem`) and translates them into running Ceph daemons.

## Why It's Here

Running Ceph manually is complex — it involves configuring monitors, managers, OSDs, and MDS servers across nodes. The Rook Operator automates all of this declaratively. When `ENABLE_ROOK=true`, the operator is deployed first (sync-wave 1) so that the [Rook-Ceph Cluster](rook-cluster.md) resources can be created on top of it.

This is a prerequisite for all in-cluster persistent storage: block volumes, shared filesystems, and S3-compatible object storage.

## How It's Configured

**Deployment**: Pulled directly from upstream Rook v1.19.1 manifests (CRDs, common resources, operator) with a single ConfigMap patch for cluster-specific tuning.

**Kustomize sources** (from GitHub):
- `rook/rook/v1.19.1/deploy/examples/crds.yaml` — Custom Resource Definitions
- `rook/rook/v1.19.1/deploy/examples/common.yaml` — RBAC, ServiceAccounts, namespaces
- `rook/rook/v1.19.1/deploy/examples/operator.yaml` — Operator Deployment

**Operator config overrides** (`operator-config-patch.yaml`):

| Setting | Value | Reason |
|---------|-------|--------|
| `CSI_PROVISIONER_REPLICAS` | `1` | Single-node cluster, no need for HA provisioner |
| `CSI_ENABLE_CEPHFS_SNAPSHOTTER` | `true` | Enable CephFS volume snapshots |
| `CSI_ENABLE_RBD_SNAPSHOTTER` | `true` | Enable block volume snapshots |
| `ROOK_ENABLE_DISCOVERY_DAEMON` | `true` | Auto-detects raw block devices on worker nodes |
| `ROOK_CSI_ENABLE_HOST_NETWORK` | `false` | Pod network is sufficient for test clusters |

**ArgoCD sync policy**: `prune: false` (never auto-delete the operator), `selfHeal: true`.

## What It Manages

Once running, the operator watches for and reconciles:
- `CephCluster` → deploys MON, MGR, OSD daemons
- `CephBlockPool` → creates RBD pools for block storage
- `CephFilesystem` → creates CephFS with MDS servers
- `CephObjectStore` → deploys RADOS Gateway for S3 API
- CSI driver pods for mounting volumes into workloads

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Rook-Ceph Cluster](rook-cluster.md) | Operator reconciles the CephCluster and storage pool CRDs |
| ArgoCD | Deployed as sync-wave 1; cluster resources deploy at wave 2 |

## Troubleshooting

```bash
# Check operator pod
kubectl get pods -n rook-ceph -l app=rook-ceph-operator
kubectl logs -n rook-ceph -l app=rook-ceph-operator --tail=50

# Check CRDs are installed
kubectl get crd | grep ceph

# Check CSI driver pods
kubectl get pods -n rook-ceph -l app=csi-rbdplugin
kubectl get pods -n rook-ceph -l app=csi-cephfsplugin

# Check discovery daemon (finds raw block devices)
kubectl get pods -n rook-ceph -l app=rook-discover
kubectl logs -n rook-ceph -l app=rook-discover --tail=20

# Check ArgoCD sync status
kubectl get application rook-operator -n argocd
```

**Operator pod CrashLoopBackOff**: Check logs for missing CRDs or RBAC issues. The operator needs cluster-admin level permissions.

**CSI pods not starting**: Check `CSI_PROVISIONER_REPLICAS` in the operator ConfigMap. For single-node clusters, this should be `1`.

**Discovery daemon not finding disks**: Disks must be raw (no LVM, partitions, or filesystem signatures). See [Storage — Troubleshooting > Rook-Ceph](../../infrastructure/storage.md#rook-ceph).

## Links

- [Rook Documentation](https://rook.io/docs/rook/latest/)
- [Rook Operator Configuration](https://rook.io/docs/rook/latest/Storage-Configuration/Advanced/ceph-configuration/)
- [Rook GitHub Repository](https://github.com/rook/rook)
- [Ceph Documentation](https://docs.ceph.com/en/latest/)
