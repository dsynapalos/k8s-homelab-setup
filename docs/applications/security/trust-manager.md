# trust-manager

## What It Does

trust-manager is a cert-manager sub-project that distributes trust bundles (CA certificates) across Kubernetes namespaces. It watches `Bundle` custom resources and propagates CA certificates into ConfigMaps in selected namespaces, ensuring that workloads can verify TLS certificates signed by the homelab CA without per-application trust configuration.

In this cluster, trust-manager runs in the `cert-manager` namespace and manages two Bundle resources: one distributing the homelab CA certificate cluster-wide (for general TLS verification), and one providing ArgoCD with the CA in both PEM and JKS formats (for OIDC backchannel calls to Keycloak).

## Why It's Here

Before trust-manager, services that needed to verify the homelab self-signed CA (e.g., Grafana calling Keycloak for OIDC, Synapse calling Keycloak for token validation) either had to skip TLS verification (`insecureSkipTLSVerify`) or manually mount CA Secrets. trust-manager provides:

- **Centralized CA distribution** — the homelab CA certificate is distributed to all namespaces automatically via a single `Bundle` CR
- **Proper TLS verification** — services mount the distributed `homelab-ca-bundle` ConfigMap instead of skipping TLS verification
- **Automatic updates** — when the CA certificate rotates, trust-manager propagates the new certificate to all target namespaces
- **ArgoCD-specific trust** — a dedicated Bundle provides the CA in JKS format for ArgoCD's OIDC integration with Keycloak

## How It's Configured

### Installation

trust-manager is deployed via a Helm chart combined with Kustomize manifests for the Bundle CRs:

```
argocd_applications/security/trust-manager/
├── kustomization.yaml           # References the two Bundle CRs
├── ca-bundle.yaml               # Cluster-wide homelab CA distribution
└── argocd-tls-bundle.yaml       # ArgoCD-specific CA bundle (PEM + JKS)
```

The ArgoCD Application uses a multi-source configuration — the Helm chart from the Jetstack registry and the Bundle CRs from the Git repository:

| Source | Type | Purpose |
|--------|------|---------|
| `charts.jetstack.io/trust-manager` v0.14.0 | Helm | Operator deployment |
| `argocd_applications/security/trust-manager/` | Git | Bundle CRs |

The operator is deployed into the `cert-manager` namespace with `app.trust.namespace: cert-manager` so it can read the CA Secret created by cert-manager.

### Sync Wave Ordering

| Wave | Resource | Purpose |
|------|----------|---------|
| 1 | trust-manager operator (Helm) | CRDs, controller, webhook |
| 10 | `homelab-ca-bundle` Bundle | Cluster-wide CA distribution |
| 10 | `argocd-tls-certs-cm` Bundle | ArgoCD-specific CA bundle |

The operator deploys at sync wave 1 alongside cert-manager. The Bundle CRs use sync wave 10 to ensure the operator and cert-manager CA Secret are available.

### Bundle: `homelab-ca-bundle`

Distributes the homelab CA certificate cluster-wide:

- **Source**: `homelab-ca-secret` Secret in `cert-manager` namespace (created by cert-manager's CA chain)
- **Target**: ConfigMap named `homelab-ca-bundle` with key `ca-certificates.crt`
- **Scope**: All namespaces except `kube-system` (via `namespaceSelector` with `NotIn`)
- **Includes default CAs**: `useDefaultCAs: true` — the bundle contains both the homelab CA and the system's default CA certificates

Services mount this ConfigMap to verify TLS connections to `*.k8s.local` endpoints (e.g., Grafana → Keycloak, Synapse → Keycloak).

### Bundle: `argocd-tls-certs-cm`

Provides the homelab CA specifically for ArgoCD's OIDC integration:

- **Source**: `homelab-ca-secret` Secret (same as above)
- **Target**: ConfigMap named `argocd-tls-certs-cm` with key `keycloak.k8s.local` (PEM) + JKS truststore
- **Scope**: `argocd` namespace only (via `namespaceSelector` matching `kubernetes.io/metadata.name: argocd`)
- **Additional formats**: JKS truststore at key `truststore.jks` for Java-based OIDC libraries

ArgoCD uses the special ConfigMap name `argocd-tls-certs-cm` — ArgoCD's built-in TLS certificate handling automatically loads certificates from this ConfigMap and trusts them for outbound connections (including OIDC provider calls to Keycloak).

## Integration Points

| Direction | Target | Purpose |
|-----------|--------|---------|
| ← ArgoCD | trust-manager | Deploys and syncs operator + Bundle CRs via Application CR |
| ← cert-manager | `homelab-ca-secret` | Source CA certificate for all Bundles |
| → All namespaces | `homelab-ca-bundle` ConfigMap | CA trust for TLS verification |
| → ArgoCD namespace | `argocd-tls-certs-cm` ConfigMap | CA trust for OIDC backchannel calls |
| → Grafana | Volume mount | Grafana mounts `homelab-ca-bundle` for Keycloak OIDC TLS |
| → Matrix Synapse | Volume mount | Synapse mounts `homelab-ca-bundle` for Keycloak OIDC TLS |
| → ArgoCD Server | Volume mount | ArgoCD Server mounts `homelab-ca-bundle` + sets `SSL_CERT_DIR` |

## Troubleshooting

```bash
# Check trust-manager pods are running
kubectl get pods -n cert-manager -l app.kubernetes.io/name=trust-manager

# Check trust-manager logs
kubectl logs -n cert-manager -l app.kubernetes.io/name=trust-manager --tail=50

# List all Bundle resources
kubectl get bundles

# Check Bundle status
kubectl describe bundle homelab-ca-bundle
kubectl describe bundle argocd-tls-certs-cm

# Verify ConfigMap was created in a target namespace
kubectl get configmap homelab-ca-bundle -n monitoring
kubectl get configmap homelab-ca-bundle -n security
kubectl get configmap argocd-tls-certs-cm -n argocd

# Inspect the distributed CA certificate
kubectl get configmap homelab-ca-bundle -n monitoring -o jsonpath='{.data.ca-certificates\.crt}' | openssl x509 -text -noout

# Verify the ArgoCD JKS truststore exists
kubectl get configmap argocd-tls-certs-cm -n argocd -o jsonpath='{.data}' | python3 -c "import sys,json; print(list(json.load(sys.stdin).keys()))"
```

**ConfigMap not appearing in a namespace**: Check the Bundle's `namespaceSelector`. The `homelab-ca-bundle` excludes `kube-system` — if you need the CA in `kube-system`, update the selector. Verify the namespace label matches: `kubectl get ns <namespace> --show-labels`.

**ArgoCD OIDC fails with TLS errors**: Verify the `argocd-tls-certs-cm` ConfigMap exists in the `argocd` namespace and contains the `keycloak.k8s.local` key. Check that the ArgoCD Server Deployment has the `homelab-ca` volume mount and `SSL_CERT_DIR` includes `/etc/ssl/certs/homelab`.

**Bundle stuck in not-ready state**: Check that the source Secret (`homelab-ca-secret`) exists in the `cert-manager` namespace. If cert-manager's CA chain hasn't been established yet, the Secret won't exist. Verify cert-manager is healthy: `kubectl get certificate -n cert-manager homelab-ca`.

## Links

- [trust-manager Documentation](https://cert-manager.io/docs/trust/trust-manager/)
- [trust-manager Bundle API](https://cert-manager.io/docs/trust/trust-manager/api-reference/)
- [cert-manager Trust Distribution](https://cert-manager.io/docs/trust/)
- [GitHub Repository](https://github.com/cert-manager/trust-manager)
