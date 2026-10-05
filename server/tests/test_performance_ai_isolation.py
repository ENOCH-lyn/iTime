import pytest

from itime.ai.provider import EndpointPolicyError, enforce_endpoint_policy
from itime.config import Settings
from performance.ai_isolation import (
    AIIsolationState,
    build_ai_isolation_summary,
    parse_ai_isolation_state,
)
from performance.contracts import RequestStat, ResourceSummary


def healthy_resources() -> ResourceSummary:
    return ResourceSummary(
        cpu_mean_ratio=0.5,
        cpu_peak_over_90_seconds=0,
        memory_peak_ratio=0.6,
        memory_growth_ratio=0,
        database_connection_peak_ratio=0.4,
        blocked_over_two_seconds=0,
        pool_timeouts=0,
        server_5xx=0,
        sampling_gap_seconds=0,
    )


def empty_state() -> AIIsolationState:
    return AIIsolationState(
        jobs=0,
        distinct_owners=0,
        queued=0,
        running=0,
        succeeded=0,
        failed=0,
        timed_out=0,
        cancelled=0,
        attempts=0,
        succeeded_attempts=0,
        provider_costs=0,
        reservations_reserved=0,
        reservations_settled=0,
        reservations_released=0,
        usage_ledger_entries=0,
        inconsistent_jobs=0,
        claimed_p95_ms=0,
        end_to_end_p99_ms=0,
    )


def successful_state() -> AIIsolationState:
    return AIIsolationState(
        jobs=20,
        distinct_owners=20,
        queued=0,
        running=0,
        succeeded=20,
        failed=0,
        timed_out=0,
        cancelled=0,
        attempts=20,
        succeeded_attempts=20,
        provider_costs=20,
        reservations_reserved=0,
        reservations_settled=20,
        reservations_released=0,
        usage_ledger_entries=20,
        inconsistent_jobs=0,
        claimed_p95_ms=500,
        end_to_end_p99_ms=1500,
    )


def ordinary_requests(
    *,
    read_p95: float,
    write_p95: float,
) -> tuple[RequestStat, ...]:
    return (
        RequestStat("read::tasks.list", 100, 50, read_p95, 900, 0),
        RequestStat("write::tasks.create", 20, 100, write_p95, 1200, 0),
    )


def ai_requests() -> tuple[RequestStat, ...]:
    return (
        RequestStat("ai::messages.accept", 20, 100, 500, 800, 0),
        RequestStat("ai::accepted-to-claimed", 20, 500, 1000, 1500, 0),
        RequestStat("ai::sse-visible-lag", 20, 100, 500, 1000, 0),
        RequestStat("ai::end-to-end", 20, 1000, 1500, 2000, 0),
    )


def test_ai_isolation_summary_accepts_exact_20_plus_20_contract():
    result = build_ai_isolation_summary(
        initial=empty_state(),
        final=successful_state(),
        ordinary_baseline_requests=ordinary_requests(
            read_p95=200,
            write_p95=300,
        ),
        ordinary_active_requests=ordinary_requests(
            read_p95=400,
            write_p95=600,
        ),
        ai_requests=ai_requests(),
        baseline_resources=healthy_resources(),
        active_resources=healthy_resources(),
        duration_seconds=300,
    )

    assert result.passed
    assert result.violations == ()
    assert result.standard_users == 20
    assert result.ai_users == 20
    assert result.degradation_ratios == {"read": 2, "write": 2}


def test_ai_isolation_summary_rejects_missing_job_and_active_reservation():
    final = successful_state()
    broken = AIIsolationState(
        **{
            **final.__dict__,
            "jobs": 19,
            "succeeded": 19,
            "reservations_reserved": 1,
            "reservations_settled": 19,
            "inconsistent_jobs": 1,
        }
    )

    result = build_ai_isolation_summary(
        initial=empty_state(),
        final=broken,
        ordinary_baseline_requests=ordinary_requests(
            read_p95=200,
            write_p95=300,
        ),
        ordinary_active_requests=ordinary_requests(
            read_p95=300,
            write_p95=400,
        ),
        ai_requests=ai_requests(),
        baseline_resources=healthy_resources(),
        active_resources=healthy_resources(),
        duration_seconds=300,
    )

    assert not result.passed
    assert "jobs delta 19, expected 20" in result.violations
    assert "reservations_reserved delta 1, expected 0" in result.violations
    assert "inconsistent_jobs delta 1, expected 0" in result.violations


def test_ai_isolation_state_parser_rejects_missing_fields():
    value = successful_state().__dict__
    assert parse_ai_isolation_state(value) == successful_state()

    invalid = dict(value)
    invalid.pop("provider_costs")
    try:
        parse_ai_isolation_state(invalid)
    except ValueError as exc:
        assert "字段无效" in str(exc)
    else:
        raise AssertionError("missing state field must be rejected")


def test_test_environment_allows_explicit_http_container_host(
    settings: Settings,
):
    allowed = settings.model_copy(
        update={
            "environment": "test",
            "ai_private_endpoint_hosts": ["host.docker.internal"],
        }
    )
    enforce_endpoint_policy("http://host.docker.internal:18081/v1", allowed)

    production = allowed.model_copy(update={"environment": "production"})
    with pytest.raises(EndpointPolicyError):
        enforce_endpoint_policy(
            "http://host.docker.internal:18081/v1",
            production,
        )
