# OpenTelemetry Collector

## What It Does

The OpenTelemetry Collector is a vendor-agnostic agent for collecting, processing, and exporting telemetry data — traces, metrics, and logs — from applications and infrastructure. It acts as a unified pipeline that can receive data in multiple formats and fan out to various backends.

## Why It's Here

As the cluster evolves beyond basic metrics into distributed tracing and structured logging, the OTel Collector provides a single ingestion point. Rather than instrumenting each application for a specific backend, applications send OpenTelemetry data to the collector, which routes it to Prometheus (metrics), Jaeger/Tempo (traces), or Loki (logs).

## Current Status

**Placeholder** — manifests are not yet configured. This application folder exists to reserve the slot for future deployment.

## Planned Use Cases

- Trace collection from application workloads (HTTP request paths, latency distribution)
- Log aggregation as an alternative to per-node log shipping
- Metric format conversion (OTLP → Prometheus)

## Links

- [OpenTelemetry Collector Documentation](https://opentelemetry.io/docs/collector/)
- [OpenTelemetry Collector GitHub](https://github.com/open-telemetry/opentelemetry-collector)
- [Getting Started with OTel](https://opentelemetry.io/docs/getting-started/)
