# Troubleshooting

## What This Document Covers

Common failure modes and how to debug them, organized by component. Start with the general debugging section if you're not sure where to look.

---

## General Debugging

### Ingress Access (*.k8s.local hostnames)

All web UIs (ArgoCD, Grafana, Harbor, Prometheus, Hubble, Matrix, Thanos) are exposed via Cilium Ingress using `*.k8s.local` hostnames with TLS certificates from cert-manager. HTTP requests are automatically redirected to HTTPS (via `ingress.cilium.io/force-https`). These are **not real DNS names** — you must add them to your workstation's `/etc/hosts`:

```bash
# Replace the IP with one from your CILIUM_LOADBALANCER_IPPOOL range
# Find the actual IP: kubectl get ingress -A
192.168.1.193  argocd.k8s.local grafana.k8s.local harbor.k8s.local prometheus.k8s.local thanos.k8s.local hubble.k8s.local matrix.k8s.local keycloak.k8s.local
```

If you can reach the cluster via `kubectl` but can't open any web UI, this is almost certainly the issue.

Since the CA is self-signed, browsers will show a certificate warning. To remove it, import the CA certificate into your trust store — see [cert-manager — Troubleshooting](../applications/security/cert-manager.md#troubleshooting).

### ArgoCD Login

ArgoCD generates a random admin password on first install:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

Username is `admin`. TLS is terminated at the Cilium Ingress using a cert-manager-provisioned certificate. The CA is self-signed — expect a browser warning unless you've imported the CA cert (see [cert-manager](../applications/security/cert-manager.md#troubleshooting)).

### Ansible Runner Artifacts

Every run cleans and repopulates the `artifacts/` directory. After a failure, check:

| File | What's In It |
|------|-------------|
| `artifacts/*/stdout` | Full playbook output — start here |
| `artifacts/*/stderr` | Error messages from failed commands |
| `artifacts/*/job_events/*.json` | Per-task execution timeline with timing |

### Quick Health Checks

```bash
# Environment configuration
grep -E "^(K8S_|PROXMOX_|ENABLE_)" .env

# Node connectivity (replace with IPs from .env)
ssh -i ~/.ssh/id_rsa k8s@<node-ip> "hostname && uptime"

# Cluster status
kubectl get nodes
cilium status --wait
```

### Common Root Causes

**Template error / undefined variable**: A missing `.env` variable causes Ansible to fail at template rendering. Check `example.env` for the full list of expected variables. Compare your `.env` against `example.env` to catch missing variables early: `diff <(grep -oP '^[A-Z_]+' example.env | sort) <(grep -oP '^[A-Z_]+' .env | sort)`.

**SSH connection refused**: Verify the `K8S_SSH_KEY` path exists and the `K8S_SSH_USER` has authorized the corresponding public key on the target. Test manually with `ssh -i <key> <user>@<ip>`.

**Label aggregation issues**: If node labels aren't being applied correctly, the issue may be in `aggregate_labels.yaml` (part of `provision_infra`), which merges labels from multiple inventory group levels. Check that your host-level labels in `inventory/k8s.yaml` are under the correct host definition.

---

## VM Provisioning

**API authentication fails**:
- Verify `PROXMOX_API_USER`, `PROXMOX_API_PASSWORD`, and `PROXMOX_API_HOST` in `.env`
- Test manually: `curl -k https://<host>:8006/api2/json/access/ticket -d 'username=root@pam&password=<pass>'`

**VM creation hangs**:
- Check ISO exists in Proxmox storage (the automation uploads it, but if storage is full it fails silently)
- Ensure enough resources (disk, memory, CPU) on the Proxmox node
- Check Proxmox task log: Datacenter → Node → Tasks

**VM gets DHCP but static IP fails**:
- Verify `VM_GATEWAY` and `VM_NAMESERVER` in `.env`
- SSH into the VM manually and check `/etc/netplan/` for malformed config
- Run `netplan apply` and check `ip addr`

---

## Kubernetes Cluster

**kubeadm init fails**:
- Ensure swap is disabled: `swapon -s` should be empty
- Check CRI-O is running: `systemctl status crio`
- Verify kubelet: `journalctl -u kubelet -f`

**Node won't join**:
- Tokens expire after 24 hours — the playbook generates a fresh one each run
- Check that the node can reach the API server: `nc -zv <control-plane-ip> 6443`
- Firewall: ensure port 6443 is open on the control plane

**kubeconfig not found on localhost**:
- The playbook fetches it from `/etc/kubernetes/admin.conf` on the control plane
- Check SSH connectivity to the control plane node
- Look for it at `~/.kube/config`

---

## Networking (Cilium)

See [Networking — Troubleshooting > Cilium](networking.md#cilium) for diagnostic commands and common issues (DNS failures, pods stuck in ContainerCreating, LoadBalancer IPs not reachable, WireGuard status).

**Cilium pods slow to start on first boot**: During initial cluster bootstrap, Harbor proxy-cache mirrors are unreachable because CoreDNS depends on Cilium. Two OS-level tunings (`net.ipv4.tcp_syn_retries=3` and CRI-O `pull_progress_timeout=2m`) ensure CRI-O falls back to upstream registries within seconds. If you still see stalls, check `journalctl -u crio -f` for image pull errors and verify the node has upstream internet access.

**Hubble Relay not ready after install**: This is expected. Hubble Relay depends on a TLS certificate generated by a CronJob (`hubble-generate-certs`). The relay comes up automatically once the `hubble-relay-client-certs` secret exists. To force immediate cert generation: `kubectl create job --from=cronjob/hubble-generate-certs hubble-generate-certs-manual -n kube-system`.

---

## Istio Ambient

See [Networking — Troubleshooting > Istio Ambient](networking.md#istio-ambient) for diagnostic commands and common issues (x86-64-v2 CPU errors, ztunnel authentication failures, namespace enrollment, mTLS modes).

---

## GPU

See [GPU Support — Troubleshooting](gpu-support.md#troubleshooting) for diagnostic commands and common issues (PCI passthrough, driver installation, CRI-O runtime config, device plugin, DCGM Exporter, metric deduplication).

---

## CephFS Storage

See [Storage — Troubleshooting > CephFS CSI](storage.md#cephfs-csi) for diagnostic commands and common issues (PVC stuck in Pending, "Operation not permitted" with CSI v3.12+, Ceph kernel module, monitor connectivity).

---

## Rook-Ceph

See [Storage — Troubleshooting > Rook-Ceph](storage.md#rook-ceph) for diagnostic commands and common issues (OSD not starting, CephCluster stuck in Creating, CSI provisioning, dashboard access, Ceph health status). For disk cleanup when re-provisioning, see [Storage — Cleanup for Re-provisioning](storage.md#cleanup-for-re-provisioning).

---

## Matrix / Alerting Stack

See the individual application docs for diagnostic commands and common issues:
- [Matrix Synapse](../applications/monitoring/matrix.md#troubleshooting) — bootstrap job failures, bot registration, PostgreSQL state
- [Alertmanager](../applications/monitoring/alertmanager.md#troubleshooting) — alert routing, webhook delivery, state persistence
- [Matrix Bridge](../applications/monitoring/matrix-bridge.md#troubleshooting) — config generation, Secret dependency, Alertmanager + Harbor webhook delivery

---

## Monitoring Stack

See the individual application docs for diagnostic commands and common issues:
- [OTel Collector](../applications/monitoring/otel-collector.md#troubleshooting) — Prometheus scraping, remote write to Thanos, pipeline health
- [Prometheus](../applications/monitoring/prometheus.md#troubleshooting) — *(deprecated, replaced by OTel Collector)*
- [Grafana](../applications/monitoring/grafana.md#troubleshooting) — datasource configuration, dashboard provisioning
- [Thanos](../applications/monitoring/thanos.md#troubleshooting) — remote write from OTel Collector, S3 bucket, component health
- [DCGM Exporter](../applications/monitoring/dcgm-exporter.md#troubleshooting) — GPU metrics, scheduling, deduplication
- [Node Exporter](../applications/monitoring/node-exporter.md#troubleshooting) — DaemonSet status, collector errors

---

## cert-manager / TLS Certificates

See [cert-manager — Troubleshooting](../applications/security/cert-manager.md#troubleshooting) for diagnostic commands and common issues (ClusterIssuer not ready, certificates stuck in Pending, webhook validation errors, browser trust warnings).

---

## Keycloak / Identity Management

See [Keycloak — Troubleshooting](../applications/security/keycloak.md#troubleshooting) for diagnostic commands and common issues (CrashLoopBackOff, CNPG Cluster provisioning, PostgreSQL TLS, admin login, Ingress TLS).

---

## Harbor / Container Registry

See [Harbor — Troubleshooting](../applications/infrastructure/harbor.md#troubleshooting) for diagnostic commands and common issues (proxy cache project creation, OIDC configuration, admin password retrieval, bootstrap Job failures, image pull through cache).

---

## Vulnerability Scanning & CVE Reporting

See [Security — Troubleshooting](security.md#troubleshooting) for diagnostic commands and common issues (CVE reporter not posting, empty scan results, signature verification failures).

---

## CloudNativePG / PostgreSQL Operator

See [CloudNativePG — Troubleshooting](../applications/storage/cloudnative-pg.md#troubleshooting) for diagnostic commands and common issues (operator startup, Cluster stuck in Creating, connection refused, credentials not available).

---

## trust-manager / CA Distribution

See [trust-manager — Troubleshooting](../applications/security/trust-manager.md#troubleshooting) for diagnostic commands and common issues (ConfigMap not appearing, ArgoCD OIDC TLS errors, Bundle not ready).

---

## ArgoCD

See [ArgoCD GitOps — Troubleshooting](../cicd/gitops.md#troubleshooting) for sync failures, SSH deploy key issues, and repository access errors.
