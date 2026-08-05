from __future__ import annotations

from calendar import month_abbr
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db.session import get_async_session
from api.app.models.auth import User
from api.app.models.hospital.call import (
    CallFeedback,
    CommunityEngagement,
    Escalation,
    OutboundCall,
)
from api.app.models.hospital.patient import Patient
from api.app.models.organization import Organization
from api.app.schemas.hospital.dashboard import (
    AdminDashboardResponse,
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
    FeedbackDistItem,
    FeedbackPatient,
    KPICard,
    KPISummaryResponse,
    MonthlyRevisitPoint,
    PatientEngagementMeta,
    PatientEngagementResponse,
    PatientIntent,
    RecentActivityResponse,
    RecentEscalation,
    ResolutionStatusItem,
    RevisitAnalyticsResponse,
    TimeGapItem,
)
from api.app.views.deps import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard-outbound"])


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
        return f"{s} seconds ago"
    m = s // 60
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = m // 60
    if h < 24:
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = h // 24
    return f"{d} day{'s' if d != 1 else ''} ago"


@router.get("/kpi", response_model=KPISummaryResponse)
async def kpi_summary(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> KPISummaryResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start_of_this_week = today - timedelta(days=today.weekday())
    start_of_last_week = start_of_this_week - timedelta(days=7)

    async def _count_feedback(
        start: date, end: date, call_status: str | None = None
    ) -> int:
        q = (
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(CallFeedback.called_at) >= start,
                func.date(CallFeedback.called_at) <= end,
            )
        )
        if call_status:
            q = q.where(CallFeedback.call_status == call_status)
        return (await session.scalar(q)) or 0

    async def _count_escalations(start: date, end: date) -> int:
        return (
            await session.scalar(
                select(func.count(Escalation.id))
                .join(Patient, Escalation.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(Escalation.escalated_at) >= start,
                    func.date(Escalation.escalated_at) <= end,
                )
            )
        ) or 0

    # KPI 1: Total patients contacted
    contact_this_week = await _count_feedback(start_of_this_week, today)
    contact_last_week = await _count_feedback(
        start_of_last_week, start_of_this_week - timedelta(days=1)
    )
    total_contacts = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ) or 0

    if contact_last_week > 0:
        contact_change = (
            (contact_this_week - contact_last_week) / contact_last_week * 100
        )
        contact_trend = "up" if contact_change > 0 else "down"
    else:
        contact_change = 100.0 if contact_this_week > 0 else 0.0
        contact_trend = "up" if contact_this_week > 0 else "flat"

    # KPI 2: Call answer rate
    connected_this_week = await _count_feedback(start_of_this_week, today, "connected")
    connected_last_week = await _count_feedback(
        start_of_last_week, start_of_this_week - timedelta(days=1), "connected"
    )
    total_connected = (
        await session.scalar(
            select(func.count(func.distinct(CallFeedback.patient_id)))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
            )
        )
    ) or 0

    rate_this_week = (
        (connected_this_week / contact_this_week * 100) if contact_this_week else 0
    )
    rate_last_week = (
        (connected_last_week / contact_last_week * 100) if contact_last_week else 0
    )
    connect_change = rate_this_week - rate_last_week
    connect_trend = (
        "up" if connect_change > 0 else ("down" if connect_change < 0 else "flat")
    )
    answer_rate = _round2(
        (total_connected / total_contacts * 100) if total_contacts else 0
    )

    # KPI 3: Community conversion
    comm_this_week = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
                CallFeedback.community_added.is_(True),
                func.date(CallFeedback.called_at) >= start_of_this_week,
                func.date(CallFeedback.called_at) <= today,
            )
        )
    ) or 0
    comm_last_week = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
                CallFeedback.community_added.is_(True),
                func.date(CallFeedback.called_at) >= start_of_last_week,
                func.date(CallFeedback.called_at) < start_of_this_week,
            )
        )
    ) or 0
    total_comm = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.community_added.is_(True),
            )
        )
    ) or 0
    comm_rate = _round2((total_comm / total_contacts * 100) if total_contacts else 0)

    comm_rate_tw = (
        (comm_this_week / connected_this_week * 100) if connected_this_week else 0
    )
    comm_rate_lw = (
        (comm_last_week / connected_last_week * 100) if connected_last_week else 0
    )
    comm_change = comm_rate_tw - comm_rate_lw
    comm_trend = "up" if comm_change > 0 else ("down" if comm_change < 0 else "flat")

    # KPI 4: Escalations
    esc_this_week = await _count_escalations(start_of_this_week, today)
    esc_last_week = await _count_escalations(
        start_of_last_week, start_of_this_week - timedelta(days=1)
    )
    total_esc = (
        await session.scalar(
            select(func.count(Escalation.id))
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ) or 0

    if esc_last_week > 0:
        esc_change = (esc_this_week - esc_last_week) / esc_last_week * 100
        esc_trend = "up" if esc_change > 0 else "down"
    else:
        esc_change = 100.0 if esc_this_week > 0 else 0.0
        esc_trend = "up" if esc_this_week > 0 else "flat"

    return KPISummaryResponse(
        total_patients_contacted=KPICard(
            title="Total Patients Contacted",
            value=f"{total_contacts:,}",
            change=f"{contact_change:+.0f}%",
            trend=contact_trend,
            icon="Users",
            color="blue",
        ),
        call_answer_rate=KPICard(
            title="Call Answer Rate",
            value=f"{answer_rate:.0f}%",
            change=f"{connect_change:+.0f}%",
            trend=connect_trend,
            icon="Phone",
            color="green",
        ),
        community_conversion=KPICard(
            title="Community Conversion",
            value=f"{comm_rate:.0f}%",
            change=f"{comm_change:+.0f}%",
            trend=comm_trend,
            icon="MessageCircle",
            color="purple",
        ),
        escalated_issues=KPICard(
            title="Escalated Issues",
            value=str(total_esc),
            change=f"{esc_change:+.0f}%",
            trend=esc_trend,
            icon="AlertTriangle",
            color="orange",
        ),
    )


@router.get("/admin", response_model=AdminDashboardResponse)
async def admin_dashboard(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> AdminDashboardResponse:
    org_id = current_user.organization_id

    # All counts in one CTE-style set of scalar queries
    patients_count = (
        await session.scalar(
            select(func.count(Patient.id)).where(Patient.organization_id == org_id)
        )
    ) or 0

    call_count = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ) or 0

    esc_count = (
        await session.scalar(
            select(func.count(Escalation.id))
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ) or 0

    connected_calls = (
        await session.scalar(
            select(func.count(CallFeedback.id))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
            )
        )
    ) or 0

    community_added = (
        await session.scalar(
            select(func.count(CommunityEngagement.id))
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ) or 0

    call_answer_rate = _round2(
        (connected_calls / call_count * 100) if call_count else 0
    )

    # Connected distinct patients who were added to community
    connected_and_added = (
        await session.scalar(
            select(func.count(func.distinct(CallFeedback.patient_id)))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
                CallFeedback.community_added.is_(True),
            )
        )
    ) or 0
    connected_distinct = (
        await session.scalar(
            select(func.count(func.distinct(CallFeedback.patient_id)))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CallFeedback.call_status == "connected",
            )
        )
    ) or 0
    community_conversion = _round2(
        (connected_and_added / connected_distinct * 100) if connected_distinct else 0
    )

    return AdminDashboardResponse(
        patientsCount=patients_count,
        callCount=call_count,
        escalationCount=esc_count,
        hospitalsCount=1,  # In multi-tenant: 1 per JWT org
        connectedCalls=connected_calls,
        communityAdded=community_added,
        callAnswerRate=call_answer_rate,
        communityConversion=community_conversion,
    )


@router.get("/patient-engagement", response_model=PatientEngagementResponse)
async def patient_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PatientEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))
    delta = (today - start).days

    # Contacts data — group by month if >60 days, else by day
    if delta > 60:
        period_col = func.date_trunc("month", OutboundCall.started_at).label("period")
        rows = (
            await session.execute(
                select(period_col, func.count(OutboundCall.id).label("contacts"))
                .join(Patient, OutboundCall.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(OutboundCall.started_at).between(start, today),
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
        period_col = func.date_trunc("day", OutboundCall.started_at).label("period")
        rows = (
            await session.execute(
                select(period_col, func.count(OutboundCall.id).label("contacts"))
                .join(Patient, OutboundCall.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(OutboundCall.started_at).between(start, today),
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
                date=datetime.strptime(ds, "%Y-%m-%d").strftime("%b %d"),
                contacts=count,
            )
            for ds, count in sorted(day_map.items())
        ]

    # Feedback aggregate
    total, answered, avg_duration = (
        await session.execute(
            select(
                func.count(CallFeedback.id).label("total"),
                func.count(CallFeedback.id)
                .filter(CallFeedback.call_status == "connected")
                .label("answered"),
                func.avg(cast(CallFeedback.call_duration, Float)).label("avg_dur"),
            )
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(CallFeedback.called_at).between(start, today),
            )
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
            average_call_duration=_round2(avg_duration),
        ),
    )


@router.get("/community-engagement", response_model=CommunityEngagementResponse)
async def community_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> CommunityEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    # Weekly growth (last 12 weeks)
    start_of_this_week = today - timedelta(days=today.weekday())
    week_starts = [start_of_this_week - timedelta(weeks=i) for i in reversed(range(12))]

    week_rows = (
        await session.execute(
            select(
                func.date_trunc("week", CommunityEngagement.created_at).label("week"),
                func.count(CommunityEngagement.id).label("added"),
            )
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(CommunityEngagement.created_at) >= week_starts[0],
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
                CommunityEngagement.department,
                func.count(CommunityEngagement.id).label("engagement"),
            )
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CommunityEngagement.department.isnot(None),
                CommunityEngagement.department != "",
                func.date(CommunityEngagement.created_at).between(start, today),
            )
            .group_by(CommunityEngagement.department)
            .order_by(func.count(CommunityEngagement.id).desc())
        )
    ).all()
    dept_data = [
        DeptEngagementItem(department=r.department, engagement=r.engagement)
        for r in dept_rows
    ]

    # Metadata aggregations
    total_eng, total_posts = (
        await session.execute(
            select(
                func.count(CommunityEngagement.id).label("total"),
                func.count(CommunityEngagement.id)
                .filter(CommunityEngagement.engagement_type == "post")
                .label("posts"),
            )
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ).one()
    total_eng = total_eng or 0
    total_posts = total_posts or 0
    avg_per_post = _round2((total_eng / total_posts) if total_posts else 0)

    posts_this_week = (
        await session.scalar(
            select(func.count(CommunityEngagement.id))
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                CommunityEngagement.engagement_type == "post",
                func.date(CommunityEngagement.engagement_date) >= start_of_this_week,
            )
        )
    ) or 0

    start_of_month = today.replace(day=1)
    new_members = (
        await session.scalar(
            select(func.count(func.distinct(CommunityEngagement.patient_id)))
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(CommunityEngagement.engagement_date) >= start_of_month,
            )
        )
    ) or 0

    # Conversion rate
    conn_and_added, conn_total = (
        await session.execute(
            select(
                func.count(CallFeedback.id)
                .filter(
                    CallFeedback.call_status == "connected",
                    CallFeedback.community_added.is_(True),
                )
                .label("added"),
                func.count(CallFeedback.id)
                .filter(CallFeedback.call_status == "connected")
                .label("connected"),
            )
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
        )
    ).one()
    conn_and_added = conn_and_added or 0
    conn_total = conn_total or 0
    conversion_rate = _round2((conn_and_added / conn_total * 100) if conn_total else 0)

    # Weekly active users
    weekly_active = (
        await session.scalar(
            select(func.count(func.distinct(CommunityEngagement.patient_id)))
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(CommunityEngagement.engagement_date)
                >= today - timedelta(days=7),
            )
        )
    ) or 0

    # Feedback distribution — group by outcome in SQL
    fb_rows = (
        await session.execute(
            select(CallFeedback.call_outcome, func.count(CallFeedback.id).label("cnt"))
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id, CallFeedback.call_outcome.isnot(None)
            )
            .group_by(CallFeedback.call_outcome)
            .order_by(func.count(CallFeedback.id).desc())
        )
    ).all()

    # For each outcome, fetch top 10 patients with remarks
    feedback_dist: list[FeedbackDistItem] = []
    for fb_row in fb_rows:
        outcome = fb_row.call_outcome
        detail_rows = (
            await session.execute(
                select(
                    Patient.patient_name, CallFeedback.called_at, CallFeedback.remarks
                )
                .join(Patient, CallFeedback.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    CallFeedback.call_outcome == outcome,
                    CallFeedback.remarks.isnot(None),
                )
                .order_by(CallFeedback.called_at.desc())
                .limit(10)
            )
        ).all()
        feedback_dist.append(
            FeedbackDistItem(
                name=outcome.replace("_", " ").title(),
                value=fb_row.cnt,
                patients=[
                    FeedbackPatient(
                        name=d.patient_name,
                        date=d.called_at.strftime("%Y-%m-%d") if d.called_at else "N/A",
                        feedback=d.remarks or "No remarks provided.",
                    )
                    for d in detail_rows
                ],
            )
        )

    # Patient intents from community engagement types
    intent_rows = (
        await session.execute(
            select(
                CommunityEngagement.engagement_type,
                func.count(CommunityEngagement.id).label("cnt"),
            )
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
            .group_by(CommunityEngagement.engagement_type)
            .order_by(func.count(CommunityEngagement.id).desc())
        )
    ).all()

    patient_intents = [
        PatientIntent(
            name=r.engagement_type.replace("_", " ").title(),
            value=r.cnt,
            intent=r.engagement_type.replace("_", " ").title(),
            count=r.cnt,
            patients=[],
        )
        for r in intent_rows
    ]

    return CommunityEngagementResponse(
        communityGrowthData=growth_data,
        departmentEngagementData=dept_data,
        feedbackDistribution=feedback_dist,
        patientIntents=patient_intents,
        metadata=CommunityEngagementMeta(
            community_members=total_eng,
            post_week=posts_this_week,
            new_members=new_members,
            avg_engagement_per_post=avg_per_post,
        ),
        metrics=CommunityEngagementMetrics(
            conversion_rate=conversion_rate,
            poll_participation_rate=0.0,
            weekly_active_users=weekly_active,
        ),
    )


@router.get("/revisit-analytics", response_model=RevisitAnalyticsResponse)
async def revisit_analytics(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RevisitAnalyticsResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    # Fetch distinct (mobile_no, department, date) visit records from patients
    rows = (
        await session.execute(
            select(Patient.mobile_no, Patient.department, Patient.visit_date)
            .where(
                Patient.organization_id == org_id,
                Patient.visit_date.isnot(None),
                Patient.visit_date.between(start, today),
            )
            .distinct()
        )
    ).all()

    # Build visit map — (mobile_no, dept) → set of visit dates
    visit_map: dict[tuple, set] = defaultdict(set)
    for r in rows:
        visit_map[(r.mobile_no, r.department or "")].add(r.visit_date)

    _DEPT_COLORS = {
        "Cardiology": "#EF4444",
        "Orthopedics": "#F59E0B",
        "Pediatrics": "#10B981",
        "General Medicine": "#3B82F6",
        "Dental": "#8B5CF6",
        "Dermatology": "#EC4899",
        "Neurology": "#6366F1",
        "ENT": "#14B8A6",
    }

    dept_counter: Counter = Counter()
    monthly_counter: Counter = Counter()
    time_gap_counter: Counter = Counter()
    all_gaps: list[int] = []
    repeat_visits_this_month = 0
    current_year = today.year
    current_month = today.month

    for (mobile_no, department), visit_days in visit_map.items():
        sorted_days = sorted(visit_days)
        for d in sorted_days:
            monthly_counter[(d.year, d.month)] += 1

        if len(visit_days) > 1:
            dept_counter[department] += len(sorted_days) - 1
            for d in sorted_days[1:]:
                if d.year == current_year and d.month == current_month:
                    repeat_visits_this_month += 1
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

    total_revisits = sum(dept_counter.values()) or 1
    dept_data = [
        DeptRevisitItem(
            name=dept,
            value=cnt,
            percentage=round(cnt / total_revisits * 100, 2),
            color=_DEPT_COLORS.get(dept, "#6B7280"),
        )
        for dept, cnt in dept_counter.items()
    ]

    # Build monthly trend filling all months in range
    curr = date(start.year, start.month, 1)
    monthly_data: list[MonthlyRevisitPoint] = []
    while curr <= today:
        monthly_data.append(
            MonthlyRevisitPoint(
                month=f"{month_abbr[curr.month]} {str(curr.year)[2:]}",
                revisits=monthly_counter.get((curr.year, curr.month), 0),
            )
        )
        if curr.month == 12:
            curr = curr.replace(year=curr.year + 1, month=1)
        else:
            curr = curr.replace(month=curr.month + 1)

    gap_order = ["0-7 days", "8-30 days", "1-3 months", "3-6 months", "6+ months"]
    time_gap_data = [
        TimeGapItem(gap=label, count=time_gap_counter.get(label, 0))
        for label in gap_order
    ]

    return RevisitAnalyticsResponse(
        department_data=dept_data,
        monthly_trend=monthly_data,
        time_gap_distribution=time_gap_data,
        average_revisit_gap=(
            round(sum(all_gaps) / len(all_gaps), 2) if all_gaps else None
        ),
        repeat_visits_this_month=repeat_visits_this_month,
    )


@router.get("/escalation-engagement", response_model=EscalationEngagementResponse)
async def escalation_engagement(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> EscalationEngagementResponse:
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=90))

    # Department escalation counts + issue details
    dept_rows = (
        await session.execute(
            select(
                Escalation.department,
                Escalation.issue_description,
                Patient.patient_name,
                Patient.mobile_no,
            )
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                Escalation.department.isnot(None),
                Escalation.department != "",
                func.date(Escalation.escalated_at).between(start, today),
            )
        )
    ).all()

    dept_dict: dict[str, list] = defaultdict(list)
    for r in dept_rows:
        if r.issue_description:
            dept_dict[r.department].append(
                EscalationIssue(
                    patient_name=r.patient_name,
                    issue=r.issue_description,
                    mobile_no=r.mobile_no,
                )
            )
    dept_esc_data = [
        DeptEscalationItem(department=dept, count=len(issues), issues=issues)
        for dept, issues in sorted(
            dept_dict.items(), key=lambda x: len(x[1]), reverse=True
        )
    ]

    # Resolution status
    status_rows = (
        await session.execute(
            select(Escalation.status, func.count(Escalation.id).label("cnt"))
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(Escalation.escalated_at).between(start, today),
            )
            .group_by(Escalation.status)
        )
    ).all()

    _STATUS_COLORS = {
        "resolved": "#10B981",
        "in-progress": "#F59E0B",
        "pending": "#EF4444",
    }
    total_sum = sum(r.cnt for r in status_rows) or 1

    present: dict[str, bool] = {
        "Pending": False,
        "In-progress": False,
        "Resolved": False,
    }
    resolution_data: list[ResolutionStatusItem] = []
    for status_row in status_rows:
        title = status_row.status.replace("_", " ").title()
        present[title] = True
        resolution_data.append(
            ResolutionStatusItem(
                name=title,
                value=_round2(status_row.cnt / total_sum * 100),
                color=_STATUS_COLORS.get(status_row.status, "#6B7280"),
            )
        )
    for name, seen in present.items():
        if not seen:
            resolution_data.append(
                ResolutionStatusItem(
                    name=name,
                    value=0.0,
                    color=_STATUS_COLORS.get(name.lower(), "#6B7280"),
                )
            )

    # Recent escalations
    recent_rows = (
        await session.execute(
            select(Escalation, Patient)
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(Escalation.escalated_at).between(start, today),
            )
            .order_by(Escalation.escalated_at.desc())
            .limit(5)
        )
    ).all()
    recent_escs = [
        RecentEscalation(
            id=esc.id,
            patient=pat.patient_name,
            issue=esc.issue_description,
            status=esc.status,
            time=_naturaltime(esc.escalated_at),
        )
        for esc, pat in recent_rows
    ]

    # Metadata
    total_esc = (
        await session.scalar(
            select(func.count(Escalation.id))
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                func.date(Escalation.escalated_at).between(start, today),
            )
        )
    ) or 0

    avg_res = await session.scalar(
        select(
            func.avg(
                func.extract("epoch", Escalation.resolved_at - Escalation.escalated_at)
            )
        )
        .join(Patient, Escalation.patient_id == Patient.id)
        .where(
            Patient.organization_id == org_id,
            Escalation.status == "resolved",
            Escalation.resolved_at.isnot(None),
        )
    )
    avg_res_minutes = _round2((avg_res or 0) / 60)

    resolved_today = (
        await session.scalar(
            select(func.count(Escalation.id))
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(
                Patient.organization_id == org_id,
                Escalation.status == "resolved",
                func.date(Escalation.resolved_at) == today,
            )
        )
    ) or 0

    return EscalationEngagementResponse(
        departmentEscalationData=dept_esc_data,
        resolutionStatusData=resolution_data,
        recentEscalations=recent_escs,
        metadata=EscalationMeta(
            total_escalations=total_esc,
            avg_resolution_time=avg_res_minutes,
            resolved_today=resolved_today,
        ),
    )


@router.get("/roi")
async def roi_metrics(
    call_direction: str = Query(default="outbound", enum=["outbound", "inbound"]),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """
    Financial + efficiency ROI metrics.
    Formulae (from legacy):
      Revenue Influenced  = Appointments Booked × ₹650
      Leakage Prevented   = Missed Calls × 0.42 × ₹650
      Staff Hours Saved   = Total Call Duration (minutes) / 60
      Equivalent FTE      = Total Call Duration / 6000
      Cost Efficiency     = FTE × ₹40,000
    """
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    if call_direction == "inbound":
        from api.app.models.hospital.call import InboundCall, InboundFeedback

        attempts = (
            await session.scalar(
                select(func.count(InboundCall.id)).where(
                    InboundCall.organization_id == org_id,
                    func.date(InboundCall.started_at).between(start, today),
                )
            )
        ) or 0

        missed = (
            await session.scalar(
                select(func.count(InboundCall.id)).where(
                    InboundCall.organization_id == org_id,
                    InboundCall.calling_process == "not_connected",
                    func.date(InboundCall.started_at).between(start, today),
                )
            )
        ) or 0

        booked, total_dur = (
            await session.execute(
                select(
                    func.count(InboundFeedback.id)
                    .filter(InboundFeedback.call_outcome == "positive")
                    .label("booked"),
                    func.sum(cast(InboundFeedback.call_duration, Float)).label("tdur"),
                )
                .join(InboundCall, InboundFeedback.inbound_call_id == InboundCall.id)
                .where(InboundCall.organization_id == org_id)
            )
        ).one()
    else:
        attempts = (
            await session.scalar(
                select(func.count(OutboundCall.id))
                .join(Patient, OutboundCall.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(OutboundCall.started_at).between(start, today),
                )
            )
        ) or 0

        missed = (
            await session.scalar(
                select(func.count(OutboundCall.id))
                .join(Patient, OutboundCall.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    OutboundCall.calling_process == "not_connected",
                    func.date(OutboundCall.started_at).between(start, today),
                )
            )
        ) or 0

        booked, total_dur = (
            await session.execute(
                select(
                    func.count(CallFeedback.id)
                    .filter(CallFeedback.call_outcome == "positive")
                    .label("booked"),
                    func.sum(cast(CallFeedback.call_duration, Float)).label("tdur"),
                )
                .join(Patient, CallFeedback.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(CallFeedback.called_at).between(start, today),
                )
            )
        ).one()

    booked = booked or 0
    tdur = float(total_dur or 0)  # minutes

    staff_hours = round(tdur / 60, 1)
    fte = round(tdur / 6000, 2)

    return {
        "error": 0,
        "roi_financial": [
            {"name": "Interactions", "value": attempts, "unit": ""},
            {"name": "Appointments Booked", "value": booked, "unit": ""},
            {"name": "Revenue Influenced", "value": booked * 650, "unit": "₹"},
            {
                "name": "Leakage Prevented",
                "value": int(missed * 0.42 * 650),
                "unit": "₹",
            },
        ],
        "roi_efficiency": [
            {"name": "Staff Hours Saved", "value": staff_hours, "unit": "hrs"},
            {"name": "Equivalent FTE Freed", "value": fte, "unit": "FTE"},
            {"name": "Cost Efficiency Value", "value": int(fte * 40000), "unit": "₹"},
        ],
    }


@router.get("/departments")
async def department_analytics(
    call_direction: str = Query(default="outbound", enum=["outbound", "inbound"]),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Department breakdown: interactions, bookings, conversion rate, revenue."""
    org_id = current_user.organization_id
    today = end_date or date.today()
    start = start_date or (today - timedelta(days=730))

    if call_direction == "inbound":
        from api.app.models.hospital.call import (
            InboundCall,
            InboundCommunityEngagement,
            InboundFeedback,
        )

        # Per-department interaction counts
        dept_rows = (
            await session.execute(
                select(
                    InboundCall.department.label("dept"),
                    func.count(InboundCall.id).label("interactions"),
                )
                .where(
                    InboundCall.organization_id == org_id,
                    func.date(InboundCall.started_at).between(start, today),
                )
                .group_by(InboundCall.department)
            )
        ).all()

        # Bookings per department (positive outcome in feedback)
        book_rows = (
            await session.execute(
                select(
                    InboundCall.department.label("dept"),
                    func.count(InboundFeedback.id).label("bookings"),
                )
                .join(
                    InboundFeedback, InboundFeedback.inbound_call_id == InboundCall.id
                )
                .where(
                    InboundCall.organization_id == org_id,
                    InboundFeedback.call_outcome == "positive",
                )
                .group_by(InboundCall.department)
            )
        ).all()

        book_map = {r.dept: r.bookings for r in book_rows}

        department_table = [
            {
                "department": r.dept or "General",
                "interactions": r.interactions,
                "bookings": book_map.get(r.dept, 0),
                "conversion": f"{(book_map.get(r.dept, 0) / r.interactions * 100 if r.interactions else 0):.1f}%",
                "revenue": book_map.get(r.dept, 0) * 650,
            }
            for r in dept_rows
        ]

        intent_rows = (
            await session.execute(
                select(
                    InboundCommunityEngagement.engagement_type.label("etype"),
                    func.count(InboundCommunityEngagement.id).label("value"),
                )
                .join(
                    InboundCall,
                    InboundCommunityEngagement.inbound_call_id == InboundCall.id,
                )
                .where(
                    InboundCall.organization_id == org_id,
                    func.date(InboundCommunityEngagement.created_at).between(
                        start, today
                    ),
                )
                .group_by(InboundCommunityEngagement.engagement_type)
                .order_by(func.count(InboundCommunityEngagement.id).desc())
            )
        ).all()

    else:
        # Outbound: dept from patients table
        dept_rows = (
            await session.execute(
                select(
                    Patient.department.label("dept"),
                    func.count(Patient.id).label("interactions"),
                )
                .where(Patient.organization_id == org_id)
                .group_by(Patient.department)
            )
        ).all()

        book_rows = (
            await session.execute(
                select(
                    Patient.department.label("dept"),
                    func.count(CallFeedback.id).label("bookings"),
                )
                .join(CallFeedback, CallFeedback.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    CallFeedback.call_outcome == "positive",
                )
                .group_by(Patient.department)
            )
        ).all()
        book_map = {r.dept: r.bookings for r in book_rows}

        department_table = [
            {
                "department": r.dept or "General",
                "interactions": r.interactions,
                "bookings": book_map.get(r.dept, 0),
                "conversion": f"{(book_map.get(r.dept, 0) / r.interactions * 100 if r.interactions else 0):.1f}%",
                "revenue": book_map.get(r.dept, 0) * 650,
            }
            for r in dept_rows
        ]

        intent_rows = (
            await session.execute(
                select(
                    CommunityEngagement.engagement_type.label("etype"),
                    func.count(CommunityEngagement.id).label("value"),
                )
                .join(Patient, CommunityEngagement.patient_id == Patient.id)
                .where(
                    Patient.organization_id == org_id,
                    func.date(CommunityEngagement.created_at).between(start, today),
                )
                .group_by(CommunityEngagement.engagement_type)
                .order_by(func.count(CommunityEngagement.id).desc())
            )
        ).all()

    top_intents = [
        {"intent": r.etype.replace("_", " ").title(), "value": r.value}
        for r in intent_rows
    ]

    return {
        "error": 0,
        "department_table": department_table,
        "top_intents": top_intents,
    }


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def recent_activity(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> RecentActivityResponse:
    org_id = current_user.organization_id

    call_rows = (
        await session.execute(
            select(
                CallFeedback.id,
                CallFeedback.call_outcome,
                CallFeedback.called_at,
                Patient.patient_name,
            )
            .join(Patient, CallFeedback.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
            .order_by(CallFeedback.called_at.desc())
            .limit(5)
        )
    ).all()

    esc_rows = (
        await session.execute(
            select(
                Escalation.id,
                Escalation.issue_description,
                Escalation.status,
                Escalation.escalated_at,
                Patient.patient_name,
            )
            .join(Patient, Escalation.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
            .order_by(Escalation.escalated_at.desc())
            .limit(5)
        )
    ).all()

    comm_rows = (
        await session.execute(
            select(
                CommunityEngagement.id,
                CommunityEngagement.engagement_type,
                CommunityEngagement.created_at,
                Patient.patient_name,
            )
            .join(Patient, CommunityEngagement.patient_id == Patient.id)
            .where(Patient.organization_id == org_id)
            .order_by(CommunityEngagement.created_at.desc())
            .limit(5)
        )
    ).all()

    org_name = (
        await session.scalar(select(Organization.name).where(Organization.id == org_id))
        or ""
    )

    return RecentActivityResponse(
        call_data=[
            {
                "id": str(r.id),
                "call_outcome": r.call_outcome,
                "called_at": r.called_at,
                "hospital": org_name,
                "patient_name": r.patient_name,
            }
            for r in call_rows
        ],
        escalation_data=[
            {
                "id": str(r.id),
                "issue_description": r.issue_description,
                "status": r.status,
                "escalated_at": r.escalated_at,
                "hospital": org_name,
                "patient_name": r.patient_name,
            }
            for r in esc_rows
        ],
        community_data=[
            {
                "id": str(r.id),
                "engagement_type": r.engagement_type,
                "created_at": r.created_at,
                "hospital": org_name,
                "patient_name": r.patient_name,
            }
            for r in comm_rows
        ],
    )
