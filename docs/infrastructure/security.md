# Security

## What This Document Covers

How the cluster handles vulnerability management — image scanning, signature verification, and automated reporting. For TLS certificate management, see [cert-manager](../applications/security/cert-manager.md). For identity and access management, see [Keycloak](../applications/security/keycloak.md). For CA trust distribution, see [trust-manager](../applications/security/trust-manager.md).

---

## Vulnerability Scanning

### Trivy (Harbor-integrated)

Every container image pulled through [Harbor](../applications/infrastructure/harbor.md) is automatically scanned by Trivy, which is embedded in Harbor as the default scanner. Trivy analyses images for known CVEs in OS packages and application dependencies, producing a per-artifact vulnerability report visible in the Harbor UI (Projects → Repository → Artifact → Vulnerabilities tab).

**Scanning triggers**:

| Trigger | When | Scope |
|---------|------|-------|
| On push / first cache | Image enters Harbor (proxy pull or direct push) | Single artifact |
| Scheduled scan-all | Daily at 03:00 UTC (`0 0 3 * * *`) | All artifacts in all projects |
| Manual | Harbor UI or API (`POST /api/v2.0/.../scan`) | Single artifact |
| Artifact indexer | Hourly CronJob triggers scan for newly discovered artifacts | Artifacts missing scan results |

The daily scan-all schedule is configured by the Harbor bootstrap Job. See [Harbor — Vulnerability Scanning & Maintenance Schedules](../applications/infrastructure/harbor.md#vulnerability-scanning--maintenance-schedules) for all scheduled tasks.

### Artifact Indexer

Images pulled through Harbor's proxy cache don't always register artifact metadata correctly ([Harbor bug #21454](https://github.com/goharbor/harbor/issues/21454)). The `harbor-artifact-indexer` CronJob (hourly) discovers all Harbor-proxied images running in the cluster, forces Harbor to index them, and triggers SBOM generation and vulnerability scans for any artifact missing results.

Images running in the cluster that bypass Harbor's proxy cache (e.g., Cilium, CoreDNS, etcd — pulled directly during kubeadm bootstrap) are imported into a writable `cluster-images` project by the same CronJob, where they are scanned automatically on push.

See [Harbor — Artifact Indexing](../applications/infrastructure/harbor.md#artifact-indexing-harbor-bug-21454-workaround) and [Harbor — External Image Import](../applications/infrastructure/harbor.md#external-image-import-cluster-images-project) for details.

### Scan Coverage

Between the proxy cache, the artifact indexer, and the `cluster-images` import, **every container image running in the cluster** has a Trivy vulnerability report in Harbor. Coverage is maintained automatically — new images are discovered and scanned within an hour of deployment.

---

## Signature Verification

The artifact indexer CronJob performs upstream signature verification using [Cosign](https://docs.sigstore.dev/cosign/overview/) (Sigstore, CNCF Graduated):

**Proxy cache images** are verified against upstream Sigstore/Rekor transparency logs using keyless verification. Results are recorded as Harbor labels on each artifact:
- **`upstream-verified`** — Valid Sigstore signature found from upstream publisher
- **`upstream-unverified`** — No verifiable Sigstore signature (may use a different signing mechanism or be unsigned)

**CI/CD project images** are signed with a homelab Cosign keypair (auto-generated, stored as `cosign-keypair` Secret in the `harbor` namespace). Signatures are pushed as OCI artifacts without Rekor transparency log entries.

See [Harbor — Signature Verification](../applications/infrastructure/harbor.md#signature-verification-cosign) for current verification status and troubleshooting.

---

## CVE Reporting

### Overview

The `harbor-cve-reporter` CronJob posts a weekly CVE report to the Matrix `#alerts` channel as a downloadable CSV file attachment. The report contains every Critical, High, and Medium vulnerability across all scanned artifacts in Harbor — exactly as reported by Trivy, with no filtering or reformatting.

### Schedule

| Schedule | Cron | Description |
|----------|------|-------------|
| CVE report | `0 9 * * 1` (every Monday 09:00) | CSV of all Critical/High/Medium CVEs posted to Matrix `#alerts` |

### How It Works

The reporter runs as a Kubernetes CronJob in the `harbor` namespace using the `harbor-bootstrap` ServiceAccount. The logic is implemented in [`cve-reporter.py`](../../argocd_applications/infrastructure/harbor/cve-reporter.py), mounted as a ConfigMap.

**Execution flow**:

1. **Read Matrix credentials** — Queries the Kubernetes API to read the `matrix-bot` Secret from the `monitoring` namespace (bot access token and room ID)
2. **Scan Harbor** — Walks all projects → repositories → artifacts via the Harbor API. For each artifact with a successful Trivy scan, fetches the full vulnerability list
3. **Filter** — Keeps only Critical, High, and Medium severity CVEs
4. **Build CSV** — Renders all matching CVEs as a CSV file sorted by severity (Critical first) then CVSS score (descending)
5. **Upload to Matrix** — Uploads the CSV via Matrix's content repository API (`/_matrix/media/v3/upload`) and sends it as an `m.file` message to the `#alerts` room

**CSV columns**:

| Column | Source |
|--------|--------|
| Repository | Harbor project/repository name |
| Tag | Artifact tag |
| CVE ID | Trivy CVE identifier (e.g., `CVE-2024-12345`) |
| Severity | `Critical`, `High`, or `Medium` |
| CVSS v3 | Trivy's preferred CVSS v3 score |
| Package | Affected OS/application package |
| Current Version | Installed package version in the image |
| Fixed In | Package version that resolves the CVE (if available) |
| Description | CVE description from the vulnerability database |

**Output**: The message body contains a summary line (severity counts + images affected). The CSV file is named `harbor-cves-YYYY-MM-DD.csv`.

### Manifests

```
argocd_applications/infrastructure/harbor/
├── cve-reporter.py              # Reporter script (mounted as ConfigMap)
├── cve-reporter-cronjob.yaml    # CronJob definition (weekly Monday 09:00)
├── kustomization.yaml           # Includes CronJob + configMapGenerator for script
└── rbac.yaml                    # RBAC for harbor-bootstrap ServiceAccount
```

The cross-namespace RBAC allowing the `harbor-bootstrap` ServiceAccount to read the `matrix-bot` Secret from the `monitoring` namespace is defined in [`monitoring/matrix-bridge/rbac.yaml`](../../argocd_applications/monitoring/matrix-bridge/rbac.yaml). This is separate from the harbor kustomization to avoid the `namespace: harbor` override that would place the Role in the wrong namespace.

### CronJob Specification

- **Image**: `python:3.12-alpine` (from Harbor proxy cache)
- **ServiceAccount**: `harbor-bootstrap` (shared with artifact indexer and bootstrap Job)
- **Secrets**: `harbor-admin-password` (env var), `matrix-bot` (read via K8s API at runtime)
- **CA trust**: `homelab-ca-bundle` ConfigMap mounted at `/etc/ssl/certs/homelab/`
- **Resources**: 50m–250m CPU, 128Mi–512Mi memory
- **History**: 3 successful + 3 failed jobs retained
- **TTL**: Jobs cleaned up after 24 hours

### Dependencies

| Component | Purpose |
|-----------|---------|
| [Harbor](../applications/infrastructure/harbor.md) | Source of vulnerability data (Trivy scan results) |
| [Matrix](../applications/monitoring/matrix.md) | Delivery channel (`#alerts` room) |
| [Matrix Bridge](../applications/monitoring/matrix-bridge.md) | Provides `matrix-bot` Secret (created by Matrix bootstrap Job) |
| [cert-manager](../applications/security/cert-manager.md) | TLS trust for Harbor API calls |
| [trust-manager](../applications/security/trust-manager.md) | `homelab-ca-bundle` ConfigMap for CA certificate |

---

## Real-time Vulnerability Notifications

In addition to the weekly CSV report, Harbor sends real-time webhook notifications to the [Matrix Bridge](../applications/monitoring/matrix-bridge.md) whenever a vulnerability scan completes on any artifact. The bridge filters for **Critical-only** severity and posts an HTML summary to the same `#alerts` room.

This provides two complementary notification channels:

| Channel | Trigger | Severity Filter | Format | Latency |
|---------|---------|----------------|--------|---------|
| Webhook (Matrix Bridge) | Scan completion | Critical only | HTML summary in chat | Real-time |
| CVE Reporter (CronJob) | Weekly schedule | Critical + High + Medium | CSV file attachment | Weekly |

The webhook notifications catch new Critical vulnerabilities as soon as Trivy detects them. The weekly CSV provides a comprehensive audit trail of all actionable vulnerabilities for review and remediation planning.

---

## Troubleshooting

### CVE reporter not posting to Matrix

```bash
# Check CronJob status and last run
kubectl get cronjob harbor-cve-reporter -n harbor
kubectl get jobs -n harbor -l job-name=harbor-cve-reporter --sort-by=.status.startTime

# Manual test run
kubectl create job --from=cronjob/harbor-cve-reporter cve-reporter-test -n harbor
kubectl logs -f job/cve-reporter-test -n harbor

# Clean up test job
kubectl delete job cve-reporter-test -n harbor
```

**Common issues**:
- **RBAC error reading `matrix-bot` Secret** — Verify Role + RoleBinding exist in the `monitoring` namespace: `kubectl get role,rolebinding -n monitoring | grep matrix-bot`
- **Harbor API timeout** — Trivy scans may still be running; the daily scan-all completes around 03:00 UTC. Run the reporter after scans finish.
- **Matrix upload fails** — Check that the Synapse homeserver is reachable from the harbor namespace: `kubectl run -it --rm debug --image=busybox -n harbor -- wget -qO- http://matrix.monitoring.svc.cluster.local:8008/_matrix/client/versions`
- **Empty CSV (0 CVEs)** — Verify artifacts exist and have been scanned: check the Harbor UI → Projects → any repository → Vulnerabilities tab

### No scan results in Harbor

```bash
# Check if Trivy scanner is healthy
HARBOR_PW=$(kubectl get secret harbor-admin-password -n harbor -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
curl -sk -u "admin:$HARBOR_PW" https://harbor.k8s.local/api/v2.0/scanners | jq '.[].health'

# Trigger a manual scan-all
curl -sk -u "admin:$HARBOR_PW" -X POST https://harbor.k8s.local/api/v2.0/system/scanAll/schedule \
  -H "Content-Type: application/json" -d '{"schedule":{"type":"Manual"}}'

# Check scan status of a specific artifact
curl -sk -u "admin:$HARBOR_PW" \
  "https://harbor.k8s.local/api/v2.0/projects/dockerhub-cache/repositories/library%2Falpine/artifacts?with_scan_overview=true" | \
  jq '.[0].scan_overview'
```

### Signature verification issues

See [Harbor — Cosign verification / signing issues](../applications/infrastructure/harbor.md#cosign-verification--signing-issues).

---

## Links

- [Trivy Documentation](https://aquasecurity.github.io/trivy/)
- [Harbor Vulnerability Scanning](https://goharbor.io/docs/latest/administration/vulnerability-scanning/)
- [Cosign / Sigstore](https://docs.sigstore.dev/cosign/overview/)
- [Matrix Client-Server API — Content Repository](https://spec.matrix.org/latest/client-server-api/#content-repository)
