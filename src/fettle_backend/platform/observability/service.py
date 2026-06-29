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
    "last_24h": timedelta(days=1),
    "last_7d": timedelta(days=7),
    "last_30d": timedelta(days=30),
    "today": timedelta(days=1),
    "yesterday": timedelta(days=2),
    "this_week": timedelta(days=7),
    "this_month": timedelta(days=31),
    "all_time": None,
}

RATING_ORDER = {"critical": 0, "risky": 1, "good": 2, "excellent": 3}
CONSULTATION_ENTRY_METRICS = {
    "session.audio_upload",
    "session.transcription.queue",
    "session.transcription.provider_call",
    "session.transcription.end_to_end",
    "transcription.request",
    "transcription.result",
    "session.report_context.extract",
    "report_context.fallback_path",
}
TRANSCRIPTION_DISRUPTION_THRESHOLD_MS = 10 * 60 * 1000
REPORT_READY_DISRUPTION_THRESHOLD_MS = 5 * 60 * 1000
LATEST_EVENT_STALE_THRESHOLD_MS = {
    "last_24h": 6 * 60 * 60 * 1000,
    "last_7d": 24 * 60 * 60 * 1000,
    "last_30d": 72 * 60 * 60 * 1000,
    "today": 6 * 60 * 60 * 1000,
    "yesterday": 24 * 60 * 60 * 1000,
    "this_week": 24 * 60 * 60 * 1000,
    "this_month": 72 * 60 * 60 * 1000,
}
MINIMUM_CONSULTATIONS_FOR_VERDICT = 5


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
        events = await self._load_events()
        grouped: dict[str, list[KpiEvent]] = defaultdict(list)
        for event in events:
            grouped[event.metric_key].append(event)
        metrics = []
        for metric_key, metric_events in sorted(grouped.items()):
            failures = [event for event in metric_events if event.payload.get("status") == "failure"]
            total = len(metric_events)
            success = total - len(failures)
            latencies = [float(event.payload.get("elapsedMs", 0)) for event in metric_events if event.payload.get("elapsedMs") is not None]
            latest = metric_events[-1]
            metrics.append(
                {
                    "metric": metric_key,
                    "section": section_for_metric(metric_key),
                    "total": total,
                    "success": success,
                    "failure": len(failures),
                    "successRate": self._to_share(success, total),
                    "rating": classify_rating(total, len(failures)),
                    "sampleStatus": "ready" if total >= 3 else "low_sample",
                    "minimumSampleSize": 3,
                    "avgDurationMs": mean(latencies) if latencies else None,
                    "lastSeenAt": latest.created_at.isoformat(),
                    **({"lastFailureAt": failures[-1].created_at.isoformat()} if failures else {}),
                    **({"lastError": str(failures[-1].payload.get("error"))} if failures and failures[-1].payload.get("error") else {}),
                    **({"lastProvider": str(latest.payload.get("provider"))} if latest.payload.get("provider") else {}),
                }
            )
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "minimumSampleSize": 3,
            "metrics": metrics,
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
                        "successRate": self._to_share(total - failure_count, total),
                    }
                )
            if not metrics:
                continue
            section_rating = min((str(metric["rating"]) for metric in metrics), key=lambda item: RATING_ORDER[item])
            sections.append(
                {
                    "section": section_name,
                    "rating": section_rating,
                    "sampleStatus": "ready" if sum(metric["total"] for metric in metrics) >= 3 else "low_sample",
                    "successRate": self._to_share(sum(metric["success"] for metric in metrics), sum(metric["total"] for metric in metrics)),
                    "total": sum(metric["total"] for metric in metrics),
                    "success": sum(metric["success"] for metric in metrics),
                    "failure": sum(metric["failure"] for metric in metrics),
                    "metrics": metrics,
                }
            )

        sections.sort(key=lambda item: (RATING_ORDER[str(item["rating"])], str(item["section"])) if focus else str(item["section"]))
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "minimumSampleSize": 3,
            "notifications": [],
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
                    "total": len(provider_events),
                    "success": len(provider_events) - len(failures),
                    "failure": len(failures),
                    "successRate": self._to_share(len(provider_events) - len(failures), len(provider_events)),
                    "avgLatencyMs": mean(latencies) if latencies else None,
                    "p95LatencyMs": max(latencies) if latencies else None,
                    "p99LatencyMs": max(latencies) if latencies else None,
                    "topErrors": [
                        {"reason": reason, "count": count}
                        for reason, count in sorted(error_counts.items(), key=lambda item: item[1], reverse=True)
                    ],
                }
            )
        return {"generatedAt": datetime.now(UTC).isoformat(), "window": window or "today", "providers": providers}

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
            latencies = [float(event.payload.get("elapsedMs", 0)) for event in metric_events if event.payload.get("elapsedMs") is not None]
            stages.append(
                {
                    "metric": metric,
                    "rating": classify_rating(len(metric_events), failure_count),
                    "total": len(metric_events),
                    "success": len(metric_events) - failure_count,
                    "failure": failure_count,
                    "successRate": self._to_share(len(metric_events) - failure_count, len(metric_events)),
                    "avgDurationMs": mean(latencies) if latencies else None,
                    "lastSeenAt": metric_events[-1].created_at.isoformat() if metric_events else datetime.now(UTC).isoformat(),
                }
            )
        return {"generatedAt": datetime.now(UTC).isoformat(), "window": window or "today", "stages": stages}

    async def get_report_context_fallback_diagnostics(self, *, window: str | None = None) -> dict:
        events = [event for event in await self._load_events(window) if event.metric_key == "report_context.fallback_path"]
        total = len(events)
        fallback_used = sum(1 for event in events if bool(event.payload.get("fallbackUsed")))
        by_operation_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "fallbackUsed": 0, "primaryPath": 0, "failure": 0, "success": 0})
        for event in events:
            operation = str(event.payload.get("operation", "unknown"))
            by_operation_counts[operation]["total"] += 1
            by_operation_counts[operation]["fallbackUsed"] += 1 if bool(event.payload.get("fallbackUsed")) else 0
            by_operation_counts[operation]["primaryPath"] += 0 if bool(event.payload.get("fallbackUsed")) else 1
            by_operation_counts[operation]["failure"] += 1 if event.payload.get("status") == "failure" else 0
            by_operation_counts[operation]["success"] += 1 if event.payload.get("status") == "success" else 0
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "window": window or "today",
            "summary": {
                "total": total,
                "fallbackUsed": fallback_used,
                "primaryPath": total - fallback_used,
                "failure": sum(1 for event in events if event.payload.get("status") == "failure"),
                "success": sum(1 for event in events if event.payload.get("status") == "success"),
                "fallbackRate": (fallback_used / total) if total else 0,
            },
            "byOperation": [
                {
                    "operation": operation,
                    **counts,
                    "fallbackRate": (counts["fallbackUsed"] / counts["total"]) if counts["total"] else 0,
                }
                for operation, counts in sorted(by_operation_counts.items())
            ],
            "trend": [],
        }

    async def get_event_trends(self, *, window: str | None = None) -> dict:
        events = await self._load_events(window)
        bucket = {
            "bucket": datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "totalEvents": len(events),
            "failures": sum(1 for event in events if event.payload.get("status") == "failure"),
            "successRate": self._to_share(len(events) - sum(1 for event in events if event.payload.get("status") == "failure"), len(events)),
        }
        stage_groups: dict[str, list[KpiEvent]] = defaultdict(list)
        for event in events:
            if "elapsedMs" in event.payload:
                stage_groups[event.metric_key].append(event)
        stage_latency = []
        for metric, metric_events in sorted(stage_groups.items()):
            latencies = [float(event.payload.get("elapsedMs", 0)) for event in metric_events]
            stage_latency.append({"metric": metric, "points": [{"bucket": bucket["bucket"], "avgLatencyMs": mean(latencies) if latencies else 0, "samples": len(latencies)}]})
        return {"generatedAt": datetime.now(UTC).isoformat(), "window": window or "today", "points": [bucket], "stageLatency": stage_latency}

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

    async def get_ops_summary(self, *, window: str = "last_24h") -> dict:
        current_events = await self._load_events(window)
        current = self._aggregate_consultation_ops(current_events)
        previous = await self._read_previous_window_aggregate(window)
        freshness = self._build_freshness(window, current.get("latestCriticalEventAt"))
        confidence = {
            "minimumConsultations": MINIMUM_CONSULTATIONS_FOR_VERDICT,
            "consultationCount": current["consultationsEntered"],
            "hasEnoughVolume": current["consultationsEntered"] >= MINIMUM_CONSULTATIONS_FOR_VERDICT,
        }
        doctor_path_status = self._resolve_doctor_path_status(current, freshness["isStale"], confidence["hasEnoughVolume"])
        overall_status = self._resolve_overall_status(current, doctor_path_status, freshness["isStale"], confidence["hasEnoughVolume"])
        return {
            "generatedAt": datetime.now(UTC).isoformat(),
            "window": window,
            "previousWindowLabel": previous["label"] if previous else None,
            "trafficScope": {"label": "Consultation sessions in selected window"},
            "freshness": freshness,
            "confidence": confidence,
            "verdict": {
                "overallStatus": overall_status,
                "doctorPathStatus": doctor_path_status,
                "summary": self._build_verdict_summary(current, overall_status),
                "causes": self._build_cause_list(current, overall_status),
                "actions": self._build_actions(current, overall_status),
                "reassurance": self._build_reassurance(current, overall_status),
            },
            "totals": {
                "consultationsEntered": current["consultationsEntered"],
                "healthy": current["healthy"],
                "degradedProtected": current["degradedProtected"],
                "doctorVisibleDisruption": current["disrupted"],
                "doctorVisibleFailure": current["failed"],
            },
            "shares": {
                "healthy": self._to_share(current["healthy"], current["consultationsEntered"]),
                "degradedProtected": self._to_share(current["degradedProtected"], current["consultationsEntered"]),
                "doctorVisibleDisruption": self._to_share(current["disrupted"], current["consultationsEntered"]),
                "doctorVisibleFailure": self._to_share(current["failed"], current["consultationsEntered"]),
            },
            "comparison": None
            if previous is None
            else {
                "label": previous["label"],
                "consultationsEntered": previous["aggregate"]["consultationsEntered"],
                "healthy": previous["aggregate"]["healthy"],
                "degradedProtected": previous["aggregate"]["degradedProtected"],
                "doctorVisibleDisruption": previous["aggregate"]["disrupted"],
                "doctorVisibleFailure": previous["aggregate"]["failed"],
            },
            "disruptionByStage": current["disruptionByStage"],
        }

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

    async def _read_previous_window_aggregate(self, window: str) -> dict | None:
        if window == "all_time":
            return None
        current_range = self._resolve_window_range(window)
        duration = current_range["end"] - current_range["start"]
        previous_events = await self._load_events_between(current_range["start"] - duration, current_range["start"])
        return {"label": "Previous equivalent window", "aggregate": self._aggregate_consultation_ops(previous_events)}

    async def _load_events_between(self, start: datetime, end: datetime) -> list[KpiEvent]:
        stmt = select(KpiEvent).where(KpiEvent.created_at >= start, KpiEvent.created_at <= end).order_by(KpiEvent.created_at.asc())
        return list((await self.session.scalars(stmt)).all())

    def _resolve_window_range(self, window: str) -> dict[str, datetime]:
        now = datetime.now(UTC)
        delta = WINDOWS.get(window) or timedelta(days=1)
        return {"start": now - delta, "end": now}

    def _aggregate_consultation_ops(self, events: list[KpiEvent]) -> dict:
        sessions: dict[str, dict] = {}
        latest_critical_event_at: str | None = None
        provider_failures = 0
        report_fallbacks = 0
        sync_failures = 0
        sync_dead_letters = 0

        for event in events:
            payload = event.payload or {}
            metric = event.metric_key
            status = str(payload.get("status", ""))
            session_id = payload.get("sessionId")
            timestamp = event.created_at.isoformat()

            if metric.startswith("provider.") and status == "failure":
                provider_failures += 1
            if metric == "report_context.fallback_path" and payload.get("fallbackUsed") is True:
                report_fallbacks += 1
            if metric == "fettle_sync.failure" and status == "failure":
                sync_failures += 1
            if metric == "fettle_sync.dead_letter" and status == "failure":
                sync_dead_letters += 1
            if not session_id:
                continue

            aggregate = sessions.setdefault(
                str(session_id),
                {
                    "sessionId": str(session_id),
                    "enteredCriticalPath": False,
                    "startedAt": None,
                    "lastSeenAt": None,
                    "transcriptionSucceededAt": None,
                    "transcriptionLatencyMs": None,
                    "transcriptionFailed": False,
                    "transcriptionRescued": False,
                    "reportReadyAt": None,
                    "reportReadyLatencyMs": None,
                    "reportFailed": False,
                    "reportRescued": False,
                    "syncFailed": False,
                    "syncDeadLetter": False,
                    "syncSucceeded": False,
                },
            )

            if metric in CONSULTATION_ENTRY_METRICS:
                aggregate["enteredCriticalPath"] = True
            if aggregate["startedAt"] is None or timestamp < aggregate["startedAt"]:
                aggregate["startedAt"] = timestamp
            if aggregate["lastSeenAt"] is None or timestamp > aggregate["lastSeenAt"]:
                aggregate["lastSeenAt"] = timestamp
            if self._is_critical_metric(metric) and (latest_critical_event_at is None or timestamp > latest_critical_event_at):
                latest_critical_event_at = timestamp

            if metric == "transcription.result" and status == "failure":
                aggregate["transcriptionRescued"] = True
            if metric == "session.transcription.stale_recovery" and status == "success":
                aggregate["transcriptionRescued"] = True
            if metric == "session.transcription.end_to_end":
                if status == "success":
                    aggregate["transcriptionSucceededAt"] = timestamp
                    if isinstance(payload.get("elapsedMs"), (int, float)):
                        aggregate["transcriptionLatencyMs"] = float(payload["elapsedMs"])
                else:
                    aggregate["transcriptionFailed"] = True
            if metric == "session.transcription.dead_letter" and status == "failure":
                aggregate["transcriptionFailed"] = True
            if metric in {"session.report_context.extract", "report_context.extract_parse"}:
                if status == "success":
                    aggregate["reportReadyAt"] = timestamp
                    if isinstance(payload.get("elapsedMs"), (int, float)):
                        aggregate["reportReadyLatencyMs"] = float(payload["elapsedMs"])
                else:
                    aggregate["reportFailed"] = True
            if metric == "report_context.fallback_path" and payload.get("fallbackUsed") is True and status == "success":
                aggregate["reportRescued"] = True
            if metric == "fettle_sync.failure" and status == "failure":
                aggregate["syncFailed"] = True
            if metric == "fettle_sync.dead_letter" and status == "failure":
                aggregate["syncDeadLetter"] = True
            if metric == "fettle_sync.success" and status == "success":
                aggregate["syncSucceeded"] = True

        outcomes = [self._classify_consultation(item) for item in sessions.values() if item["enteredCriticalPath"]]
        consultations_entered = len(outcomes)
        healthy = len([item for item in outcomes if item["status"] == "healthy"])
        degraded = len([item for item in outcomes if item["status"] == "degraded_protected"])
        disrupted = len([item for item in outcomes if item["status"] == "doctor_visible_disruption"])
        failed = len([item for item in outcomes if item["status"] == "doctor_visible_failure"])
        return {
            "consultationOutcomes": outcomes,
            "consultationsEntered": consultations_entered,
            "healthy": healthy,
            "degradedProtected": degraded,
            "disrupted": disrupted,
            "failed": failed,
            "doctorVisibleDisruptionCount": len([item for item in outcomes if item["doctorVisibleDisruption"]]),
            "rescuedConsultations": len([item for item in outcomes if item["rescuedPath"]]),
            "internalPainConsultations": len([item for item in outcomes if item["internalPain"]]),
            "disruptionByStage": self._summarize_disruption_by_stage(outcomes, consultations_entered),
            "syncDeadLetters": sync_dead_letters,
            "syncFailures": sync_failures,
            "providerFailures": provider_failures,
            "reportFallbacks": report_fallbacks,
            **({"latestCriticalEventAt": latest_critical_event_at} if latest_critical_event_at else {}),
        }

    def _classify_consultation(self, session: dict) -> dict:
        now_ms = datetime.now(UTC).timestamp() * 1000
        last_seen_ms = datetime.fromisoformat(session["lastSeenAt"]).timestamp() * 1000 if session.get("lastSeenAt") else now_ms
        transcription_pending_too_long = (
            not session.get("transcriptionSucceededAt")
            and not session.get("transcriptionFailed")
            and session.get("enteredCriticalPath")
            and now_ms - last_seen_ms >= TRANSCRIPTION_DISRUPTION_THRESHOLD_MS
        )
        report_pending_too_long = (
            not session.get("reportReadyAt")
            and not session.get("reportFailed")
            and bool(session.get("transcriptionSucceededAt"))
            and (session.get("reportRescued") or session.get("reportReadyLatencyMs") is not None)
            and now_ms - last_seen_ms >= REPORT_READY_DISRUPTION_THRESHOLD_MS
        )
        if session.get("transcriptionFailed") or session.get("reportFailed"):
            return {
                "sessionId": session["sessionId"],
                "status": "doctor_visible_failure",
                "stage": "transcription" if session.get("transcriptionFailed") else "report_ready",
                "doctorVisibleDisruption": True,
                "rescuedPath": session.get("transcriptionRescued") or session.get("reportRescued"),
                "internalPain": session.get("syncFailed") or session.get("syncDeadLetter"),
            }
        if (
            transcription_pending_too_long
            or report_pending_too_long
            or (isinstance(session.get("transcriptionLatencyMs"), (int, float)) and session["transcriptionLatencyMs"] >= TRANSCRIPTION_DISRUPTION_THRESHOLD_MS)
            or (isinstance(session.get("reportReadyLatencyMs"), (int, float)) and session["reportReadyLatencyMs"] >= REPORT_READY_DISRUPTION_THRESHOLD_MS)
        ):
            return {
                "sessionId": session["sessionId"],
                "status": "doctor_visible_disruption",
                "stage": "transcription"
                if transcription_pending_too_long
                or (isinstance(session.get("transcriptionLatencyMs"), (int, float)) and session["transcriptionLatencyMs"] >= TRANSCRIPTION_DISRUPTION_THRESHOLD_MS)
                else "report_ready",
                "doctorVisibleDisruption": True,
                "rescuedPath": session.get("transcriptionRescued") or session.get("reportRescued"),
                "internalPain": session.get("syncFailed") or session.get("syncDeadLetter"),
            }
        if session.get("transcriptionRescued") or session.get("reportRescued") or session.get("syncDeadLetter") or session.get("syncFailed"):
            return {
                "sessionId": session["sessionId"],
                "status": "degraded_protected",
                "stage": "sync" if session.get("syncDeadLetter") or session.get("syncFailed") else "transcription",
                "doctorVisibleDisruption": False,
                "rescuedPath": session.get("transcriptionRescued") or session.get("reportRescued"),
                "internalPain": session.get("syncFailed") or session.get("syncDeadLetter"),
            }
        return {
            "sessionId": session["sessionId"],
            "status": "healthy",
            "stage": "unknown",
            "doctorVisibleDisruption": False,
            "rescuedPath": False,
            "internalPain": False,
        }

    def _summarize_disruption_by_stage(self, outcomes: list[dict], denominator: int) -> list[dict]:
        counts: dict[str, int] = defaultdict(int)
        for outcome in outcomes:
            if outcome["status"] not in {"doctor_visible_disruption", "doctor_visible_failure"}:
                continue
            counts[outcome["stage"]] += 1
        labels = {
            "transcription": "Transcript delayed or failed",
            "report_ready": "Report readiness delayed or failed",
            "sync": "Sync issues",
            "unknown": "Other disruption",
        }
        return sorted(
            [
                {
                    "stage": stage,
                    "label": labels.get(stage, "Other disruption"),
                    "count": count,
                    "share": self._to_share(count, denominator),
                }
                for stage, count in counts.items()
            ],
            key=lambda item: item["count"],
            reverse=True,
        )

    def _build_freshness(self, window: str, latest_critical_event_at: str | None) -> dict:
        if window == "all_time":
            return {
                "staleThresholdMinutes": round(LATEST_EVENT_STALE_THRESHOLD_MS["last_30d"] / 60_000),
                "isStale": False,
                **({"latestCriticalEventAt": latest_critical_event_at} if latest_critical_event_at else {}),
            }
        threshold_ms = LATEST_EVENT_STALE_THRESHOLD_MS.get(window, LATEST_EVENT_STALE_THRESHOLD_MS["last_24h"])
        latest_ms = datetime.fromisoformat(latest_critical_event_at).timestamp() * 1000 if latest_critical_event_at else None
        return {
            "staleThresholdMinutes": round(threshold_ms / 60_000),
            "isStale": latest_ms is not None and (datetime.now(UTC).timestamp() * 1000 - latest_ms > threshold_ms),
            **({"latestCriticalEventAt": latest_critical_event_at} if latest_critical_event_at else {}),
        }

    def _resolve_doctor_path_status(self, current: dict, is_stale: bool, has_enough_volume: bool) -> str:
        if is_stale and current["consultationsEntered"] > 0:
            return "monitoring_stale"
        if not has_enough_volume:
            return "insufficient_volume"
        if current["failed"] > 0:
            return "doctor_visible_failure"
        if current["disrupted"] > 0:
            return "doctor_visible_disruption"
        if current["degradedProtected"] > 0:
            return "degraded_protected"
        return "healthy"

    def _resolve_overall_status(self, current: dict, doctor_path_status: str, is_stale: bool, has_enough_volume: bool) -> str:
        if is_stale and current["consultationsEntered"] > 0:
            return "monitoring_stale"
        if not has_enough_volume:
            return "insufficient_volume"
        if doctor_path_status in {"doctor_visible_failure", "doctor_visible_disruption"}:
            return doctor_path_status
        if current["degradedProtected"] > 0 or current["syncDeadLetters"] > 0 or current["syncFailures"] > 0 or current["providerFailures"] > 0:
            return "degraded_protected"
        return "healthy"

    def _build_verdict_summary(self, current: dict, overall_status: str) -> str:
        if overall_status == "monitoring_stale":
            return "Monitoring data is stale for the selected window."
        if overall_status == "insufficient_volume":
            return "Not enough real consultation volume to make a strong judgment yet."
        if overall_status == "doctor_visible_failure":
            return f"{current['failed']} consultation{'s' if current['failed'] != 1 else ''} did not complete acceptably."
        if overall_status == "doctor_visible_disruption":
            return f"{current['disrupted']} consultation{'s' if current['disrupted'] != 1 else ''} had doctor-visible delay or disruption."
        if overall_status == "degraded_protected":
            return f"{current['degradedProtected']} consultation{'s' if current['degradedProtected'] != 1 else ''} completed through a degraded or rescued path."
        return "Consultation flow is healthy for the selected window."

    def _build_cause_list(self, current: dict, overall_status: str) -> list[dict]:
        causes: list[dict] = []
        if overall_status == "monitoring_stale":
            causes.append({"id": "stale-monitoring", "label": "Monitoring stale", "tone": "bad", "summary": "Hero metrics are not fresh enough to trust the operational verdict.", "weight": 100})
        if current["failed"] > 0:
            causes.append({"id": "doctor-visible-failure", "label": "Doctor-visible failures", "tone": "bad", "summary": f"{current['failed']} consultation{'s' if current['failed'] != 1 else ''} failed before the expected outcome was ready.", "weight": 90})
        if current["disrupted"] > 0:
            causes.append({"id": "doctor-visible-disruption", "label": "Doctor-visible disruption", "tone": "bad", "summary": f"{current['disrupted']} consultation{'s' if current['disrupted'] != 1 else ''} crossed the acceptable delay threshold.", "weight": 80})
        if current["degradedProtected"] > 0:
            causes.append({"id": "rescued-paths", "label": "Backup paths used", "tone": "watch", "summary": f"{current['degradedProtected']} consultation{'s' if current['degradedProtected'] != 1 else ''} completed only after a degraded or rescued path.", "weight": 70})
        if current["syncDeadLetters"] > 0:
            causes.append({"id": "sync-dead-letter", "label": "Sync backlog needs replay", "tone": "watch", "summary": f"{current['syncDeadLetters']} sync job{'s' if current['syncDeadLetters'] != 1 else ''} reached dead-letter.", "weight": 60})
        if current["providerFailures"] > 0 and not causes:
            causes.append({"id": "provider-errors", "label": "Provider errors observed", "tone": "watch", "summary": f"{current['providerFailures']} provider failure event{'s' if current['providerFailures'] != 1 else ''} occurred, but the doctor-critical path stayed healthy.", "weight": 40})
        if not causes:
            causes.append({"id": "stable", "label": "Doctor-critical flow stable", "tone": "good", "summary": "No doctor-visible failures, delays, or rescued consultations were detected.", "weight": 1})
        causes.sort(key=lambda item: item["weight"], reverse=True)
        return [{k: v for k, v in cause.items() if k != "weight"} for cause in causes[:3]]

    def _build_actions(self, current: dict, overall_status: str) -> list[dict]:
        if overall_status == "monitoring_stale":
            return [{"label": "Check KPI ingestion and fresh event flow", "href": "#diagnostics-pipeline", "kind": "intervene"}]
        if current["failed"] > 0 or current["disrupted"] > 0:
            return [
                {"label": "Inspect consultation path failures first", "href": "#diagnostics-pipeline", "kind": "intervene"},
                {"label": "Review provider diagnostics for the affected window", "href": "#diagnostics-providers", "kind": "investigate"},
            ]
        if current["syncDeadLetters"] > 0:
            return [{"label": "Drain replay backlog before it becomes doctor-visible", "href": "#diagnostics-sync", "kind": "intervene"}]
        if current["degradedProtected"] > 0:
            return [{"label": "Watch rescued-path volume and review fallback diagnostics", "href": "#diagnostics-report-fallback", "kind": "monitor"}]
        return [{"label": "Monitor only", "href": "#diagnostics-trends", "kind": "monitor"}]

    def _build_reassurance(self, current: dict, overall_status: str) -> list[str]:
        reassurance: list[str] = []
        if current["healthy"] > 0:
            reassurance.append(f"{current['healthy']} consultation{'s' if current['healthy'] != 1 else ''} stayed on the primary healthy path.")
        if overall_status != "doctor_visible_failure" and current["syncDeadLetters"] == 0:
            reassurance.append("No sync dead-letter backlog is blocking the current view.")
        return reassurance[:2]

    def _is_critical_metric(self, metric: str) -> bool:
        return metric in {"session.audio_upload", "session.transcription.end_to_end", "session.report_context.extract", "transcription.result"}

    def _to_share(self, value: int, total: int) -> float:
        if total <= 0:
            return 0
        return round((value / total) * 100, 1)
