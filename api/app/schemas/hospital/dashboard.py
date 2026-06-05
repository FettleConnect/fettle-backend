from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class KPICard(BaseModel):
    title: str
    value: str
    change: str
    trend: str
    icon: str
    color: str


class KPISummaryResponse(BaseModel):
    total_patients_contacted: KPICard
    call_answer_rate: KPICard
    community_conversion: KPICard
    escalated_issues: KPICard


class AdminDashboardResponse(BaseModel):
    error: int = 0
    patientsCount: int
    callCount: int
    escalationCount: int
    hospitalsCount: int
    connectedCalls: int
    communityAdded: int
    callAnswerRate: float
    communityConversion: float


class ContactDataPoint(BaseModel):
    date: str
    contacts: int


class CallAnswerItem(BaseModel):
    name: str
    value: float
    color: str


class PatientEngagementMeta(BaseModel):
    total_calls_all_period: int
    average_call_duration: float


class PatientEngagementResponse(BaseModel):
    contactsData: list[ContactDataPoint]
    callAnswerData: list[CallAnswerItem]
    metadata: PatientEngagementMeta
    error: int = 0


class CommunityGrowthPoint(BaseModel):
    date: str
    added: int


class DeptEngagementItem(BaseModel):
    department: str
    engagement: int


class FeedbackPatient(BaseModel):
    name: str
    date: str
    feedback: str


class FeedbackDistItem(BaseModel):
    name: str
    value: int
    patients: list[FeedbackPatient]


class PatientIntent(BaseModel):
    name: str
    value: int
    intent: str
    count: int
    patients: list[dict]


class CommunityEngagementMeta(BaseModel):
    community_members: int
    post_week: int
    new_members: int
    avg_engagement_per_post: float = 0.0


class CommunityEngagementMetrics(BaseModel):
    conversion_rate: float
    poll_participation_rate: float
    weekly_active_users: int


class CommunityEngagementResponse(BaseModel):
    communityGrowthData: list[CommunityGrowthPoint]
    departmentEngagementData: list[DeptEngagementItem]
    feedbackDistribution: list[FeedbackDistItem]
    patientIntents: list[PatientIntent]
    metadata: CommunityEngagementMeta
    metrics: CommunityEngagementMetrics


class EscalationIssue(BaseModel):
    patient_name: str
    issue: str
    mobile_no: str | None = None


class DeptEscalationItem(BaseModel):
    department: str
    count: int
    issues: list[EscalationIssue]


class ResolutionStatusItem(BaseModel):
    name: str
    value: float
    color: str


class RecentEscalation(BaseModel):
    id: Any
    patient: str
    issue: str
    status: str
    time: str


class EscalationMeta(BaseModel):
    total_escalations: int
    avg_resolution_time: float
    resolved_today: int


class EscalationEngagementResponse(BaseModel):
    departmentEscalationData: list[DeptEscalationItem]
    resolutionStatusData: list[ResolutionStatusItem]
    recentEscalations: list[RecentEscalation]
    metadata: EscalationMeta


class RecentActivityResponse(BaseModel):
    error: int = 0
    call_data: list[dict]
    escalation_data: list[dict]
    community_data: list[dict]


# Inbound KPI has 8 cards
class InboundKPIResponse(BaseModel):
    interactions: KPICard
    resolution: KPICard
    conversion: KPICard
    noshow: KPICard
    patients: KPICard
    ans_rate: KPICard
    escalation: KPICard
    revenue: KPICard


# Revisit analytics
class DeptRevisitItem(BaseModel):
    name: str
    value: int
    percentage: float
    color: str


class MonthlyRevisitPoint(BaseModel):
    month: str
    revisits: int


class TimeGapItem(BaseModel):
    gap: str
    count: int


class RevisitAnalyticsResponse(BaseModel):
    department_data: list[DeptRevisitItem]
    monthly_trend: list[MonthlyRevisitPoint]
    time_gap_distribution: list[TimeGapItem]
    average_revisit_gap: float | None
    repeat_visits_this_month: int
