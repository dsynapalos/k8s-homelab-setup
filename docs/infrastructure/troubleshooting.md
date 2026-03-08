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

**SSH connection refused**: Verify the `K8S_SSH_KEY` path exists and the `K8S_SSH_USER` has authorized the corresponding public key on the target. Test manually with `ssh -i <key> <user>@<ip>`. Note: SSH host key checking is fully disabled via `ansible_ssh_common_args` in inventory (`-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`), so stale `known_hosts` entries from VM recreation are never a problem.

**Label aggregation issues**: If node labels aren't being applied correctly, the issue may be in `aggregate_labels.yaml` (part of `provision_infra`), which merges labels from multiple inventory group levels. Check that your host-level labels in `inventory/k8s.yaml` are under the correct host definition.

**Taint management issues**: If pods are stuck in `Pending` with `node(s) had untolerated taint`, check:
- The pod spec includes a toleration matching the target node's taint (`role=infra:NoSchedule` or `role=platform:NoSchedule`)
- For upstream apps, verify the Kustomize strategic-merge patch or Helm values include the toleration
- Check current taints on nodes: `kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints`
- Taint aggregation uses the `taints` list in `inventory/k8s.yaml` — verify the taint definition is under the correct host

**Intermittent network failures during provisioning**: All network-dependent operations across the pipeline are protected with automatic retries:

| Layer | Mechanism | Detail |
|-------|-----------|--------|
| Ubuntu autoinstall (ISO) | `early-commands` + apt retry config | Waits for NIC carrier, runs `dhclient`, verifies DNS, writes `Acquire::Retries "5"` to live system so curtin inherits retries |
| Ubuntu autoinstall (target) | `apt.conf` + `late-commands` | `Acquire::Retries "5"` during install, persisted as `Acquire::Retries "3"` post-install |
| Ansible `apt` tasks | `retries: 5`, `delay: 10` | Package installs, upgrades, GPG key downloads |
| Ansible `get_url`/`kubernetes.core.k8s` (remote URLs) | `retries: 5`, `delay: 15` | ArgoCD manifests, Gateway API CRDs |
| Ansible `helm_repository` | `retries: 5`, `delay: 15` | Cilium, Istio, Sveltos, Ceph CSI chart repos |
| Ansible `helm` installs | `retries: 5`, `delay: 15` | All Helm chart deployments |

**Autoinstall stuck in "network send update change" loop**: On Proxmox with virtio NICs, subiquity's network controller can get stuck re-processing link-state change events from the vNIC — visible in the VM console as repeated `start subiquity network send update change enp6s18` / `finish` messages. The autoinstall ISO includes `early-commands` that run before subiquity probes network devices: they bring the NIC up, acquire a DHCP lease via `dhclient`, and verify DNS resolution. By the time subiquity starts, the interface is already stable and configured.

**Autoinstall fails with curtin exit status 100**: This is curtin's `apt-get install --download-only` failing (typically for `linux-generic` or `qemu-guest-agent`). The `apt` section in autoinstall only configures the **target** system's apt — curtin runs in the **live** installer environment. The `early-commands` fix this by writing `Acquire::Retries "5"` to `/etc/apt/apt.conf.d/99-retries` in the live system before curtin runs.

If a provisioning run fails with network errors despite retries, check:
- DNS resolution from the host: `dig gr.archive.ubuntu.com`
- Internet connectivity from VMs: `ssh <vm-ip> "curl -s https://helm.cilium.io/"`
- Proxmox bridge/firewall not dropping packets: `journalctl -u pvedaemon` on the Proxmox host
- If the failure happened during autoinstall (before SSH is available), the old ISO may not include the latest fixes — delete cached ISOs from both Proxmox hosts and locally, then re-run:
  ```bash
  ssh root@<pve-ip> "rm -f /var/lib/vz/template/iso/ubuntu-*-autoinstall.iso"
  sudo rm -f roles/setup_localhost/files/iso/ubuntu-*-autoinstall.iso
  ```

---

## VM Provisioning

**API authentication fails**:
- Verify `PROXMOX_API_USER`, `PROXMOX_API_PASSWORD`, and `PROXMOX_API_HOST_1`/`PROXMOX_API_HOST_2` in `.env`
- Test manually: `curl -k https://<host>:8006/api2/json/access/ticket -d 'username=root@pam&password=<pass>'`
- If using multiple Proxmox hosts, test each one independently`

**VM creation fails with VMID conflict**: With `strategy: free`, VMs on the same Proxmox node can request the same VMID concurrently. The `create_vm.py` script retries up to 5 times with exponential backoff when it detects an "already exists" error, automatically re-fetching a new VMID. The stagger delay interleaves clusters (e.g., cluster_1 at 0s, cluster_2 at 5s, cluster_1 at 10s, ...) so same-host VMs are always well-separated. If you still see failures, check that Proxmox has enough free VMIDs in its range.

**VM creation hangs**:
- Check ISO exists in Proxmox storage (the automation uploads it, but if storage is full it fails silently)
- Ensure enough resources (disk, memory, CPU) on the Proxmox node
- Check Proxmox task log: Datacenter → Node → Tasks

**VM never gets a DHCP address (IP poll timeout)**: If `poll_for_ip.py` times out after 20 minutes, the provisioning pipeline automatically retries the autoinstall. The `reinstall_vm.py` script sets `boot=ide2;scsi0` (ISO first) and `reboot=0` (VM halts on guest reboot), stops the VM, starts it to re-run the unattended autoinstall, then waits for the VM to halt when the installer finishes and triggers a guest reboot. Because `reboot=0` causes Proxmox to stop the VM instead of rebooting it, the script can then revert `boot=scsi0;ide2` (disk first) and `reboot=1` while the VM is stopped — ensuring the config changes apply immediately rather than going into Proxmox's pending state. The VM is then started from disk. A second 20-minute IP poll follows automatically. If the retry also fails, the play fails and you should investigate the VM console via the Proxmox UI for installation errors.

**VM gets DHCP but static IP fails**:
- Verify `VM_GATEWAY` and `VM_NAMESERVER` in `.env`
- SSH into the VM manually and check `/etc/netplan/` for malformed config
- Run `netplan apply` and check `ip addr`

---

## Kubernetes Cluster

**kubeadm init fails**:
- All kubeadm commands (init, join control plane, join worker) pass `--ignore-preflight-errors=NumCPU` to support single-vCPU control plane VMs
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

## API Server HA (kube-vip)

**VIP not responding**:
- Check that kube-vip pods are running on the control plane: `kubectl get pods -n kube-system | grep kube-vip`
- Verify the VIP is being announced: `arping -I <interface> <K8S_VIP>` from another machine on the LAN
- Check kube-vip logs: `kubectl logs -n kube-system kube-vip-k8s-control-1`
- Verify the VIP address is outside your DHCP range and `CILIUM_LOADBALANCER_IPPOOL`

**kube-vip not deploying on new cluster**:
- Ensure `K8S_VIP` is set in `.env` (not empty)
- kube-vip is only deployed when `admin.conf` does not exist on the primary control plane — if the cluster was previously initialized, kube-vip is skipped

**kube-vip deployed but API unreachable via VIP**:
- Check leader election: `kubectl get lease plndr-cp-lock -n kube-system -o yaml`
- Verify `controlPlaneEndpoint` in the kubeadm config matches the VIP: `kubectl get cm -n kube-system kubeadm-config -o yaml | grep controlPlaneEndpoint`
- Ensure the kube-vip interface matches the node's default interface: check `ansible_facts.default_ipv4.interface` vs the `vip_interface` env var in the kube-vip manifest

**kubeadm init hangs with `context deadline exceeded` on ClusterRoleBinding (K8s 1.29+)**:
- Since Kubernetes 1.29, `admin.conf` uses a non-privileged user requiring a ClusterRoleBinding created during `kubeadm init`. If kube-vip mounts `admin.conf`, it can't authenticate → the VIP never comes up → kubeadm can't reach the API server via VIP to create the ClusterRoleBinding → deadlock.
- **Fix**: The primary control plane (`k8s-control-1`) mounts `super-admin.conf` instead — this file has `system:masters` in the client certificate and works without RBAC. Secondary control planes use `admin.conf` because the ClusterRoleBinding already exists by the time they join.
- If you see this on a fresh cluster, verify the kube-vip static pod manifest on control-1 references `/etc/kubernetes/super-admin.conf` in the hostPath volume.

**kube-vip logs show "could not create k8s REST config from incluster file"**:
- kube-vip defaults to in-cluster auth (service account token), which doesn't exist for static pods. The `vip_kubeconfig` environment variable must be set to point kube-vip at the mounted kubeconfig file (`/etc/kubernetes/admin.conf`).
- Check the static pod manifest at `/etc/kubernetes/manifests/kube-vip.yaml` for the `vip_kubeconfig` env var.

**kube-vip works on control-1 but not control-2**:
- `kubeadm join --control-plane` creates `admin.conf` but NOT `super-admin.conf`. If both nodes mount `super-admin.conf`, kube-vip on secondary nodes will fail with a file-not-found error.
- The template uses a Jinja2 conditional: `inventory_hostname == 'k8s-control-1'` → `super-admin.conf`, all others → `admin.conf`.

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

## Dragonfly / P2P Image Distribution

See [Dragonfly — Troubleshooting](../applications/infrastructure/dragonfly.md#troubleshooting) for diagnostic commands and common issues (client CrashLoopBackOff, CRI-O mirror config, seed client caching, Harbor preheat setup).

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
