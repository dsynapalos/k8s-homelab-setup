# Rook-Ceph Operator

## What It Does

The Rook Operator is a Kubernetes controller that automates the deployment, configuration, and lifecycle management of a Ceph storage cluster inside Kubernetes. It watches for Rook CRDs (like `CephCluster`, `CephBlockPool`, `CephFilesystem`) and translates them into running Ceph daemons.

## Why It's Here

Running Ceph manually is complex — it involves configuring monitors, managers, OSDs, and MDS servers across nodes. The Rook Operator automates all of this declaratively. When `ENABLE_ROOK=true`, the operator is deployed first (sync-wave 1) so that the [Rook-Ceph Cluster](rook-cluster.md) resources can be created on top of it.

This is a prerequisite for all in-cluster persistent storage: block volumes, shared filesystems, and S3-compatible object storage.

## How It's Configured

**Deployment**: Pulled directly from upstream Rook v1.19.1 example manifests (CRDs, common resources, CSI operator, Rook operator) with a single ConfigMap patch for cluster-specific tuning.

> **Note**: The `deploy/examples/` manifests are reference/starter files, not production-grade configs. They ship with generic defaults and bundle entire CRD sets, RBAC, and Deployments into single large files. This is fine for a homelab, but for production use consider migrating to the official [Rook Helm Charts](#future-upgrade-path-helm-charts) which provide templated configuration, cleaner upgrade diffs, and `values.yaml`-based customization.

**Kustomize sources** (from GitHub):
- `rook/rook/v1.19.1/deploy/examples/crds.yaml` — Custom Resource Definitions
- `rook/rook/v1.19.1/deploy/examples/common.yaml` — RBAC, ServiceAccounts, namespaces
- `rook/rook/v1.19.1/deploy/examples/csi-operator.yaml` — Ceph CSI Operator CRDs, RBAC, and Deployment (required since v1.16+; the Rook operator delegates CSI management to this component via `ROOK_USE_CSI_OPERATOR: "true"`)
- `rook/rook/v1.19.1/deploy/examples/operator.yaml` — Rook Operator Deployment and default ConfigMap

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
- [Rook Operator Helm Chart](https://rook.io/docs/rook/latest/Helm-Charts/operator-chart/)
- [Rook Cluster Helm Chart](https://rook.io/docs/rook/latest/Helm-Charts/ceph-cluster-chart/)

## Future Upgrade Path: Helm Charts

The current deployment uses raw upstream example manifests via Kustomize remote URLs. To migrate to the official Helm charts for better upgrade management:

### 1. Add the Rook Helm repository

```bash
helm repo add rook-release https://charts.rook.io/release
helm repo update
```

### 2. Replace the operator Kustomize deployment with Helm

Replace the ArgoCD Application source in `roles/bootstrap_rook_ceph/files/rook_operator_manifest.yaml`:

```yaml
spec:
  source:
    chart: rook-ceph
    repoURL: https://charts.rook.io/release
    targetRevision: v1.19.1  # or latest
    helm:
      valuesObject:
        csi:
          provisionerReplicas: 1
          enableCephfsSnapshotter: true
          enableRBDSnapshotter: true
        enableDiscoveryDaemon: true
```

This replaces the Kustomize `resources:` + `patches:` approach entirely.

### 3. Replace the cluster Kustomize deployment with Helm

Replace the ArgoCD Application source in `roles/bootstrap_rook_ceph/files/rook_cluster_manifest.yaml`:

```yaml
spec:
  source:
    chart: rook-ceph-cluster
    repoURL: https://charts.rook.io/release
    targetRevision: v1.19.1  # or latest
    helm:
      valuesObject:
        cephClusterSpec:
          cephVersion:
            image: quay.io/ceph/ceph:v19.2.0
          mon:
            count: 1
            allowMultiplePerNode: false
          mgr:
            count: 1
          storage:
            useAllNodes: false
            useAllDevices: false
            nodes:
              - name: k8s-node-1
                deviceFilter: "^sd[b-z]"
```

### 4. Remove the Kustomize manifests

After confirming the Helm-based deployment works, remove:
- `argocd_applications/storage/rook-operator/` (entire directory)
- `argocd_applications/storage/rook-cluster/` (entire directory)

### Benefits of migration

- **Version upgrades**: Change `targetRevision` in one place; Helm handles CRD/RBAC changes
- **Cleaner diffs**: `values.yaml` shows only your overrides, not the full upstream manifest
- **Rollback**: Helm tracks release history for easy rollbacks
- **Validation**: Helm charts include schema validation for values
