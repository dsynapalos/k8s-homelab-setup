# cert-manager

## What It Does

cert-manager automates TLS certificate lifecycle management within Kubernetes. It watches for Ingress resources annotated with `cert-manager.io/cluster-issuer`, creates Certificate resources, talks to the configured issuer to obtain signed certificates, stores them as Kubernetes Secrets, and renews them before expiry — all without manual intervention.

In this cluster, cert-manager provisions TLS certificates for every Ingress endpoint (`*.k8s.local`) using a self-signed CA chain. The CA is a two-tier hierarchy:

1. **selfsigned-issuer** (ClusterIssuer) — bootstrap-only issuer that creates the root CA
2. **homelab-ca** (Certificate) — self-signed root CA certificate (10-year validity, ECDSA P-256)
3. **homelab-ca-issuer** (ClusterIssuer) — signs all leaf certificates using the CA key

Leaf certificates are automatically created for each Ingress via the `cert-manager.io/cluster-issuer: homelab-ca-issuer` annotation. Each certificate is stored in a Secret named by the Ingress `tls[].secretName` field (e.g., `grafana-tls`, `argocd-tls`).

## Why It's Here

Before cert-manager, all Ingress endpoints were plain HTTP. ArgoCD used its own self-signed certificate with TLS passthrough, creating an inconsistent TLS story across services. cert-manager provides:

- **Consistent HTTPS** across all Ingress endpoints with a single trusted CA
- **Automatic renewal** — certificates renew before expiry without intervention
- **Centralized trust** — one CA root to distribute to clients for trust
- **ArgoCD integration** — replaces ArgoCD's bespoke self-signed certificate with a cert-manager-managed one, using TLS termination at the Cilium Ingress instead of passthrough

## How It's Configured

### Installation

cert-manager is deployed from the upstream static install manifest via Kustomize:

```
argocd_applications/security/cert-manager/
├── kustomization.yaml          # References upstream cert-manager.yaml + local resources
├── self-signed-issuer.yaml     # ClusterIssuer: selfsigned-issuer (sync-wave 5)
├── ca-certificate.yaml         # Certificate: homelab-ca (sync-wave 6)
└── ca-issuer.yaml              # ClusterIssuer: homelab-ca-issuer (sync-wave 7)
```

The ArgoCD Application uses `ServerSideApply=true` (required for large CRDs) and aggressive retry policy (10 retries, exponential backoff) because the cert-manager webhook may take a moment to become ready after the controller deploys.

### Sync Wave Ordering

Within the cert-manager Application, ArgoCD sync waves ensure resources are created in the right order:

| Wave | Resource | Purpose |
|------|----------|---------|
| 0 (default) | Upstream cert-manager (CRDs, controller, webhook) | Core infrastructure |
| 5 | `selfsigned-issuer` ClusterIssuer | Bootstrap issuer for creating the CA |
| 6 | `homelab-ca` Certificate | Root CA certificate (10-year validity) |
| 7 | `homelab-ca-issuer` ClusterIssuer | Production issuer that signs leaf certs |

### Using cert-manager in Ingress Resources

All Ingress resources request certificates and enforce HTTPS by adding three things:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: homelab-ca-issuer
    ingress.cilium.io/force-https: "enabled"
spec:
  tls:
  - hosts:
    - <hostname>.k8s.local
    secretName: <app>-tls
```

cert-manager's ingress-shim controller watches for the `cluster-issuer` annotation, creates a Certificate resource, and populates the referenced Secret with the signed TLS certificate and private key. The `ingress.cilium.io/force-https` annotation makes Cilium return a `301` redirect from HTTP to HTTPS, ensuring clients are always upgraded to a secure connection.

### Version Management

The cert-manager version is pinned in the Kustomize resource URL (`kustomization.yaml`). To upgrade, update the version in the URL:

```yaml
resources:
  - https://github.com/cert-manager/cert-manager/releases/download/v1.17.2/cert-manager.yaml
```

### ArgoCD Server TLS

ArgoCD's own HTTPS endpoint uses cert-manager instead of its built-in self-signed certificate:

1. The `argocd-cmd-params-cm` ConfigMap sets `server.insecure: "true"` — ArgoCD serves HTTP only
2. A single Ingress resource (`argocd-server`) with the `cert-manager.io/cluster-issuer` annotation handles TLS termination
3. Cilium terminates TLS at the ingress using the cert-manager-provisioned certificate
4. During initial bootstrap (before cert-manager deploys), ArgoCD is accessible via HTTP; HTTPS becomes available once cert-manager processes the annotation

### Relationship with Istio mTLS

cert-manager and Istio Ambient operate at different layers and **do not interact**:

| Concern | Handler | Layer | Scope |
|---------|---------|-------|-------|
| Ingress TLS (north-south) | cert-manager | L7 | Client → Ingress endpoint |
| Workload mTLS (east-west) | Istio ztunnel | L4 | Pod → Pod within mesh |

Istio's built-in CA (istiod/citadel) manages SPIFFE identities and workload certificates for mTLS. cert-manager manages Ingress TLS certificates from the homelab CA. These are separate PKI chains with separate trust roots. **Do not** configure cert-manager as Istio's CA (`istio-csr`) unless you have a specific multi-cluster PKI requirement — the complexity is not justified for a homelab.

## Integration Points

| Direction | Target | Purpose |
|-----------|--------|---------|
| ← ArgoCD | cert-manager | Deploys and syncs cert-manager via Application CR |
| → Ingress resources | All `*.k8s.local` services | Provisions TLS certificates via annotations |
| → Kubernetes Secrets | Per-namespace | Stores TLS certs (e.g., `grafana-tls`, `argocd-tls`) |
| ∅ Istio | No interaction | Separate PKI — Istio manages its own workload certs |

### Certificates Managed

| Secret Name | Namespace | Hostname | Service |
|-------------|-----------|----------|---------|
| `argocd-tls` | `argocd` | `argocd.k8s.local` | ArgoCD UI |
| `grafana-tls` | `monitoring` | `grafana.k8s.local` | Grafana dashboards |
| `matrix-tls` | `monitoring` | `matrix.k8s.local` | Matrix Synapse |
| `prometheus-tls` | `monitoring` | `prometheus.k8s.local` | Prometheus UI |
| `thanos-query-tls` | `monitoring` | `thanos.k8s.local` | Thanos Query |
| `hubble-ui-tls` | `kube-system` | `hubble.k8s.local` | Hubble UI |
| `keycloak-tls` | `security` | `keycloak.k8s.local` | Keycloak IAM |
| `keycloak-db-server-tls` | `security` | `keycloak-db-rw.security.svc` | Keycloak PostgreSQL (CNPG) |

## Troubleshooting

```bash
# Check cert-manager pods are running
kubectl get pods -n cert-manager

# Verify ClusterIssuers are ready
kubectl get clusterissuers
kubectl describe clusterissuer homelab-ca-issuer

# Check CA certificate status
kubectl get certificate -n cert-manager homelab-ca
kubectl describe certificate -n cert-manager homelab-ca

# List all certificates across the cluster
kubectl get certificates -A

# Check a specific certificate's status
kubectl describe certificate -n monitoring grafana-tls

# View certificate details from the Secret
kubectl get secret -n monitoring grafana-tls -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text -noout

# Check cert-manager logs for errors
kubectl logs -n cert-manager -l app.kubernetes.io/component=controller --tail=50

# Check webhook is responding
kubectl get apiservice v1.webhook.cert-manager.io

# Force certificate renewal
kubectl delete secret -n monitoring grafana-tls
# cert-manager will detect the missing Secret and re-issue
```

**Certificates not being created**: Verify the ClusterIssuer is Ready (`kubectl get clusterissuers`). If the CA chain isn't established yet, the homelab-ca-issuer will show `NotReady`. Check that the cert-manager controller and webhook pods are running.

**ArgoCD sync fails on ClusterIssuer resources**: The cert-manager webhook validates custom resources. If the webhook isn't ready during the first sync, ArgoCD will retry automatically (configured with 10 retries). Wait a few minutes and check `kubectl get applications -n argocd cert-manager`.

**Browser shows untrusted certificate**: Expected — the homelab CA is self-signed. Import the CA certificate into your browser/OS trust store:

```bash
# Extract the CA certificate
kubectl get secret -n cert-manager homelab-ca-secret -o jsonpath='{.data.ca\.crt}' | base64 -d > homelab-ca.crt

# Add to system trust (Ubuntu/Debian)
sudo cp homelab-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# Or import into browser directly
```

**Certificates stuck in `Pending`**: Check the Certificate's events for error messages:

```bash
kubectl describe certificate -n <namespace> <name>
kubectl get certificaterequests -n <namespace>
kubectl describe certificaterequest -n <namespace> <name>
```

## Links

- [cert-manager documentation](https://cert-manager.io/docs/)
- [Ingress annotations reference](https://cert-manager.io/docs/usage/ingress/)
- [Self-signed CA guide](https://cert-manager.io/docs/configuration/selfsigned/)
- [ArgoCD + cert-manager best practices](https://cert-manager.io/docs/installation/best-practice/)
