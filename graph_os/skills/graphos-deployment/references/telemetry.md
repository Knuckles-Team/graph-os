# Telemetry defaults

| Signal | Default | Setting |
|---|---|---|
| Traces, metrics, logs (OTLP) | on in production profiles, off in tiny | `ENABLE_OTEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`; header/credential values only as `*_REF` references |
| Prometheus scrape | graph-os `/metrics` (bearer-gated), carrying the engine's series merged from its loopback listener | scrape the graph-os service; never add a proxy for the engine listener |
| Browser RUM | **on by default** (from train 6, EH-410) | web UI reports Core Web Vitals to its own `/api/rum` route, relayed to `RUM_OTLP_METRICS_ENDPOINT`; unset endpoint = accept and drop |
| Content capture in traces | off (`LANGFUSE_CAPTURE_CONTENT=false`, `USAGE_CONTENT_RETENTION=metadata`) | turn on only with an explicit data-handling decision |
| Security audit feeds | pseudonymized (from train 6) | a keyed HMAC key reference; without the key the feeds refuse to ingest |

Browser RUM is first-party only: no third-party SDK, no IP address, no user
agent, a session id that rotates every UTC day, and 30-day retention of the
`rum_*` series. A browser whose consent choice is "denied" never sends.

Security audit ingestion pseudonymizes identities and secret paths as a keyed
HMAC and truncates IP addresses (/24 for IPv4, /48 for IPv6); secret values are
never ingested.

A dedicated telemetry collector identity, scoped to telemetry-write for one
tenant, stamps the tenant on what it forwards to the engine; it never holds
graph scopes.

Correlate ingress request, agent run, model request, retrieval, tool call,
connector request, engine transaction and background work under one trace.
Record tokens, cost, latency, queue delay, retries, cache hit/miss and
termination reason; redact errors. An observability vendor is optional; trace
semantics are not.
