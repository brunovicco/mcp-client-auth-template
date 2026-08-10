from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_OVERLAY = _ROOT / "compose.observability.yml"
_COLLECTOR = _ROOT / "observability/otel-collector.yaml"
_TEMPO = _ROOT / "observability/tempo.yaml"
_DATASOURCE = _ROOT / "observability/grafana/provisioning/datasources/tempo.yaml"
_RUNNER = _ROOT / "scripts/run_observability_demo.sh"
_VERIFIER = _ROOT / "scripts/verify_observability_demo.py"

_COLLECTOR_IMAGE = (
    "otel/opentelemetry-collector-contrib:0.153.0@"
    "sha256:93aad750175cbf1a973ae1c5886c3371f4d800f61be25cdd26870b8441ffe9fa"
)
_TEMPO_IMAGE = (
    "grafana/tempo:2.9.0@sha256:65a5789759435f1ef696f1953258b9bbdb18eb571d5ce711ff812d2e128288a4"
)
_GRAFANA_IMAGE = (
    "grafana/grafana:12.2.0@sha256:74144189b38447facf737dfd0f3906e42e0776212bf575dc3334c3609183adf7"
)


def test_observability_images_are_immutable() -> None:
    text = _OVERLAY.read_text(encoding="utf-8")
    assert _COLLECTOR_IMAGE in text
    assert _TEMPO_IMAGE in text
    assert _GRAFANA_IMAGE in text


def test_overlay_keeps_observability_on_shared_loopback() -> None:
    text = _OVERLAY.read_text(encoding="utf-8")
    assert text.count('network_mode: "service:server"') == 4
    assert '"127.0.0.1:3000:3000"' in text
    assert "4318:4318" not in text
    assert "3200:3200" not in text


def test_export_is_explicit_opt_in() -> None:
    text = _OVERLAY.read_text(encoding="utf-8")
    endpoint = 'A2A_OTEL_OTLP_ENDPOINT: "http://127.0.0.1:4318/v1/traces"'
    assert text.count('A2A_OTEL_ENABLED: "true"') == 2
    assert text.count(endpoint) == 2


def test_collector_has_receipt_and_tempo_export() -> None:
    text = _COLLECTOR.read_text(encoding="utf-8")
    assert "endpoint: 0.0.0.0:4318" in text
    assert "endpoint: http://127.0.0.1:4319" in text
    assert "path: /receipts/traces.jsonl" in text
    assert "exporters: [otlphttp/tempo, file/demo]" in text


def test_collector_receipt_is_cumulative_across_export_batches() -> None:
    text = _COLLECTOR.read_text(encoding="utf-8")

    assert "path: /receipts/traces.jsonl" in text
    assert "append: true" in text


def test_tempo_uses_separate_internal_otlp_port() -> None:
    text = _TEMPO.read_text(encoding="utf-8")
    assert "http_listen_port: 3200" in text
    assert "endpoint: 0.0.0.0:4319" in text


def test_tempo_flushes_demo_blocks_within_verifier_window() -> None:
    text = _TEMPO.read_text(encoding="utf-8")

    assert "trace_idle_period: 1s" in text
    assert "flush_check_period: 1s" in text
    assert "max_block_duration: 5s" in text
    assert "complete_block_timeout: 1m" in text
    assert "block_retention: 1h" in text


def test_tempo_blocklist_poll_bridges_fast_flush_query_window() -> None:
    text = _TEMPO.read_text(encoding="utf-8")

    assert "blocklist_poll: 1s" in text
    assert "complete_block_timeout: 1m" in text
    assert "max_block_duration: 5s" in text


def test_grafana_datasource_points_to_shared_tempo() -> None:
    text = _DATASOURCE.read_text(encoding="utf-8")
    assert "type: tempo" in text
    assert "url: http://127.0.0.1:3200" in text


def test_demo_waits_for_its_own_positive_collector_receipt() -> None:
    overlay = _OVERLAY.read_text(encoding="utf-8")
    runner = (_ROOT / "scripts/compose_reference_demo.py").read_text(encoding="utf-8")

    assert 'P1_7C_RECEIPT_PATH: "/receipts/traces.jsonl"' in overlay
    assert '"./.demo-observability:/receipts:ro"' in overlay
    assert "demo.reference_flow" in runner
    assert "_wait_for_collector_receipt(trace_id)" in runner
    assert "OpenTelemetry client force_flush timed out" in runner


def test_receipt_is_recreated_only_after_stale_collector_is_stopped() -> None:
    text = _RUNNER.read_text(encoding="utf-8")

    stop_index = text.index("Removing stale stack before touching Collector evidence")
    receipt_index = text.index("Preparing fresh Collector receipt")
    recreate_index = text.index('rm -f "$RECEIPT_FILE"')

    assert stop_index < receipt_index < recreate_index


def test_verifier_anchors_on_reference_flow_root_trace() -> None:
    text = _VERIFIER.read_text(encoding="utf-8")

    assert "demo.reference_flow" in text
    assert "_reference_trace_id" in text
    assert "_wait_reference_trace" in text


def test_keep_preserves_failed_stack_for_diagnostics() -> None:
    text = _RUNNER.read_text(encoding="utf-8")

    assert 'if [[ "$KEEP" == "true" ]]' in text
    assert "P1.7c stack preserved after failure for diagnostics." in text


def test_one_shots_cannot_recreate_observability_dependencies() -> None:
    text = _RUNNER.read_text(encoding="utf-8")

    assert "compose run --rm --no-deps verifier" in text
    assert "compose run --rm --no-deps demo" in text
    assert "compose run --rm verifier" not in text
    assert "compose run --rm demo" not in text


def test_runner_rejects_cross_generation_trace_verification() -> None:
    text = _RUNNER.read_text(encoding="utf-8")

    assert 'SERVER_CONTAINER_ID="$(compose ps -q server)"' in text
    assert 'COLLECTOR_CONTAINER_ID="$(compose ps -q collector)"' in text
    assert 'TEMPO_CONTAINER_ID="$(compose ps -q tempo)"' in text
    assert 'GRAFANA_CONTAINER_ID="$(compose ps -q grafana)"' in text
    assert "assert_stack_identity()" in text
    assert "Refusing to verify traces across different container generations." in text


def test_runner_requires_positive_verification() -> None:
    text = _RUNNER.read_text(encoding="utf-8")
    assert "set -Eeuo pipefail" in text
    assert "--wait-stack" in text
    assert "--verify-traces" in text
    assert "--keep" in text


def test_verifier_checks_continuity_backends_and_privacy() -> None:
    text = _VERIFIER.read_text(encoding="utf-8")
    assert "mcp.client.streamable_http" in text
    assert "mcp.server.streamable_http" in text
    assert "/api/traces/" in text
    assert "/api/datasources/name/Tempo" in text
    assert "mcp:tools:health" in text
