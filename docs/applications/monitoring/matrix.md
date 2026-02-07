# Matrix Synapse

## What It Does

Matrix Synapse is a self-hosted Matrix homeserver that provides the messaging infrastructure for alert notifications. It hosts the `#alerts` chat room where Alertmanager notifications appear, accessible from any Matrix client like Element on mobile or desktop.

## Why It's Here

The alerting stack needs a destination that supports push notifications to your phone. Rather than relying on email (slow, easy to miss) or Slack/Discord (external dependency), Matrix provides a self-hosted, open-protocol solution. You install the Element app, join the `#alerts` room, and get instant push notifications when GPU temperatures spike or pods crash.

## How It's Configured

**Deployment**: StatefulSet (`matrixdotorg/synapse:v1.98.0`) with a PostgreSQL 15 sidecar for persistence. Uses `rook-ceph-block` PVC (10Gi) for Synapse data.

> **⚠️ PostgreSQL data is ephemeral**: The PostgreSQL sidecar uses `emptyDir` storage — its data is lost when the pod restarts. Only the Synapse data directory (homeserver config, media, signing keys) is on the persistent PVC. In practice, this means a pod restart loses the message database. For a homelab alerting bot this is acceptable (the bot and room are re-created by the bootstrap job), but be aware that chat history does not survive restarts.

**Server name**: `matrix.k8s.local`

**Init containers**:
1. **`matrix-init-secret`** (sync-wave 0): Pre-Sync Job that generates the PostgreSQL credentials as a Kubernetes Secret (`matrix-db`), skipping if it already exists.
2. **`generate-config`**: Runs `synapse generate` on first boot, then ensures registration is enabled on every start (handles PVC persistence).

**Bootstrap job** (sync-wave 3, PostSync hook):
- Waits for Synapse to be healthy
- Checks if `matrix-bot` Secret already exists (idempotent)
- Extracts `registration_shared_secret` from `homeserver.yaml` inside the running pod
- Registers a bot user (`alertbot-TIMESTAMP`) using HMAC-SHA1 authentication
- Creates public `#alerts` room with world-readable history
- Saves all credentials to `matrix-bot` Secret (used by [alertmanager-matrix-bridge](alertmanager-matrix-bridge.md))

**Access**: Exposed via Cilium Ingress at `matrix.k8s.local` (port 8008 HTTP, 8448 federation).

**ArgoCD sync-wave**: 1 (deploys first so the bootstrap job can run before dependent components).

## Deployment Order

```
Wave 0: matrix-init-secret Job → creates matrix-db Secret
Wave 2: StatefulSet starts (Synapse + PostgreSQL)
Wave 3: matrix-bootstrap Job → creates bot user, #alerts room, matrix-bot Secret
Wave 4: alertmanager-matrix-bridge reads matrix-bot Secret
```

## Connecting as a User

1. Open Element (or any Matrix client)
2. Set homeserver to `http://matrix.k8s.local`
3. Register a new account (registration is enabled)
4. Join the `#alerts` room

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Alertmanager-Matrix-Bridge](alertmanager-matrix-bridge.md) | Uses `matrix-bot` Secret to post alert messages |
| [Rook-Ceph Block Storage](../storage/rook-cluster.md) | Synapse data stored on `rook-ceph-block` PVC |

## Troubleshooting

```bash
# Check Synapse pod status
kubectl get pods -n monitoring -l app=matrix
kubectl logs -n monitoring matrix-0 -c synapse --tail=30
kubectl logs -n monitoring matrix-0 -c postgres --tail=30

# Check bootstrap job status
kubectl get jobs -n monitoring -l app=matrix-bootstrap
kubectl logs -n monitoring -l job-name=matrix-bootstrap --tail=50

# Check matrix-bot Secret (created by bootstrap)
kubectl get secret matrix-bot -n monitoring
kubectl get secret matrix-bot -n monitoring -o jsonpath='{.data}' | python3 -m json.tool

# Check PostgreSQL credentials Secret
kubectl get secret matrix-db -n monitoring

# Test Synapse health
kubectl exec -n monitoring matrix-0 -c synapse -- curl -s http://localhost:8008/_matrix/client/versions
```

**Bootstrap job fails with `M_USER_IN_USE`**: The bot user already exists from a previous run. Delete the stale resources and re-sync:
```bash
kubectl delete secret matrix-bot -n monitoring
kubectl delete job matrix-bootstrap -n monitoring
```
Then re-sync the Matrix application in ArgoCD.

**Bootstrap job can't read `registration_shared_secret`**: The job exec's into the running `matrix-0` pod to read `homeserver.yaml`. Ensure the Synapse pod is Running first.

**Chat history lost after restart**: Expected — PostgreSQL uses `emptyDir`. The bootstrap job re-creates the bot and room automatically.

**Element client can't connect**: Ensure `/etc/hosts` has `matrix.k8s.local` pointing to a LoadBalancer IP. The homeserver is HTTP only (port 8008).

## Links

- [Matrix Synapse Documentation](https://element-hq.github.io/synapse/latest/)
- [Matrix Specification](https://spec.matrix.org/)
- [Element Client](https://element.io/)
