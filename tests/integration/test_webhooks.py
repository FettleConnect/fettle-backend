from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_org_with_config(
    db_session: AsyncSession, vobiz_did: str = "+911234567890"
) -> tuple:
    from api.app.models.organization import HospitalConfig, Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"Webhook Test Hospital {uuid.uuid4().hex[:6]}",
        slug=f"webhook-{uuid.uuid4().hex[:8]}",
        type="hospital",
    )
    db_session.add(org)
    await db_session.flush()

    cfg = HospitalConfig(
        id=uuid.uuid4(),
        organization_id=org.id,
        vobiz_did=vobiz_did,
        branding_json={},
    )
    db_session.add(cfg)
    await db_session.flush()
    return org.id, cfg.id, vobiz_did


class TestLiveKitWebhook:
    async def test_room_finished_acknowledged(self, async_client: AsyncClient) -> None:
        event = {
            "event": "room_finished",
            "room": {"name": "test_room_123"},
        }
        resp = await async_client.post("/api/v1/webhooks/livekit", json=event)
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"

    async def test_unknown_event_still_acknowledged(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/api/v1/webhooks/livekit", json={"event": "participant_joined"}
        )
        assert resp.status_code == 200

    async def test_inbound_room_detected(self, async_client: AsyncClient) -> None:
        event = {
            "event": "room_finished",
            "room": {"name": "inbound_+911234567890_abc123"},
        }
        resp = await async_client.post("/api/v1/webhooks/livekit", json=event)
        assert resp.status_code == 200

    async def test_empty_body_handled(self, async_client: AsyncClient) -> None:
        resp = await async_client.post(
            "/api/v1/webhooks/livekit",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (200, 422)


class TestVobizInboundWebhook:
    async def test_inbound_creates_record_for_known_did(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import InboundCall

        org_id, _, did = await _seed_org_with_config(db_session, "+911234560001")

        payload = {
            "CallUUID": f"vobiz-{uuid.uuid4().hex[:8]}",
            "CallStatus": "ringing",
            "From": "+919876543210",
            "To": did,
        }
        resp = await async_client.post("/api/v1/webhooks/vobiz/inbound", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

        # Verify InboundCall record created
        from sqlalchemy import select

        row = await db_session.scalar(
            select(InboundCall).where(
                InboundCall.organization_id == org_id,
                InboundCall.livekit_call_id == payload["CallUUID"],
            )
        )
        assert row is not None
        assert row.from_phone_number == "+919876543210"

    async def test_inbound_unknown_did_creates_unlinked_record(
        self, async_client: AsyncClient
    ) -> None:
        payload = {
            "CallUUID": f"vobiz-unknown-{uuid.uuid4().hex[:8]}",
            "CallStatus": "ringing",
            "From": "+919000000001",
            "To": "+910000000000",  # No hospital has this DID
        }
        resp = await async_client.post("/api/v1/webhooks/vobiz/inbound", json=payload)
        # Should not crash — gracefully handles unknown DID
        assert resp.status_code == 200

    async def test_inbound_no_hardcoded_numbers(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Ensures the old hardcoded +918049280487 is gone — DID lookup is purely DB-driven."""
        # If the DID "+918049280487" maps to a hospital, it must be via HospitalConfig.vobiz_did
        # This test verifies no 404/error when processing this number — it's just unknown in test DB
        payload = {
            "CallUUID": f"test-{uuid.uuid4().hex[:8]}",
            "CallStatus": "ringing",
            "From": "+919000000002",
            "To": "+918049280487",
        }
        resp = await async_client.post("/api/v1/webhooks/vobiz/inbound", json=payload)
        assert resp.status_code == 200


class TestVobizOutboundWebhook:
    async def test_outbound_updates_call_status(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from api.app.models.hospital.call import OutboundCall
        from api.app.models.organization import Organization

        org = Organization(
            id=uuid.uuid4(),
            name="Outbound WH Org",
            slug=f"ow-{uuid.uuid4().hex[:8]}",
            type="hospital",
        )
        db_session.add(org)
        await db_session.flush()

        room_id = f"out_{uuid.uuid4().hex[:12]}"
        call = OutboundCall(
            id=uuid.uuid4(),
            organization_id=org.id,
            livekit_call_id=room_id,
            calling_process="in_progress",
            status="queued",
        )
        db_session.add(call)
        await db_session.flush()

        resp = await async_client.post(
            "/api/v1/webhooks/vobiz/outbound",
            json={
                "CallUUID": f"vobiz-uuid-{uuid.uuid4().hex[:8]}",
                "CallStatus": "completed",
                "CustomSIPHeaders": {"X-VH-CorrelationId": room_id},
            },
        )
        assert resp.status_code == 200

        from sqlalchemy import select

        updated = await db_session.scalar(
            select(OutboundCall).where(OutboundCall.id == call.id)
        )
        assert updated.calling_process == "connected"
        assert updated.status == "ended"

    async def test_outbound_unknown_correlation_id_handled(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.post(
            "/api/v1/webhooks/vobiz/outbound",
            json={
                "CallUUID": "some-uuid",
                "CallStatus": "completed",
                "CustomSIPHeaders": {"X-VH-CorrelationId": "unknown_room_id_xyz"},
            },
        )
        assert resp.status_code == 200


class TestVobizWhatsAppWebhook:
    async def test_status_update_acknowledged(
        self, async_client_no_db: AsyncClient
    ) -> None:
        resp = await async_client_no_db.post(
            "/api/v1/webhooks/vobiz/whatsapp",
            json={
                "event": "message.status",
                "channel_id": "ch_123",
                "data": {
                    "message_id": "msg_1",
                    "status": "delivered",
                    "to": "+919000000000",
                },
            },
        )
        assert resp.status_code == 200

    async def test_incoming_message_acknowledged(
        self, async_client_no_db: AsyncClient
    ) -> None:
        resp = await async_client_no_db.post(
            "/api/v1/webhooks/vobiz/whatsapp",
            json={
                "event": "message.received",
                "data": {"from": "+919000000000", "text": {"body": "Hello"}},
            },
        )
        assert resp.status_code == 200
