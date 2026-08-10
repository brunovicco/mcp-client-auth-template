# P1.7c — Observable MCP reference demo

P1.7c adds an optional observability overlay to the P1.7b Docker Compose topology. P1.7a and P1.7b
remain usable without telemetry export.

```text
MCP client ──W3C trace context──▶ MCP server
    │                                │
    └──────── a2a-otel-kit ──────────┘
                    │
               OTLP/HTTP :4318
                    ▼
         OpenTelemetry Collector
             │              │
             │ receipt      └──▶ Tempo :4319
             │                       │
             └───────────────────────┤
                                     ▼
                                Grafana :3000
```

Run with automatic cleanup:

```bash
./scripts/run_observability_demo.sh
```

Keep Grafana running after verification:

```bash
./scripts/run_observability_demo.sh --keep
```

Then open `http://127.0.0.1:3000`. Stop with:

```bash
./scripts/stop_observability_demo.sh
```

## Positive proof

The one-shot client now owns one root `demo.reference_flow` trace and remains alive until
the Collector receipt contains both MCP client and server spans for that trace. This
matches the positive-receipt-before-shutdown ordering used by `a2a-otel-kit` itself.

The run fails unless it proves all of the following:

- Collector, Tempo, and Grafana become ready;
- Collector positively receives exported trace data;
- `mcp.client.streamable_http` and `mcp.server.streamable_http` share one `trace_id`;
- both client and server service resources exist in the receipt;
- the same distributed trace can be retrieved from Tempo by trace ID;
- Grafana has the expected Tempo datasource;
- known OAuth/MCP fixture values are absent from trace telemetry.

This deliberately goes beyond checking exporter flush or endpoint reachability.

The wrapper stops any stale Collector before recreating `traces.jsonl`, so the receipt is never truncated while a Collector may still hold an open file descriptor. The final verifier selects the exact `demo.reference_flow` trace.

The Collector file exporter explicitly uses `append: true`. The file exporter defaults to truncate mode when append is disabled, which is unsuitable for a receipt that must accumulate client and server export batches during one distributed trace.

Tempo 2.9 is intentionally tuned for this local proof with a 5-second `max_block_duration`, 1-second idle/flush checks, and one-minute completed-block retention in the ingester. Production defaults are much longer; the shortened values exist only so the demo can prove backend trace retrieval inside its 30-second verification window.

The local Tempo backend also uses `storage.trace.blocklist_poll: 1s`. Tempo keeps an in-memory blocklist and does not discover newly flushed backend blocks on every query. The production default is much longer; the one-second poll interval bridges the demo's 5-second flush with the one-minute `complete_block_timeout`, preventing a trace-visibility gap inside the verifier window.

All post-start one-shot commands use `docker compose run --no-deps`. The wrapper also snapshots the server, Collector, Tempo, and Grafana container IDs immediately after startup and rejects verification if any ID changes. This guarantees that the receipt, backend ingestion, and trace lookup belong to the same container generation.

## Network boundary

All observability services join the existing Server container's network namespace. Collector listens
on `127.0.0.1:4318`, Tempo receives OTLP on `127.0.0.1:4319`, and Grafana reaches Tempo on
`127.0.0.1:3200`. Only Grafana is published to the host, on `127.0.0.1:3000`.

Anonymous Grafana Admin is local-demo-only and must not be copied to production.

## Immutable images

```text
otel/opentelemetry-collector-contrib:0.153.0@sha256:93aad750175cbf1a973ae1c5886c3371f4d800f61be25cdd26870b8441ffe9fa
grafana/tempo:2.9.0@sha256:65a5789759435f1ef696f1953258b9bbdb18eb571d5ce711ff812d2e128288a4
grafana/grafana:12.2.0@sha256:74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7
```

These match the observability stack currently used by the executable `a2a-otel-kit` demo.
