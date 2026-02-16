# Alertmanager

## What It Does

Alertmanager receives firing alerts from Prometheus, deduplicates them, groups related alerts together, and routes them to notification channels. In this environment, it routes all alerts to a Matrix chat room via the [matrix-bridge](matrix-bridge.md).

## Why It's Here

Metrics without alerting are just dashboards you'd need to watch 24/7. Alertmanager closes the loop by turning threshold violations into push notifications you receive on your phone via the Element Matrix client.

## How It's Configured

**Deployment**: Single replica (`prom/alertmanager:v0.31.1`) in the `monitoring` namespace with `emptyDir` storage. This means alert state (silences, notification log) is lost on pod restart — acceptable for a homelab where alerts are transient.

**Routing**:
- All alerts are grouped by `alertname`, `cluster`, and `service`
- Group wait: 10s (how long to buffer before sending a group)
- Repeat interval: 12h (don't re-fire the same alert within this window)
- Single receiver: `matrix` → webhook at `http://matrix-bridge:3000/alerts/default`
- `send_resolved: true` — sends recovery notifications when alerts clear

**ArgoCD sync-wave**: 2 (deploys after Matrix homeserver is ready at wave 1).

## Alert Flow

```
Thanos Ruler evaluates rules (via Thanos Query) → Alertmanager groups & deduplicates
    → Webhook POST to matrix-bridge (alertmanager receiver)
    → Matrix message to #alerts room
    → Element app push notification on your phone
```

## Currently Active Alert Rules

| Alert | Condition | Severity |
|-------|-----------|----------|
| `GPUHighTemperature` | `DCGM_FI_DEV_GPU_TEMP > 60` for 5 min | warning |

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Thanos Ruler](thanos.md#thanos-ruler-statefulset) | Sends firing/resolved alerts (replaced Prometheus rule evaluation) |
| [Matrix Bridge](matrix-bridge.md) | Receives webhooks and translates to Matrix messages |

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n monitoring -l app=alertmanager
kubectl logs -n monitoring -l app=alertmanager --tail=30

# Access Alertmanager UI
kubectl port-forward -n monitoring svc/alertmanager 9093:9093
# Then open http://localhost:9093

# Check active alerts
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool | head -40

# Check silences
curl -s http://localhost:9093/api/v2/silences | python3 -m json.tool

# Verify webhook receiver is reachable
kubectl exec -n monitoring deploy/alertmanager -- wget -q -O- http://matrix-bridge:3000/healthz 2>&1 || echo "Bridge not reachable"
```

**Alerts not firing**: Check that Thanos Ruler is running (`kubectl get pods -n monitoring -l app=thanos-ruler`) and rules are loaded (Ruler UI on port 10902 → Rules). Ensure the alert condition is actually met — the threshold may not be exceeded yet.

**Alerts firing but no Matrix notification**: Verify the `matrix-bridge` pod is running (sync-wave 8). Check the `matrix-bot` Secret exists. See [Matrix Bridge troubleshooting](matrix-bridge.md#troubleshooting).

**Alert state lost after restart**: Expected — Alertmanager uses `emptyDir`, so silences and notification history don't survive pod restarts.

## Links

- [Alertmanager Documentation](https://prometheus.io/docs/alerting/latest/alertmanager/)
- [Alertmanager Configuration](https://prometheus.io/docs/alerting/latest/configuration/)
- [Alerting Rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
