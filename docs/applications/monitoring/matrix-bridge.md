# Matrix Bridge

## What It Does

Generic webhook-to-Matrix bridge that translates incoming webhook payloads into formatted messages in the `#alerts` Matrix room. Handles two webhook sources:

1. **Alertmanager** (port 3000) — Prometheus/Thanos alert notifications via [metio/matrix-alertmanager-receiver](https://github.com/metio/matrix-alertmanager-receiver)
2. **Harbor** (port 3001) — Trivy vulnerability scan results via a lightweight Python handler (Critical severity only)

## Why It's Here

The monitoring stack needs a way to deliver notifications to humans. Rather than running separate bridges for each webhook source, this pod consolidates all webhook→Matrix translation into a single deployment with a sidecar pattern.

## How It's Configured

**Deployment**: Single replica in the `monitoring` namespace with two containers (sidecar pattern).

### Init Container: `generate-config`

Generates the Alertmanager receiver config file from the `matrix-bot` Secret:
- Reads `user-id`, `access-token`, and `room-id` from the Secret
- Writes `/config/config.yml` using a quoted heredoc (`<< 'CONFIGEOF'`) to preserve Go template syntax, then `sed` substitutes credentials into YAML-quoted values
- Generates full receiver config including `http.port`, `matrix` credentials, `room-mapping`, and `templating` (emoji-based severity formatting: ℹ️ Info, ⚠️ Warning, 🚨 Critical, ✅ Resolved)
- Uses `alpine:3.19` (no runtime dependencies needed)

### Container 1: `alertmanager` (port 3000)

- Image: `metio/matrix-alertmanager-receiver:2026.2.11`
- Invoked with `--config-path /config/config.yml` CLI argument
- Reads config from the init container's generated `/config/config.yml`
- Translates Alertmanager webhook payloads into emoji-formatted HTML messages
- Health check: `GET /healthz`

### Container 2: `harbor` (port 3001)

- Image: `python:3.12-alpine`
- Runs `harbor-webhook-handler.py` from a ConfigMap mount
- Receives Harbor `SCANNING_COMPLETED` webhook events
- **Filters**: Only sends notifications for **Critical** severity — all other severities are silently dropped
- Message format: HTML table with severity breakdown (Critical/High/Medium/Low counts), scanner name, fixable count
- Health check: `GET /healthz`

### Service

Exposes both ports under a single `matrix-bridge` Service:
- Port `3000` → Alertmanager receiver (named `alertmanager`)
- Port `3001` → Harbor receiver (named `harbor`)

### Secrets

Both containers read from the `matrix-bot` Secret (created by the [Matrix bootstrap job](matrix.md)):
- `access-token` — Bot user's Matrix access token
- `room-id` — Target room ID (`#alerts`)
- `user-id` — Bot user ID (used by alertmanager receiver config)

**ArgoCD sync-wave**: 8 (after Matrix at wave 6 ensures the `matrix-bot` Secret exists).

## Dependency Chain

```
Matrix (wave 6) → bootstrap job creates matrix-bot Secret (PostSync wave 3)
    → Matrix Bridge (wave 8) reads matrix-bot Secret
    → Alertmanager (wave 6) sends webhooks to port 3000
    → Harbor (wave 2) sends webhooks to port 3001
```

Harbor webhook policies are registered during Harbor bootstrap (wave 2 PostSync) for proxy cache projects, and by the artifact-indexer CronJob for the `cluster-images` project. The matrix-bridge pod doesn't exist until wave 8. This is fine — Harbor stores the policies in its database, and webhook deliveries for scans before wave 8 fail silently. All subsequent scans deliver once the bridge starts.

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Alertmanager](alertmanager.md) | Sends firing/resolved alert webhooks to port 3000 |
| [Harbor](../infrastructure/harbor.md) | Sends `SCANNING_COMPLETED` webhooks to port 3001 |
| [Matrix](matrix.md) | Target homeserver for message delivery; source of `matrix-bot` Secret |

## Troubleshooting

### Alertmanager receiver issues

```bash
# Check pod status
kubectl get pods -n monitoring -l app=matrix-bridge
kubectl logs -n monitoring -l app=matrix-bridge -c alertmanager --tail=30

# Verify config was generated
kubectl exec -n monitoring deploy/matrix-bridge -c alertmanager -- cat /config/config.yml

# Test health endpoint
kubectl exec -n monitoring deploy/matrix-bridge -c alertmanager -- wget -q -O- http://localhost:3000/healthz

# Check matrix-bot Secret exists
kubectl get secret matrix-bot -n monitoring
```

### Harbor receiver issues

```bash
# Check harbor sidecar logs
kubectl logs -n monitoring -l app=matrix-bridge -c harbor --tail=30

# Test health endpoint
kubectl exec -n monitoring deploy/matrix-bridge -c harbor -- wget -q -O- http://localhost:3001/healthz

# Verify webhook policies exist on Harbor projects
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
for project in dockerhub-cache quay-cache k8s-registry-cache nvcr-cache cluster-images; do
  echo "=== $project ==="
  curl -sk -u "admin:$HARBOR_PW" "https://harbor.k8s.local/api/v2.0/projects/$project/webhook/policies" | jq '.[].name'
done

# Check if webhook can reach the bridge (from any pod in the cluster)
kubectl run -n monitoring test-curl --rm -it --image=alpine -- wget -q -O- http://matrix-bridge:3001/healthz
```

**No Critical scan notifications arriving**: The handler only notifies on Critical severity. Check Harbor logs to confirm the webhook fired (`kubectl logs -n harbor deploy/harbor-core --tail=50 | grep webhook`). Verify the scan actually found Critical vulnerabilities in the Harbor UI.

**Init container failing**: The `matrix-bot` Secret may not exist yet. Ensure Matrix bootstrapped successfully (`kubectl get secret matrix-bot -n monitoring`). The Matrix bootstrap job runs as a PostSync hook at wave 6.

## Links

- [metio/matrix-alertmanager-receiver](https://github.com/metio/matrix-alertmanager-receiver)
- [Harbor Webhooks Documentation](https://goharbor.io/docs/latest/working-with-projects/project-configuration/configure-webhooks/)
- [Matrix Client-Server API](https://spec.matrix.org/latest/client-server-api/)
