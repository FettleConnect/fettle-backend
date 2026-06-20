from __future__ import annotations


class TestModelsImportable:
    def test_import_all_models(self) -> None:
        import api.app.models as models

        expected_exports = [
            "ActivityBooking",
            "BodyComposition",
            "Campaign",
            "Comment",
            "CommentLike",
            "CorporateConfig",
            "DermatologyPatient",
            "DermatologyThread",
            "Escalation",
            "GlobalConfig",
            "HospitalConfig",
            "HospitalUploadLog",
            "InboundCall",
            "InboundCommunityEngagement",
            "InboundEscalation",
            "InboundFeedback",
            "MediVoiceSession",
            "MediVoiceTranscription",
            "Notification",
            "Organization",
            "OutboundCall",
            "OutdoorActivity",
            "Package",
            "Patient",
            "Post",
            "PostLike",
            "SessionBooking",
            "SupportTicket",
            "Therapist",
            "TherapistBooking",
            "User",
            "UserSavedPost",
            "WellnessSession",
            "WellnessVideo",
        ]

        for name in expected_exports:
            assert hasattr(models, name), name

    def test_organization_table_name(self) -> None:
        from api.app.models.organization import Organization

        assert Organization.__tablename__ == "organizations"

    def test_hospital_config_table_name(self) -> None:
        from api.app.models.organization import HospitalConfig

        assert HospitalConfig.__tablename__ == "hospital_configs"

    def test_corporate_config_table_name(self) -> None:
        from api.app.models.organization import CorporateConfig

        assert CorporateConfig.__tablename__ == "corporate_configs"

    def test_user_table_name(self) -> None:
        from api.app.models.auth import User

        assert User.__tablename__ == "users"

    def test_patient_table_name(self) -> None:
        from api.app.models.hospital.patient import Patient

        assert Patient.__tablename__ == "patients"


class TestTenantMixin:
    """Every domain table must carry organization_id for multi-tenant isolation."""

    def _has_column(self, model_cls, col_name: str) -> bool:
        return col_name in model_cls.__table__.columns

    def test_patient_has_organization_id(self) -> None:
        from api.app.models.hospital.patient import Patient

        assert self._has_column(Patient, "organization_id")

    def test_user_has_organization_id(self) -> None:
        from api.app.models.auth import User

        assert self._has_column(User, "organization_id")

    def test_outbound_call_has_organization_id(self) -> None:
        from api.app.models.hospital.call import OutboundCall

        assert self._has_column(OutboundCall, "organization_id")

    def test_inbound_call_has_organization_id(self) -> None:
        from api.app.models.hospital.call import InboundCall

        assert self._has_column(InboundCall, "organization_id")

    def test_campaign_has_organization_id(self) -> None:
        from api.app.models.hospital.campaign import Campaign

        assert self._has_column(Campaign, "organization_id")

    def test_medivoice_session_has_organization_id(self) -> None:
        from api.app.models.hospital.medivoice import MediVoiceSession

        assert self._has_column(MediVoiceSession, "organization_id")

    def test_dermatology_patient_has_organization_id(self) -> None:
        from api.app.models.hospital.dermatology import DermatologyPatient

        assert self._has_column(DermatologyPatient, "organization_id")

    def test_wellness_video_has_organization_id(self) -> None:
        from api.app.models.corporate.wellness import WellnessVideo

        assert self._has_column(WellnessVideo, "organization_id")

    def test_therapist_has_organization_id(self) -> None:
        from api.app.models.corporate.wellness import Therapist

        assert self._has_column(Therapist, "organization_id")

    def test_post_has_organization_id(self) -> None:
        from api.app.models.corporate.social import Post

        assert self._has_column(Post, "organization_id")

    def test_notification_has_organization_id(self) -> None:
        from api.app.models.corporate.social import Notification

        assert self._has_column(Notification, "organization_id")


class TestOrganizationModel:
    def test_organization_has_slug(self) -> None:
        from api.app.models.organization import Organization

        assert "slug" in Organization.__table__.columns

    def test_organization_has_type(self) -> None:
        from api.app.models.organization import Organization

        assert "type" in Organization.__table__.columns

    def test_organization_has_is_active(self) -> None:
        from api.app.models.organization import Organization

        assert "is_active" in Organization.__table__.columns


class TestHospitalConfigModel:
    def test_has_vobiz_did(self) -> None:
        from api.app.models.organization import HospitalConfig

        assert "vobiz_did" in HospitalConfig.__table__.columns

    def test_has_whatsapp_channel_id(self) -> None:
        from api.app.models.organization import HospitalConfig

        assert "whatsapp_channel_id" in HospitalConfig.__table__.columns

    def test_has_agent_prompt_prefix(self) -> None:
        from api.app.models.organization import HospitalConfig

        assert "agent_prompt_prefix" in HospitalConfig.__table__.columns

    def test_has_branding_json(self) -> None:
        from api.app.models.organization import HospitalConfig

        assert "branding_json" in HospitalConfig.__table__.columns


class TestUserModel:
    def test_has_permission_flags(self) -> None:
        from api.app.models.auth import User

        cols = User.__table__.columns
        assert "perm_patient_engagement" in cols
        assert "perm_community_engagement" in cols
        assert "perm_revisit_engagement" in cols
        assert "perm_escalation_engagement" in cols
        assert "perm_calllog_engagement" in cols
        assert "perm_upload_engagement" in cols
        assert "perm_pdf_engagement" in cols

    def test_has_doctor_fields(self) -> None:
        from api.app.models.auth import User

        cols = User.__table__.columns
        assert "department" in cols
        assert "availability_json" in cols
        assert "consultation_fee" in cols

    def test_has_corporate_fields(self) -> None:
        from api.app.models.auth import User

        cols = User.__table__.columns
        assert "company" in cols
        assert "profile_picture_url" in cols

    def test_unique_constraint_on_org_email(self) -> None:
        from api.app.models.auth import User

        constraint_names = {c.name for c in User.__table__.constraints}
        assert "uq_users_org_email" in constraint_names


class TestPatientModel:
    def test_has_visit_date(self) -> None:
        from api.app.models.hospital.patient import Patient

        assert "visit_date" in Patient.__table__.columns

    def test_unique_constraint_on_org_mobile_dept_date(self) -> None:
        from api.app.models.hospital.patient import Patient

        constraint_names = {c.name for c in Patient.__table__.constraints}
        assert "uq_patient_org_mobile_dept_date" in constraint_names


class TestCallModels:
    def test_outbound_call_has_campaign_fk(self) -> None:
        from api.app.models.hospital.call import OutboundCall

        assert "campaign_id" in OutboundCall.__table__.columns

    def test_inbound_call_has_phone_fields(self) -> None:
        from api.app.models.hospital.call import InboundCall

        cols = InboundCall.__table__.columns
        assert "from_phone_number" in cols
        assert "to_phone_number" in cols

    def test_inbound_feedback_has_inbound_call_fk(self) -> None:
        from api.app.models.hospital.call import InboundFeedback

        assert "inbound_call_id" in InboundFeedback.__table__.columns


class TestCorporateModels:
    def test_session_requests_table_exists(self) -> None:
        from api.app.models.corporate.wellness import session_requests

        assert session_requests.name == "session_requests"

    def test_activity_requests_table_exists(self) -> None:
        from api.app.models.corporate.wellness import activity_requests

        assert activity_requests.name == "activity_requests"

    def test_post_has_is_award(self) -> None:
        from api.app.models.corporate.social import Post

        assert "is_award" in Post.__table__.columns

    def test_comment_has_parent_id_for_threading(self) -> None:
        from api.app.models.corporate.social import Comment

        assert "parent_id" in Comment.__table__.columns

    def test_post_like_unique_constraint(self) -> None:
        from api.app.models.corporate.social import PostLike

        constraint_names = {c.name for c in PostLike.__table__.constraints}
        assert "uq_post_like_post_user" in constraint_names

    def test_body_composition_has_bmi(self) -> None:
        from api.app.models.corporate.wellness import BodyComposition

        assert "bmi" in BodyComposition.__table__.columns


class TestSharedModels:
    def test_global_config_has_key_value(self) -> None:
        from api.app.models.shared import GlobalConfig

        cols = GlobalConfig.__table__.columns
        assert "key" in cols
        assert "value" in cols
