# Networking

## What This Document Covers

How traffic moves inside the cluster — the CNI plugin, encryption, load balancing, ingress, and the optional service mesh. Cilium is the foundation; Istio Ambient is an optional layer on top.

## Cilium CNI

### What It Does

Cilium replaces kube-proxy entirely and handles all cluster networking via eBPF programs loaded directly into the Linux kernel. It manages pod-to-pod communication, service routing, load balancing, network policy enforcement, and observability — all without iptables.

### Why It's Here

Traditional Kubernetes networking stacks (kube-proxy + Flannel/Calico with iptables) work but don't scale well and offer limited visibility. Cilium was chosen because:

- **eBPF data path** eliminates iptables overhead for service routing
- **WireGuard encryption** secures all pod-to-pod traffic with minimal CPU cost
- **L2 announcements** provide bare-metal LoadBalancer support without MetalLB
- **Hubble** gives real-time network flow visualization and policy debugging
- **Built-in Ingress Controller** or Gateway API support — no separate Nginx/Traefik needed

### How It's Configured

**Deployment**: Helm chart (`cilium/cilium`) with kube-proxy replacement enabled. Deployed in Phase 5 of the pipeline after kubeadm init (which runs with `--skip-phases=addon/kube-proxy`).

**Two mutually exclusive modes** (controlled by `ENABLE_GATEWAY_API`):

| Mode | Ingress | Proxy | Best For |
|------|---------|-------|----------|
| **Ingress Controller** (default) | Traditional Ingress resources | Cilium built-in | Simple setups, Istio Ambient compatibility |
| **Gateway API** | Gateway + HTTPRoute resources | Envoy sidecar | Advanced routing, traffic splitting |

- **Ingress Controller mode**: `ingressController.loadbalancerMode=shared` (single LoadBalancer IP shared across all Ingress resources), `ingressController.default=true` (set as default IngressClass)
- **Gateway API mode**: Installs Gateway API CRDs v1.3.0, enables Envoy proxy with `rollOutPods=true`

**Cluster sizing**: Both the Cilium agent and operator run as single replicas (`operator.replicas=1`) — appropriate for small/homelab clusters. Scale up for production.

**DNS**: IPv4-only (`dns.enableIPv4=true`, `dns.enableIPv6=false`).

**Encryption**: WireGuard is enabled by default. All pod-to-pod traffic is encrypted transparently — no application changes needed.

**Observability**: Hubble relay and UI are deployed automatically, with TLS certificates valid for 3 years and quarterly rotation via CronJob (`0 0 1 */4 *`). The Hubble UI is accessible at `hubble.k8s.local` via Ingress (in Ingress Controller mode only).

**Post-install actions**: After Cilium is deployed, CRI-O is restarted on each node for proper CNI integration. Then all non-hostNetwork pods across all namespaces are deleted so they restart with Cilium networking applied. This ensures pods that started before Cilium (e.g., CoreDNS) get proper eBPF-based networking.

### CoreDNS Rewrite

After Cilium is deployed, the `bootstrap_cillium` role patches CoreDNS with a rewrite rule that resolves `*.k8s.local` hostnames to the shared Cilium Ingress service ClusterIP (`cilium-ingress.kube-system.svc.cluster.local`). This allows pods inside the cluster to reach Ingress-served endpoints (e.g., `https://keycloak.k8s.local`) using the same URLs as external clients — without hardcoding LoadBalancer IPs or maintaining separate internal/external endpoint configurations.

The rewrite uses a regex rule inserted into the CoreDNS `Corefile`:

```
rewrite name regex (.*)\\.k8s\\.local cilium-ingress.kube-system.svc.cluster.local answer auto
```

This is critical for OIDC flows where services like Grafana, ArgoCD, and Matrix Synapse need to call Keycloak endpoints at `https://keycloak.k8s.local/...` from within the cluster. Combined with the homelab CA certificate distributed by [trust-manager](../applications/security/trust-manager.md), this enables proper TLS verification without using internal HTTP endpoints or skipping TLS.

The patch is idempotent — it checks whether `k8s.local` is already in the Corefile before applying, and only restarts CoreDNS when changes are made.

### IP Addressing

| Range | Purpose | Default |
|-------|---------|---------|
| Pod CIDR | Pod IP allocation | Managed by Cilium IPAM (typically `10.0.0.0/16`) |
| Service CIDR | ClusterIP services | Kubernetes default (`10.96.0.0/12`) |
| LoadBalancer pool | External service IPs | Configured via `CILIUM_LOADBALANCER_IPPOOL` |

### L2 Load Balancing

Cilium announces LoadBalancer service IPs via ARP (IPv4) and NDP (IPv6) on the local network. This gives you external access to services without a cloud load balancer or MetalLB:

- A `CiliumLoadBalancerIPPool` is created from the `CILIUM_LOADBALANCER_IPPOOL` CIDR
- Per-node `CiliumL2AnnouncementPolicy` resources are created on each worker node
- Each policy uses a `nodeSelector` with `node-role.kubernetes.io/control-plane: DoesNotExist` to ensure only worker nodes participate in L2 leader election — control plane nodes must never hold LoadBalancer IPs because their network interface names may differ from workers
- Clients on the LAN can reach LoadBalancer IPs directly

### Firewall Rules

The `setup_os` role configures UFW on every node:

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH access |
| 6443 | TCP | Kubernetes API server |
| 2379–2380 | TCP | etcd (control plane only) |
| 10250 | TCP | kubelet API |
| 10256 | TCP | kube-proxy health checks |
| 10257, 10259 | TCP | kube-controller-manager, kube-scheduler |
| 80, 443 | TCP | Ingress HTTP/HTTPS |
| 30000–32767 | TCP | NodePort range |
| 4240 | TCP | Cilium health checks |
| 4244–4245 | TCP | Hubble relay |
| 4250 | TCP | Cilium metrics |
| 8472 | UDP | Cilium VXLAN overlay |
| 51871 | UDP | Cilium WireGuard encryption |

---

## Istio Ambient Service Mesh (Optional)

### What It Does

Istio Ambient adds transparent mTLS encryption between workloads using a per-node proxy called ztunnel (zero-trust tunnel). Unlike traditional Istio, there are no sidecar containers — traffic is intercepted at the node level via the Istio CNI plugin, encrypted using HBONE (HTTP-Based Overlay Network Encapsulation) on port 15008, and decrypted at the destination node.

### Why It's Here

Cilium's WireGuard encrypts traffic at the node level (node-to-node), but it doesn't provide workload identity or per-service access control. Istio Ambient fills this gap:

- **Workload identity**: Each pod gets a SPIFFE identity (cryptographic proof of who it is)
- **mTLS per-connection**: Encryption is between specific workloads, not just nodes
- **AuthorizationPolicy**: Fine-grained "who can call what" rules based on identity
- **Zero overhead for unenrolled namespaces**: Only namespaces you label join the mesh

### How It's Configured

Four Helm charts are deployed in sequence, with pauses between each to allow resources to stabilize:

| Component | Type | Key Configuration | Purpose |
|-----------|------|-------------------|----------|
| **istio-base** | CRDs | 10-second pause after install | Installs Istio Custom Resource Definitions |
| **istio-cni** | DaemonSet | `profile: ambient` | Transparent traffic redirection |
| **istiod** | Deployment | `profile: ambient`, `pilot.replicas: 1`, `PILOT_ENABLE_AMBIENT_CONTROLLERS: "true"` | Control plane with ambient controllers |
| **ztunnel** | DaemonSet | Waits for istiod rollout (300s timeout) | Per-node L4 proxy handling mTLS encryption |

Critical istiod settings:
- **`PILOT_ENABLE_AMBIENT_CONTROLLERS: "true"`**: Environment variable in the pilot container that activates ambient mesh support. Without this, ambient enrollment has no effect.
- **Telemetry**: `telemetry.enabled=true` with Prometheus metrics export (`v2.prometheus.enabled=true`)
- **Resources**: CPU 10m–1000m for istiod; CPU 10m–500m for ztunnel

After all four charts are installed, the role verifies:
- ztunnel DaemonSet rollout complete (300s timeout)
- All Istio pods are Running (10 retries, 10s delay)
- CNI pod count matches node count
- ztunnel pod count matches node count
- istiod health endpoint (`/healthz/ready`) returns OK

### Cilium Compatibility

When Ingress Controller mode is active (the default), three Cilium settings are adjusted to allow Istio's ztunnel to function correctly. These settings are applied **unconditionally in Ingress Controller mode** — they don't check whether `ENABLE_ISTIO` is set. This is by design: the settings have minimal impact on a non-Istio cluster but are essential when Istio is present.

> **Gateway API + Istio is not supported**: In Gateway API mode, these compatibility settings are not applied. If you enable both Gateway API and Istio, ztunnel will fail to intercept traffic correctly. Use Ingress Controller mode (the default) when running Istio Ambient.

| Setting | Value | Why |
|---------|-------|-----|
| `bpf.masquerade` | `false` | Cilium must not rewrite link-local IPs (169.254.7.127) that ztunnel uses |
| `socketLB.hostNamespaceOnly` | `true` | ztunnel needs to intercept ClusterIP traffic for mesh routing |
| `cni.exclusive` | `false` | Allows Istio CNI to chain alongside Cilium CNI |

### Multi-Cluster Identity

Both istiod and ztunnel are configured with matching identity settings for proper authentication:

| Variable | Purpose |
|----------|---------|
| `ISTIO_MESH_ID` | Identifies the mesh (same across all federated clusters) |
| `ISTIO_CLUSTER_NAME` | Unique name for this cluster within the mesh |
| `ISTIO_NETWORK` | Network identifier for cross-cluster routing |

If `ISTIO_CLUSTER_NAME` doesn't match between istiod and ztunnel, you'll see authentication failures in ztunnel logs.

### CPU Requirements

Istio 1.23+ ships Wolfi-based distroless images that require x86-64-v2 instructions (SSE4.1, SSE4.2, POPCNT). Set `VM_CPU_TYPE=host` in `.env` to expose full host CPU features to VMs. The default `kvm64` emulation only provides baseline x86-64 and ztunnel will crash on startup.

### Namespace Enrollment Recommendations

| Enroll? | Namespaces | Reasoning |
|---------|------------|-----------|
| ✅ Yes | Application namespaces (backend, frontend, api-gateway) | Benefit from mTLS, identity, and access control |
| ❌ No | `kube-system`, `istio-system`, `monitoring`, `argocd`, `rook-ceph` | Avoid circular dependencies, maintain observability, prevent platform instability |

Enrollment is transparent — pods automatically join the mesh when their namespace is labeled, no pod restart required.

### mTLS Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| **PERMISSIVE** (default) | Mesh pods use mTLS with each other; external traffic allowed in plain | Safe default, good for migration |
| **STRICT** | All traffic must be mTLS (blocks non-mesh sources) | High-security namespaces only |
| **DISABLE** | No mTLS, plain text only | Debugging, explicit opt-out |

Start with PERMISSIVE. Use `AuthorizationPolicy` for fine-grained "who can call what" rules. Only switch to STRICT per-namespace when you have a specific security requirement. Platform security should rely on RBAC, Network Policies, and Pod Security Standards — not mTLS.

### Health Checks and Monitoring Considerations

- **Kubelet probes**: Health and readiness probes bypass the mesh (they run on the host network, not intercepted by ztunnel). No special configuration needed.
- **Prometheus scraping**: When Prometheus runs outside the mesh (recommended), it can scrape mesh pods in PERMISSIVE mode. For STRICT mode, either put Prometheus in the mesh or add port-level exceptions via `PeerAuthentication`.

### Verification

```bash
# Check all Istio pods are running
kubectl get pods -n istio-system

# View ztunnel mTLS traffic
kubectl logs -n istio-system -l app=ztunnel --tail=20

# Check which namespaces are in the mesh
kubectl get namespaces -L istio.io/dataplane-mode

# Verify SPIFFE identities
kubectl logs -n istio-system -l app=ztunnel | grep "src.identity\|dst.identity"
```

## Integration Points

| Component | Relationship |
|-----------|-------------|
| [Prometheus](../applications/monitoring/prometheus.md) | Scrapes Cilium and Hubble metrics |
| [Grafana](../applications/monitoring/grafana.md) | Visualizes network flows and mesh telemetry |

## Troubleshooting

### Cilium

```bash
# Overall Cilium health
cilium status --wait

# Detailed agent status on each node
kubectl get pods -n kube-system -l k8s-app=cilium -o wide
kubectl exec -n kube-system <cilium-pod> -- cilium status --verbose

# Run full connectivity test (takes a few minutes)
cilium connectivity test

# Check WireGuard encryption is active
cilium encrypt status
kubectl exec -n kube-system <cilium-pod> -- cilium encrypt status

# Inspect L2 announcement state
kubectl get ciliumloadbalancerippool
kubectl get ciliuml2announcementpolicy
kubectl get svc -A -o wide | grep LoadBalancer

# Hubble flow observation
hubble observe --follow
hubble observe --namespace default --protocol TCP
hubble observe --verdict DROPPED    # Find blocked traffic

# Check CNI config on a node
ssh <node> "ls -la /etc/cni/net.d/; cat /etc/cni/net.d/05-cilium.conflist"

# Verify kube-proxy is NOT running (Cilium replaces it)
kubectl get pods -n kube-system | grep kube-proxy    # Should return nothing

# Check Cilium Helm values used during install
helm get values cilium -n kube-system
```

**DNS not resolving**: Check that VXLAN (8472/udp) and WireGuard (51871/udp) ports are open on all nodes. Run `cilium connectivity test` — it will pinpoint the failure.

**LoadBalancer IP not reachable from LAN**: Verify the `CILIUM_LOADBALANCER_IPPOOL` CIDR is on your local subnet. Check that `CiliumL2AnnouncementPolicy` exists for each worker node. From another machine, try `arping <loadbalancer-ip>` to confirm L2 announcements. Also check which node holds the L2 lease: `kubectl get lease -n kube-system -l cilium.io/l2-announce=true`. If a control plane node holds the lease, the `nodeSelector` on the `CiliumL2AnnouncementPolicy` may be missing — control plane nodes have a different interface name (`ens18` vs `enp6s18`) and must be excluded from L2 elections.

**Pods stuck in ContainerCreating after initial deploy**: Cilium may still be initializing. Wait for all Cilium pods to reach Running. Check `kubectl describe pod <stuck-pod>` for CNI-related events.

### Istio Ambient

```bash
# Check all Istio components
kubectl get pods -n istio-system -o wide

# Verify ztunnel is running on every node
kubectl get ds -n istio-system ztunnel

# Check ztunnel traffic logs (shows mTLS handshakes)
kubectl logs -n istio-system -l app=ztunnel --tail=50

# Verify SPIFFE identities being issued
kubectl logs -n istio-system -l app=ztunnel | grep "src.identity\|dst.identity"

# Check which namespaces are enrolled in the mesh
kubectl get namespaces -L istio.io/dataplane-mode

# Verify istiod health
kubectl exec -n istio-system deploy/istiod -- curl -s localhost:8080/healthz/ready

# Check CNI plugin chain (Cilium + Istio should coexist)
ssh <node> "ls -la /etc/cni/net.d/"

# Verify cluster name matches in ztunnel (authentication fix)
kubectl get ds -n istio-system ztunnel \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="ISTIO_META_CLUSTER_ID")].value}'

# Check Istio Helm values
helm get values istiod -n istio-system
helm get values ztunnel -n istio-system
```

**ztunnel CrashLoopBackOff with CPU error**: Set `VM_CPU_TYPE=host` in `.env` and recreate VMs. Istio 1.23+ requires x86-64-v2.

**Authentication failures in ztunnel logs**: The `ISTIO_CLUSTER_NAME` must match between istiod and ztunnel Helm values. Check the environment variable `ISTIO_META_CLUSTER_ID` in the ztunnel DaemonSet.

**Namespace enrolled but traffic not encrypted**: Confirm the namespace has `istio.io/dataplane-mode=ambient` label. Check ztunnel logs for the specific pod — if it's not being intercepted, the Istio CNI plugin may not have initialized correctly.

---

## TLS Certificate Management

### How It Works

All Ingress endpoints use HTTPS with TLS certificates provisioned automatically by [cert-manager](../applications/security/cert-manager.md). Since this cluster uses `*.k8s.local` hostnames (not real DNS), a self-signed CA chain is used instead of a public CA like Let's Encrypt.

The CA hierarchy:

1. **selfsigned-issuer** — bootstrap ClusterIssuer (creates the CA itself)
2. **homelab-ca** — root CA Certificate (10-year validity, ECDSA P-256)
3. **homelab-ca-issuer** — production ClusterIssuer that signs all leaf certificates

### Ingress TLS Pattern

Every Ingress resource includes three additions:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: homelab-ca-issuer
    ingress.cilium.io/force-https: "enabled"
spec:
  tls:
  - hosts:
    - <app>.k8s.local
    secretName: <app>-tls
```

cert-manager's ingress-shim controller watches for the annotation, creates a Certificate resource, obtains a signed certificate from the CA, and stores it in the referenced Secret. Cilium's Ingress Controller uses the Secret for TLS termination.

The `ingress.cilium.io/force-https: "enabled"` annotation makes Cilium create an HTTP listener that returns a `301` redirect to the HTTPS URL. This ensures all plain HTTP requests are automatically redirected to HTTPS — users never see unencrypted content.

### ArgoCD TLS

ArgoCD runs in insecure mode (`server.insecure: "true"` in `argocd-cmd-params-cm`), serving HTTP behind the Cilium Ingress. TLS is terminated at the Ingress using a cert-manager-provisioned certificate (`argocd-tls` Secret). This replaces the previous TLS passthrough setup where ArgoCD managed its own self-signed certificate.

### Hubble UI TLS

The Hubble UI Ingress (created by the `bootstrap_cillium` role in the infrastructure stage) includes the cert-manager annotation. Since cert-manager deploys later (via ArgoCD in the application stage), the certificate is provisioned after cert-manager starts. Until then, Hubble UI is accessible via HTTP.

### Boundary with Istio mTLS

cert-manager and Istio Ambient handle different TLS concerns:

| Traffic | Handler | Certificate Source |
|---------|---------|-------------------|
| Client → Ingress (north-south) | cert-manager + Cilium | homelab-ca-issuer |
| Pod → Pod (east-west) | Istio ztunnel | istiod built-in CA (SPIFFE) |

These are separate PKI chains. cert-manager does **not** replace or interact with Istio's workload certificate management. Do not configure `istio-csr` to bridge them unless you have an explicit multi-cluster PKI requirement.

### Trusting the CA

Since the CA is self-signed, browsers will show an untrusted certificate warning. To trust it:

```bash
# Extract the CA certificate
kubectl get secret -n cert-manager homelab-ca-secret -o jsonpath='{.data.ca\.crt}' | base64 -d > homelab-ca.crt

# Add to system trust (Ubuntu/Debian)
sudo cp homelab-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

For details on cert-manager configuration, troubleshooting, and the full certificate inventory, see [cert-manager](../applications/security/cert-manager.md).

---

## Links

- [Cilium Documentation](https://docs.cilium.io/)
- [Cilium WireGuard Encryption](https://docs.cilium.io/en/stable/security/network/encryption-wireguard/)
- [Cilium L2 Announcements](https://docs.cilium.io/en/stable/network/l2-announcements/)
- [Hubble Observability](https://docs.cilium.io/en/stable/observability/hubble/)
- [Istio Ambient Documentation](https://istio.io/latest/docs/ambient/)
- [Istio HBONE Protocol](https://istio.io/latest/docs/ambient/architecture/hbone/)
- [SPIFFE Identity Framework](https://spiffe.io/)
- [cert-manager Documentation](https://cert-manager.io/docs/)
