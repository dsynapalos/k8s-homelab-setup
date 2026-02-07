# Metrics Server

## What It Does

Metrics Server is a cluster-wide aggregator of resource usage data. It collects CPU and memory metrics from kubelets and exposes them via the Kubernetes Metrics API, enabling `kubectl top nodes`, `kubectl top pods`, and Horizontal Pod Autoscaling (HPA).

## Why It's Here

Without Metrics Server, the Kubernetes API has no concept of actual resource consumption. It's a prerequisite for:

- `kubectl top` commands (basic operational troubleshooting)
- Horizontal Pod Autoscaler (HPA) — scales workloads based on CPU/memory usage
- Vertical Pod Autoscaler (VPA) — recommends resource requests/limits
- Kubernetes Dashboard resource views

## Current Status

**Placeholder** — manifests are not yet configured. This application folder exists to reserve the slot for future deployment.

## Links

- [Metrics Server Documentation](https://github.com/kubernetes-sigs/metrics-server)
- [Kubernetes Metrics API](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/)
- [Horizontal Pod Autoscaler](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
