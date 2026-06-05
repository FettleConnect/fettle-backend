from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fettle_backend.platform.observability.models import KpiEvent

EXPECTED_METRICS: dict[str, list[str]] = {
    "auth": ["auth.pin_login"],
    "session": [
        "session.audio_upload",
        "session.transcription.queue",
        "session.transcription.provider_call",
        "session.transcription.end_to_end",
    ],
    "report_context": [
        "report_context.preprocess",
        "report_context.provider_call",
        "report_context.extract_parse",
        "report_context.fallback_path",
    ],
    "provider": ["provider.gemini.health"],
    "delivery": ["delivery.twilio.whatsapp_status"],
    "sync": ["fettle_sync.queued"],
}

WINDOWS = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=2),
    "this_week": timedelta(days=7),
    "this_month": timedelta(days=31),
    "all_time": None,
}

RATING_ORDER = {"critical": 0, "risky": 1, "good": 2, "excellent": 3}


def section_for_metric(metric_key: str) -> str:
    for section, metrics in EXPECTED_METRICS.items():
        if metric_key in metrics:
            return section
    return metric_key.split(".", 1)[0]


def classify_rating(total: int, failures: int) -> str:
    if total == 0:
        return "good"
    failure_rate = failures / total
    if failure_rate >= 0.5:
        return "critical"
    if failure_rate > 0:
        return "risky"
    if total >= 3:
        return "excellent"
    return "good"


class ObservabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_event(self, metric_key: str, status: str, payload: dict) -> None:
        event = KpiEvent(
            metric_key=metric_key,
            section=section_for_metric(metric_key),
            payload={"status": status, **payload},
        )
        self.session.add(event)
        await self.session.commit()

    async def get_reliability_summary(self) -> dict:
        scorecard = await self.get_scorecard()
        return {
            "generatedAt": scorecard["generatedAt"],
            "sections": [
                {
                    "section": section["section"],
                    "rating": section["rating"],
                    "total": sum(metric["total"] for metric in section["metrics"]),
                    "failures": sum(metric["failure"] for metric in section["metrics"]),
                }
                for section in scorecard["sections"]
            ],
        }

    async def get_scorecard(
        self,
        *,
        focus: bool = False,
        include_expected: bool = False,
        window: str | None = None,
        section: str | None = None,
        rating: str | None = None,
        metric_contains: str | None = None,
    ) -> dict:
        events = await self._load_events(window)
        grouped: dict[str, dict[str, list[KpiEvent]]] = defaultdict(lambda: defaultdict(list))
        for event in events:
            grouped[event.section][event.metric_key].append(event)

        sections = []
        for section_name in sorted(set(grouped.keys()) | set(EXPECTED_METRICS.keys())):
            if section and section_name != section:
                continue
            metrics = []
            metric_names = set(grouped.get(section_name, {}).keys())
            if include_expected:
                metric_names.update(EXPECTED_METRICS.get(section_name, []))
            for metric_name in sorted(metric_names):
                if metric_contains and metric_contains.lower() not in metric_name.lower():
                    continue
                metric_events = grouped.get(section_name, {}).get(metric_name, [])
                failure_count = sum(1 for event in metric_events if event.payload.get("status") == "failure")
                total = len(metric_events)
                metric_rating = classify_rating(total, failure_count)
                if rating and metric_rating != rating:
                    continue
                metrics.append(
                    {
                        "metric": metric_name,
                        "total": total,
                        "success": total - failure_count,
                        "failure": failure_count,
                        "rating": metric_rating,
                    }
                )
            if not metrics:
                continue
            section_rating = min((str(metric["rating"]) for metric in metrics), key=lambda item: RATING_ORDER[item])
            sections.append({"section": section_name, "rating": section_rating, "metrics": metrics})

        sections.sort(key=lambda item: (RATING_ORDER[str(item["rating"])], str(item["section"])) if focus else str(item["section"]))
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "minimumSampleSize": 3,
            "sections": sections,
        }

    async def get_provider_diagnostics(self, *, window: str | None = None) -> dict:
        events = [event for event in await self._load_events(window) if event.metric_key == "provider.gemini.health"]
        groups: dict[str, list[KpiEvent]] = defaultdict(list)
        for event in events:
            groups[str(event.payload.get("provider", "unknown"))].append(event)
        providers = []
        for provider, provider_events in sorted(groups.items()):
            latencies = [
                float(event.payload.get("elapsedMs", 0)) for event in provider_events if event.payload.get("elapsedMs") is not None
            ]
            failures = [event for event in provider_events if event.payload.get("status") == "failure"]
            error_counts: dict[str, int] = defaultdict(int)
            for event in failures:
                error_counts[str(event.payload.get("error", "unknown_error"))] += 1
            providers.append(
                {
                    "provider": provider,
                    "avgLatencyMs": mean(latencies) if latencies else None,
                    "p95LatencyMs": max(latencies) if latencies else None,
                    "topErrors": [
                        {"reason": reason, "count": count}
                        for reason, count in sorted(error_counts.items(), key=lambda item: item[1], reverse=True)
                    ],
                }
            )
        return {"providers": providers}

    async def get_pipeline_diagnostics(self, *, window: str | None = None) -> dict:
        prefixes = ("session.transcription.", "report_context.")
        events = [event for event in await self._load_events(window) if event.metric_key.startswith(prefixes)]
        groups: dict[str, list[KpiEvent]] = defaultdict(list)
        for event in events:
            groups[event.metric_key].append(event)
        expected = {
            "session.transcription.queue",
            "session.transcription.provider_call",
            "report_context.preprocess",
            "report_context.extract_parse",
        }
        stages = []
        for metric in sorted(set(groups.keys()) | expected):
            metric_events = groups.get(metric, [])
            failure_count = sum(1 for event in metric_events if event.payload.get("status") == "failure")
            stages.append({"metric": metric, "total": len(metric_events), "failure": failure_count})
        return {"stages": stages}

    async def get_report_context_fallback_diagnostics(self, *, window: str | None = None) -> dict:
        events = [event for event in await self._load_events(window) if event.metric_key == "report_context.fallback_path"]
        total = len(events)
        fallback_used = sum(1 for event in events if bool(event.payload.get("fallbackUsed")))
        by_operation_counts: dict[str, int] = defaultdict(int)
        for event in events:
            by_operation_counts[str(event.payload.get("operation", "unknown"))] += 1
        return {
            "summary": {
                "total": total,
                "fallbackUsed": fallback_used,
                "fallbackRate": (fallback_used / total) if total else 0,
            },
            "byOperation": [{"operation": operation, "total": count} for operation, count in sorted(by_operation_counts.items())],
        }

    async def get_event_trends(self, *, window: str | None = None) -> dict:
        events = await self._load_events(window)
        bucket = {
            "bucketStart": datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "totalEvents": len(events),
            "failures": sum(1 for event in events if event.payload.get("status") == "failure"),
        }
        stage_groups: dict[str, list[KpiEvent]] = defaultdict(list)
        for event in events:
            if "elapsedMs" in event.payload:
                stage_groups[event.metric_key].append(event)
        stage_latency = []
        for metric, metric_events in sorted(stage_groups.items()):
            latencies = [float(event.payload.get("elapsedMs", 0)) for event in metric_events]
            stage_latency.append({"metric": metric, "points": [{"avgLatencyMs": mean(latencies) if latencies else 0}]})
        return {"points": [bucket], "stageLatency": stage_latency}

    async def get_reliability_alerts(self) -> dict:
        scorecard = await self.get_scorecard(focus=True)
        alerts = []
        for section in scorecard["sections"]:
            for metric in section["metrics"]:
                if metric["rating"] in {"critical", "risky"}:
                    alerts.append(
                        {
                            "section": section["section"],
                            "metric": metric["metric"],
                            "rating": metric["rating"],
                            "total": metric["total"],
                            "failures": metric["failure"],
                        }
                    )
        return {"alerts": alerts}

    async def _load_events(self, window: str | None = None) -> list[KpiEvent]:
        stmt = select(KpiEvent).order_by(KpiEvent.created_at.asc())
        since = self._since_for_window(window)
        if since is not None:
            stmt = stmt.where(KpiEvent.created_at >= since)
        return list((await self.session.scalars(stmt)).all())

    def _since_for_window(self, window: str | None) -> datetime | None:
        normalized = (window or "all_time").lower()
        delta = WINDOWS.get(normalized)
        if normalized == "yesterday":
            return datetime.now(UTC) - timedelta(days=1)
        if delta is None:
            return None
        return datetime.now(UTC) - delta
