# Alertmanager-Matrix-Bridge

## What It Does

Translates Alertmanager webhook payloads into formatted Matrix messages. It sits between Alertmanager and the Matrix homeserver, converting JSON alert data into human-readable, emoji-annotated HTML messages posted to the `#alerts` room.

## Why It's Here

Alertmanager speaks webhooks; Matrix speaks its own client-server API. This bridge connects the two so you get real-time alert notifications on any Matrix client (Element on mobile/desktop) without writing custom glue code.

## How It's Configured

**Deployment**: Single replica (`metio/matrix-alertmanager-receiver:2025.11.5`) in the `monitoring` namespace.

**Config generation**: An init container (`busybox:1.36`) dynamically builds `config.yaml` at startup using values from the `matrix-bot` Secret (created by the [Matrix bootstrap job](matrix.md)). This avoids hardcoding credentials.

**Message formatting** (emoji-based severity):

| Status | Emoji | Color |
|--------|-------|-------|
| Info | ℹ️ | white |
| Warning | ⚠️ | orange |
| Critical | 🚨 | red |
| Resolved | ✅ | green |

Messages include alert annotations, and filtered labels (`alertname`, `severity`, `namespace`).

**Endpoint**: Listens on port 3000 at `/alerts/default`.

**ArgoCD sync-wave**: 4 (deploys last — after Matrix bootstrap job creates the `matrix-bot` Secret at wave 3).

## Dependency Chain

This is the last component in the alerting pipeline and has strict ordering requirements:

```
Wave 1: Matrix homeserver starts
Wave 2: Alertmanager + Prometheus deploy
Wave 3: Matrix bootstrap job creates bot user + #alerts room → matrix-bot Secret
Wave 4: This bridge reads matrix-bot Secret and starts
```

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Alertmanager](alertmanager.md) | Sends webhook POSTs to this bridge |
| [Matrix](matrix.md) | Bridge posts formatted messages using bot credentials |

## Troubleshooting

```bash
# Check pod status (should be Running after wave 4)
kubectl get pods -n monitoring -l app=alertmanager-matrix
kubectl logs -n monitoring -l app=alertmanager-matrix --tail=30

# Check init container logs (config generation)
kubectl logs -n monitoring -l app=alertmanager-matrix -c init-config

# Verify matrix-bot Secret exists (created by Matrix bootstrap at wave 3)
kubectl get secret matrix-bot -n monitoring

# Check the generated config inside the pod
kubectl exec -n monitoring deploy/alertmanager-matrix -- cat /config/config.yaml

# Test webhook endpoint from Alertmanager
kubectl exec -n monitoring deploy/alertmanager -- wget -q -O- http://alertmanager-matrix:3000/healthz 2>&1
```

**Pod stuck in Init**: The init container reads from `matrix-bot` Secret. If the Matrix bootstrap job (wave 3) hasn't run yet, the Secret doesn't exist. Check the bootstrap job status: `kubectl get jobs -n monitoring -l app=matrix-bootstrap`.

**Config generation fails**: The init container uses `sed` with YAML-quoted values. Special characters (`@`, `!`, `:`) in bot credentials must be quoted properly. Check init container logs for sed errors.

**Messages not appearing in Matrix room**: Verify the bridge can reach Synapse: `http://matrix.monitoring.svc.cluster.local:8008`. Check that the room-id in the Secret matches an existing room.

## Links

- [matrix-alertmanager-receiver (GitHub)](https://github.com/metio/matrix-alertmanager-receiver)
- [Alertmanager Webhook Configuration](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)
