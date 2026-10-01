# Telemetry defaults

| Signal | Default | Setting |
|---|---|---|
| Traces, metrics, logs (OTLP) | on in production profiles, off in tiny | `ENABLE_OTEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`; header/credential values only as `*_REF` references |
| Prometheus scrape | graph-os `/metrics` (bearer-gated), carrying the engine's series merged from its loopback listener | scrape the graph-os service; never add a proxy for the engine listener |
| Content capture in traces | off (`LANGFUSE_CAPTURE_CONTENT=false`, `USAGE_CONTENT_RETENTION=metadata`) | turn on only with an explicit data-handling decision |

A dedicated telemetry collector identity, scoped to telemetry-write for one
tenant, stamps the tenant on what it forwards to the engine; it never holds
graph scopes.

Correlate ingress request, agent run, model request, retrieval, tool call,
connector request, engine transaction and background work under one trace.
Record tokens, cost, latency, queue delay, retries, cache hit/miss and
termination reason; redact errors. An observability vendor is optional; trace
semantics are not.

## Not available yet

- **Browser RUM.** The web UI reporting Core Web Vitals to its own `/api/rum`
  route, relayed to `RUM_OTLP_METRICS_ENDPOINT` (unset endpoint accepts and
  drops). Once it ships: first-party only (no third-party SDK, no IP address,
  no user agent, a session id that rotates every UTC day, 30-day retention of
  the `rum_*` series); a browser whose consent choice is "denied" never sends.
- **Pseudonymized security-audit feeds.** Ingestion pseudonymizing identities
  and secret paths as a keyed HMAC and truncating IP addresses (/24 for IPv4,
  /48 for IPv6); a keyed HMAC key reference, without which the feeds refuse to
  ingest; secret values never ingested.
