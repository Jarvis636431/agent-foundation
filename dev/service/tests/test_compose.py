"""Resource bounds on the checkout stores and the shared Langfuse fixture."""

from pathlib import Path
from xml.etree import ElementTree

import yaml

ROOT = Path(__file__).resolve().parents[3]
LOGGING = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}


def test_checkout_stores_bound_logs_wal_and_redis_memory() -> None:
    services = yaml.safe_load((ROOT / "dev/service/compose.yaml").read_text())["services"]
    assert set(services) == {"postgres", "redis"}
    assert all(service["logging"] == LOGGING for service in services.values())
    assert services["postgres"]["command"] == ["postgres", "-c", "max_wal_size=256MB", "-c", "min_wal_size=64MB"]
    redis_command = services["redis"]["command"]
    assert redis_command[0] == "redis-server"
    assert dict(zip(redis_command[1::2], redis_command[2::2], strict=True)) == {
        "--appendonly": "yes",
        "--maxmemory": "256mb",
        "--maxmemory-policy": "noeviction",
    }


def test_shared_langfuse_bounds_logs_and_clickhouse_spill() -> None:
    compose = yaml.safe_load((ROOT / "dev/observability/langfuse.compose.yaml").read_text())
    services = compose["services"]
    assert len(services) == 6
    assert all(service["logging"] == LOGGING and service["restart"] == "no" for service in services.values())
    assert services["langfuse-clickhouse"]["tmpfs"] == ["/tmp:size=512m,mode=1777"]
    config = ElementTree.fromstring(compose["configs"]["clickhouse-local"]["content"])
    assert config.findtext("tmp_path") == "/tmp/clickhouse/"
    assert config.findtext("logger/size") == "10M"
    assert config.findtext("logger/count") == "2"
    for tag in (
        "query_log",
        "query_thread_log",
        "query_views_log",
        "query_metric_log",
        "part_log",
        "session_log",
        "text_log",
        "error_log",
        "crash_log",
        "asynchronous_insert_log",
        "latency_log",
        "trace_log",
        "metric_log",
        "asynchronous_metric_log",
        "processors_profile_log",
        "opentelemetry_span_log",
        "background_schedule_pool_log",
    ):
        assert (entry := config.find(tag)) is not None and entry.attrib == {"remove": "1"}, tag
