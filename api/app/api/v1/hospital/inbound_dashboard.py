from __future__ import annotations

"""
Inbound dashboard analytics — mirrors outbound but for InboundCall + InboundFeedback tables.
All aggregations done in SQL.
"""

from calendar import month_abbr
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.api.deps import get_current_user
from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import (
    InboundCall,
    InboundCommunityEngagement,
    InboundEscalation,
    InboundFeedback,
)
from api.app.schemas.hospital.dashboard import (
    CallAnswerItem,
    CommunityEngagementMeta,
    CommunityEngagementMetrics,
    CommunityEngagementResponse,
    CommunityGrowthPoint,
    ContactDataPoint,
    DeptEngagementItem,
    DeptEscalationItem,
    DeptRevisitItem,
    EscalationEngagementResponse,
    EscalationIssue,
    EscalationMeta,
    InboundKPIResponse,
    KPICard,
    MonthlyRevisitPoint,
    PatientEngagementMeta,
    PatientEngagementResponse,
    RecentEscalation,
    ResolutionStatusItem,
    RevisitAnalyticsResponse,
    TimeGapItem,
)

router = APIRouter(prefix="/inbound", tags=["dashboard-inbound"])


def _round2(v: float | None) -> float:
    return round(v or 0, 2)


def _naturaltime(dt: datetime) -> str:
    if dt is None:
        return "unknown"
    now = datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    return f"{s // 3600}h ago"


@router.get("/kpi", response_model=InboundKPIResponse)
async def kpi_inbound(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> InboundKPIResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    # Total inbound attempts
    total_attempts = (
        await session.scalar(
            select(func.count(InboundCall.id)).where(
                InboundCall.organization_id == org_id,
                func.date(InboundCall.started_at).between(start, today),
            )
        )
    ) or 0

    # Feedback base
    base = (
        select(
            func.count(InboundFeedback.id).label("total"),
            func.count(InboundFeedback.id)
            .filter(InboundFeedback.call_outcome == "connected")
            .label("connected"),
            func.count(InboundFeedback.id)
            .filter(InboundFeedback.call_outcome == "positive")
            .label("booked"),
            func.avg(cast(InboundFeedback.call_duration, Float)).label("avg_dur"),
        )
        .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
        .where(
            InboundCall.organization_id == org_id,
            func.date(
                func.coalesce(InboundFeedback.created_at, InboundFeedback.created_at)
            ).between(start, today),
        )
    )
    total_fb, connected_fb, booked, avg_dur = (await session.execute(base)).one()
    total_fb = total_fb or 0
    connected_fb = connected_fb or 0
    booked = booked or 0
    avg_dur_secs = int((avg_dur or 0) * 60)

    # Count calls connected from InboundCall model
    connected_calls = (
        await session.scalar(
            select(func.count(InboundCall.id)).where(
                InboundCall.organization_id == org_id,
                InboundCall.calling_process == "connected",
                func.date(InboundCall.started_at).between(start, today),
            )
        )
    ) or 0
    if connected_fb == 0:
        connected_fb = connected_calls

    ans_rate = _round2((connected_fb / total_attempts * 100) if total_attempts else 0)
    conv_rate = _round2((booked / connected_fb * 100) if connected_fb else 0)

    total_esc = (
        await session.scalar(
            select(func.count(InboundEscalation.id))
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(InboundCall.organization_id == org_id)
        )
    ) or 0

    return InboundKPIResponse(
        interactions=KPICard(
            title="Total Interactions",
            value=f"{total_attempts:,}",
            change="Live",
            trend="up",
            icon="Users",
            color="blue",
        ),
        resolution=KPICard(
            title="Avg Resolution",
            value=f"{avg_dur_secs} sec",
            change="Live",
            trend="up",
            icon="Clock",
            color="green",
        ),
        conversion=KPICard(
            title="Inbound Conversion",
            value=f"{conv_rate:.1f}%",
            change="Target 40%",
            trend="up" if conv_rate > 40 else "down",
            icon="TrendingUp",
            color="purple",
        ),
        noshow=KPICard(
            title="No-Show Rate",
            value="11.0%",
            change="Baseline",
            trend="flat",
            icon="UserX",
            color="red",
        ),
        patients=KPICard(
            title="Inbound Traffic",
            value=f"{total_attempts:,}",
            change="Active",
            trend="up",
            icon="Users",
            color="blue",
        ),
        ans_rate=KPICard(
            title="Inbound Answer Rate",
            value=f"{ans_rate:.0f}%",
            change="Live",
            trend="up",
            icon="Phone",
            color="green",
        ),
        escalation=KPICard(
            title="Escalated Issues",
            value=str(total_esc),
            change="Live",
            trend="up",
            icon="AlertTriangle",
            color="orange",
        ),
        revenue=KPICard(
            title="Revenue Influenced",
            value=f"₹{booked * 650:,}",
            change="Live",
            trend="up",
            icon="TrendingUp",
            color="emerald",
        ),
    )


@router.get("/patient-engagement", response_model=PatientEngagementResponse)
async def patient_engagement_inbound(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PatientEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))
    delta = (today - start).days

    if delta > 60:
        period_col = func.date_trunc("month", InboundCall.started_at).label("period")
        rows = (
            await session.execute(
                select(period_col, func.count(InboundCall.id).label("contacts"))
                .where(
                    InboundCall.organization_id == org_id,
                    func.date(InboundCall.started_at).between(start, today),
                )
                .group_by(period_col)
                .order_by(period_col)
            )
        ).all()
        contacts_data = [
            ContactDataPoint(date=r.period.strftime("%b %Y"), contacts=r.contacts)
            for r in rows
            if r.period
        ]
    else:
        period_col = func.date_trunc("day", InboundCall.started_at).label("period")
        rows = (
            await session.execute(
                select(period_col, func.count(InboundCall.id).label("contacts"))
                .where(
                    InboundCall.organization_id == org_id,
                    func.date(InboundCall.started_at).between(start, today),
                )
                .group_by(period_col)
                .order_by(period_col)
            )
        ).all()
        day_map: dict[str, int] = {
            (start + timedelta(days=i)).strftime("%Y-%m-%d"): 0
            for i in range(delta + 1)
        }
        for r in rows:
            if r.period:
                day_map[r.period.strftime("%Y-%m-%d")] = r.contacts
        contacts_data = [
            ContactDataPoint(
                date=datetime.strptime(ds, "%Y-%m-%d").strftime("%b %d"), contacts=c
            )
            for ds, c in sorted(day_map.items())
        ]

    total, answered, avg_dur = (
        await session.execute(
            select(
                func.count(InboundFeedback.id).label("total"),
                func.count(InboundFeedback.id)
                .filter(InboundFeedback.call_outcome == "connected")
                .label("answered"),
                func.avg(cast(InboundFeedback.call_duration, Float)).label("avg_dur"),
            )
            .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
            .where(InboundCall.organization_id == org_id)
        )
    ).one()
    total = total or 0
    answered = answered or 0
    answer_pct = _round2((answered / total * 100) if total else 0)

    return PatientEngagementResponse(
        contactsData=contacts_data,
        callAnswerData=[
            CallAnswerItem(name="Answered", value=answer_pct, color="#10B981"),
            CallAnswerItem(
                name="Not Answered", value=_round2(100 - answer_pct), color="#EF4444"
            ),
        ],
        metadata=PatientEngagementMeta(
            total_calls_all_period=total,
            average_call_duration=_round2(avg_dur),
        ),
    )


@router.get("/community-engagement", response_model=CommunityEngagementResponse)
async def community_engagement_inbound(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> CommunityEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    start_of_this_week = today - timedelta(days=today.weekday())
    week_starts = [start_of_this_week - timedelta(weeks=i) for i in reversed(range(12))]

    # Weekly community growth
    week_rows = (
        await session.execute(
            select(
                func.date_trunc("week", InboundCommunityEngagement.created_at).label(
                    "week"
                ),
                func.count(InboundCommunityEngagement.id).label("added"),
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundCommunityEngagement.created_at) >= week_starts[0],
            )
            .group_by("week")
        )
    ).all()

    week_map: dict[date, int] = {ws: 0 for ws in week_starts}
    for r in week_rows:
        if r.week:
            wsd = r.week.date() if hasattr(r.week, "date") else r.week
            if wsd in week_map:
                week_map[wsd] = r.added

    growth_data = [
        CommunityGrowthPoint(date=f"Week {i+1}", added=added)
        for i, (_, added) in enumerate(week_map.items())
    ]

    # Department engagement
    dept_rows = (
        await session.execute(
            select(
                InboundCommunityEngagement.department,
                func.count(InboundCommunityEngagement.id).label("eng"),
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(
                InboundCall.organization_id == org_id,
                InboundCommunityEngagement.department.isnot(None),
                InboundCommunityEngagement.department != "",
                func.date(InboundCommunityEngagement.created_at).between(start, today),
            )
            .group_by(InboundCommunityEngagement.department)
            .order_by(func.count(InboundCommunityEngagement.id).desc())
        )
    ).all()
    dept_data = [
        DeptEngagementItem(department=r.department, engagement=r.eng) for r in dept_rows
    ]

    # Metadata
    total_eng, total_posts = (
        await session.execute(
            select(
                func.count(InboundCommunityEngagement.id).label("total"),
                func.count(InboundCommunityEngagement.id)
                .filter(InboundCommunityEngagement.engagement_type == "post")
                .label("posts"),
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(InboundCall.organization_id == org_id)
        )
    ).one()
    total_eng = total_eng or 0
    total_posts = total_posts or 0

    posts_week = (
        await session.scalar(
            select(func.count(InboundCommunityEngagement.id))
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(
                InboundCall.organization_id == org_id,
                InboundCommunityEngagement.engagement_type == "post",
                func.date(InboundCommunityEngagement.created_at) >= start_of_this_week,
            )
        )
    ) or 0

    start_of_month = today.replace(day=1)
    new_members = (
        await session.scalar(
            select(
                func.count(func.distinct(InboundCommunityEngagement.inbound_call_id))
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundCommunityEngagement.created_at) >= start_of_month,
            )
        )
    ) or 0

    # Conversion
    conn_total, conn_added = (
        await session.execute(
            select(
                func.count(InboundFeedback.inbound_call_id.distinct()).label("total"),
                func.count(InboundFeedback.inbound_call_id.distinct())
                .filter(InboundFeedback.community_added.is_(True))
                .label("added"),
            )
            .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                InboundFeedback.call_outcome == "connected",
            )
        )
    ).one()
    conn_total = conn_total or 0
    conn_added = conn_added or 0
    conversion_rate = _round2((conn_added / conn_total * 100) if conn_total else 0)

    weekly_active = (
        await session.scalar(
            select(
                func.count(func.distinct(InboundCommunityEngagement.inbound_call_id))
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundCommunityEngagement.created_at)
                >= today - timedelta(days=7),
            )
        )
    ) or 0

    # Feedback distribution
    fb_rows = (
        await session.execute(
            select(
                InboundFeedback.call_outcome,
                func.count(InboundFeedback.id).label("cnt"),
            )
            .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                InboundFeedback.call_outcome.isnot(None),
            )
            .group_by(InboundFeedback.call_outcome)
            .order_by(func.count(InboundFeedback.id).desc())
        )
    ).all()

    feedback_dist = []
    for r in fb_rows:
        detail_rows = (
            await session.execute(
                select(
                    InboundCall.from_phone_number,
                    InboundFeedback.created_at,
                    InboundFeedback.remarks,
                )
                .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
                .where(
                    InboundCall.organization_id == org_id,
                    InboundFeedback.call_outcome == r.call_outcome,
                )
                .order_by(InboundFeedback.created_at.desc())
                .limit(10)
            )
        ).all()
        feedback_dist.append(
            {
                "name": r.call_outcome.replace("_", " ").title(),
                "value": r.cnt,
                "patients": [
                    {
                        "name": d.from_phone_number or "Unknown",
                        "date": (
                            d.created_at.strftime("%Y-%m-%d") if d.created_at else "N/A"
                        ),
                        "feedback": d.remarks or "No remarks provided.",
                    }
                    for d in detail_rows
                ],
            }
        )

    # Intents from engagement types
    intent_rows = (
        await session.execute(
            select(
                InboundCommunityEngagement.engagement_type,
                func.count(InboundCommunityEngagement.id).label("cnt"),
            )
            .join(
                InboundCall,
                InboundCommunityEngagement.inbound_call_id == InboundCall.id,
            )
            .where(InboundCall.organization_id == org_id)
            .group_by(InboundCommunityEngagement.engagement_type)
            .order_by(func.count(InboundCommunityEngagement.id).desc())
        )
    ).all()
    patient_intents = [
        {
            "name": r.engagement_type.replace("_", " ").title(),
            "value": r.cnt,
            "intent": r.engagement_type.replace("_", " ").title(),
            "count": r.cnt,
            "patients": [],
        }
        for r in intent_rows
    ]

    return CommunityEngagementResponse(
        communityGrowthData=growth_data,
        departmentEngagementData=dept_data,
        feedbackDistribution=feedback_dist,
        patientIntents=patient_intents,
        metadata=CommunityEngagementMeta(
            community_members=total_eng,
            post_week=posts_week,
            new_members=new_members,
            avg_engagement_per_post=_round2(
                (total_eng / total_posts) if total_posts else 0
            ),
        ),
        metrics=CommunityEngagementMetrics(
            conversion_rate=conversion_rate,
            poll_participation_rate=0.0,
            weekly_active_users=weekly_active,
        ),
    )


@router.get("/escalation-engagement", response_model=EscalationEngagementResponse)
async def escalation_engagement_inbound(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EscalationEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=90))

    dept_rows = (
        await session.execute(
            select(
                InboundEscalation.department,
                InboundEscalation.issue_description,
                InboundCall.from_phone_number,
            )
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                InboundEscalation.department.isnot(None),
                InboundEscalation.department != "",
                func.date(InboundEscalation.escalated_at).between(start, today),
            )
        )
    ).all()

    dept_dict: dict[str, list] = defaultdict(list)
    for r in dept_rows:
        if r.issue_description:
            dept_dict[r.department].append(
                EscalationIssue(
                    patient_name=r.from_phone_number or "Unknown",
                    issue=r.issue_description,
                )
            )
    dept_esc_data = [
        DeptEscalationItem(department=dept, count=len(issues), issues=issues)
        for dept, issues in sorted(
            dept_dict.items(), key=lambda x: len(x[1]), reverse=True
        )
    ]

    status_rows = (
        await session.execute(
            select(
                InboundEscalation.status, func.count(InboundEscalation.id).label("cnt")
            )
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundEscalation.escalated_at).between(start, today),
            )
            .group_by(InboundEscalation.status)
        )
    ).all()

    _SC = {"resolved": "#10B981", "in-progress": "#F59E0B", "pending": "#EF4444"}
    total_sum = sum(r.cnt for r in status_rows) or 1
    present: dict[str, bool] = {
        "Pending": False,
        "In-progress": False,
        "Resolved": False,
    }
    resolution_data: list[ResolutionStatusItem] = []
    for r in status_rows:
        title = r.status.replace("_", " ").title()
        present[title] = True
        resolution_data.append(
            ResolutionStatusItem(
                name=title,
                value=_round2(r.cnt / total_sum * 100),
                color=_SC.get(r.status, "#6B7280"),
            )
        )
    for name, seen in present.items():
        if not seen:
            resolution_data.append(
                ResolutionStatusItem(
                    name=name, value=0.0, color=_SC.get(name.lower(), "#6B7280")
                )
            )

    recent_rows = (
        await session.execute(
            select(InboundEscalation, InboundCall.from_phone_number)
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundEscalation.escalated_at).between(start, today),
            )
            .order_by(InboundEscalation.escalated_at.desc())
            .limit(10)
        )
    ).all()
    recent_escs = [
        RecentEscalation(
            id=esc.id,
            patient=phone or "Unknown",
            issue=esc.issue_description,
            status=esc.status,
            time=_naturaltime(esc.escalated_at),
        )
        for esc, phone in recent_rows
    ]

    total_esc = (
        await session.scalar(
            select(func.count(InboundEscalation.id))
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                func.date(InboundEscalation.escalated_at).between(start, today),
            )
        )
    ) or 0

    avg_res = await session.scalar(
        select(
            func.avg(
                func.extract(
                    "epoch",
                    InboundEscalation.resolved_at - InboundEscalation.escalated_at,
                )
            )
        )
        .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
        .where(
            InboundCall.organization_id == org_id,
            InboundEscalation.status == "resolved",
            InboundEscalation.resolved_at.isnot(None),
        )
    )
    avg_res_days = _round2((avg_res or 0) / 86400)

    resolved_today_count = (
        await session.scalar(
            select(func.count(InboundEscalation.id))
            .join(InboundCall, InboundEscalation.inbound_call_id == InboundCall.id)
            .where(
                InboundCall.organization_id == org_id,
                InboundEscalation.status == "resolved",
                func.date(InboundEscalation.resolved_at) == today,
            )
        )
    ) or 0

    return EscalationEngagementResponse(
        departmentEscalationData=dept_esc_data,
        resolutionStatusData=resolution_data,
        recentEscalations=recent_escs,
        metadata=EscalationMeta(
            total_escalations=total_esc,
            avg_resolution_time=avg_res_days,
            resolved_today=resolved_today_count,
        ),
    )


@router.get("/revisit-analytics", response_model=RevisitAnalyticsResponse)
async def revisit_analytics_inbound(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RevisitAnalyticsResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    rows = (
        await session.execute(
            select(
                InboundCall.to_phone_number,
                InboundCall.department,
                InboundCall.started_at,
            ).where(
                InboundCall.organization_id == org_id,
                func.date(InboundCall.started_at).between(start, today),
            )
        )
    ).all()

    visit_map: dict[tuple, set] = defaultdict(set)
    for r in rows:
        visit_map[(r.to_phone_number or "", r.department or "")].add(
            r.started_at.date() if r.started_at else None
        )

    _DC = {"Cardiology": "#EF4444", "Orthopedics": "#F59E0B", "Pediatrics": "#10B981"}
    dept_counter: Counter = Counter()
    monthly_counter: Counter = Counter()
    time_gap_counter: Counter = Counter()
    all_gaps: list[int] = []
    repeat_this_month = 0
    current_month = today.month
    current_year = today.year

    for (phone, dept), days in visit_map.items():
        days = {d for d in days if d is not None}
        sorted_days = sorted(days)
        for d in sorted_days:
            monthly_counter[(d.year, d.month)] += 1
        if len(days) > 1:
            dept_counter[dept] += len(sorted_days) - 1
            for d in sorted_days[1:]:
                if d.year == current_year and d.month == current_month:
                    repeat_this_month += 1
            for i in range(1, len(sorted_days)):
                gap = (sorted_days[i] - sorted_days[i - 1]).days
                all_gaps.append(gap)
                if gap <= 7:
                    time_gap_counter["0-7 days"] += 1
                elif gap <= 30:
                    time_gap_counter["8-30 days"] += 1
                elif gap <= 90:
                    time_gap_counter["1-3 months"] += 1
                elif gap <= 180:
                    time_gap_counter["3-6 months"] += 1
                else:
                    time_gap_counter["6+ months"] += 1

    total_r = sum(dept_counter.values()) or 1
    dept_data = [
        DeptRevisitItem(
            name=d,
            value=c,
            percentage=round(c / total_r * 100, 2),
            color=_DC.get(d, "#6B7280"),
        )
        for d, c in dept_counter.items()
    ]

    curr = date(start.year, start.month, 1)
    monthly_data: list[MonthlyRevisitPoint] = []
    while curr <= today:
        monthly_data.append(
            MonthlyRevisitPoint(
                month=f"{month_abbr[curr.month]} {str(curr.year)[2:]}",
                revisits=monthly_counter.get((curr.year, curr.month), 0),
            )
        )
        curr = (
            curr.replace(month=curr.month + 1)
            if curr.month < 12
            else curr.replace(year=curr.year + 1, month=1)
        )

    gap_order = ["0-7 days", "8-30 days", "1-3 months", "3-6 months", "6+ months"]
    time_gap_data = [
        TimeGapItem(gap=g, count=time_gap_counter.get(g, 0)) for g in gap_order
    ]

    return RevisitAnalyticsResponse(
        department_data=dept_data,
        monthly_trend=monthly_data,
        time_gap_distribution=time_gap_data,
        average_revisit_gap=(
            round(sum(all_gaps) / len(all_gaps), 2) if all_gaps else None
        ),
        repeat_visits_this_month=repeat_this_month,
    )
