from __future__ import annotations

import time


def _auth_headers(client):
    doctor = client.get("/doctors").json()["doctors"][0]
    login = client.post(
        "/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"}
    )
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_audio_persists_metadata_and_returns_session(client) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={
            "patientName": "Maya",
            "patientPhoneNumber": "7777777777",
            "patientAge": 40,
        },
        headers=headers,
    ).json()["session"]

    response = client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "12.5",
            "X-Audio-File-Name": "session-audio.webm",
        },
        content=b"\x1a\x45\xdf\xa3audio-bytes",
    )

    assert response.status_code == 201
    body = response.json()["session"]
    assert body["audioArtifact"]["fileName"] == "session-audio.webm"
    assert body["audioArtifact"]["mimeType"] == "audio/webm"


def test_submit_transcription_requires_audio_and_then_returns_processing(
    client,
) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={
            "patientName": "Vikram",
            "patientPhoneNumber": "6666666666",
            "patientAge": 34,
        },
        headers=headers,
    ).json()["session"]

    no_audio = client.post(f"/sessions/{session['id']}/transcription", headers=headers)
    assert no_audio.status_code == 409

    client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "8",
            "X-Audio-File-Name": "sample.webm",
        },
        content=b"\x1a\x45\xdf\xa3bytes",
    )
    response = client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": "12345678-local"},
    )
    assert response.status_code == 202
    assert response.json()["session"]["transcription"]["status"] == "processing"

    completed = None
    for _ in range(10):
        time.sleep(0.05)
        completed = client.get(f"/sessions/{session['id']}", headers=headers)
        if completed.json()["session"]["transcription"]["status"] == "completed":
            break
    assert completed is not None
    transcription = completed.json()["session"]["transcription"]
    assert transcription["status"] == "completed"
    assert transcription["providerName"] == "mock-gemini"
    assert "persistent cough" in transcription["summary"]
    assert len(transcription["transcriptSegments"]) == 3
    assert transcription["transcriptSegments"][0]["speaker"] == "doctor"
    assert transcription["transcriptSegments"][1]["speaker"] == "patient"
    assert transcription["draftDocument"]["medications"]


def test_update_draft_document_persists_document(client) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={
            "patientName": "Leela",
            "patientPhoneNumber": "5555555555",
            "patientAge": 29,
        },
        headers=headers,
    ).json()["session"]

    response = client.put(
        f"/sessions/{session['id']}/draft-document",
        headers=headers,
        json={
            "title": "Consultation Draft",
            "clinicalSummary": "Stable progress",
            "medications": [],
            "testsPrescribed": [],
            "nextSteps": ["Follow up in one week"],
            "cautions": [],
        },
    )
    assert response.status_code == 200
    draft = response.json()["session"]["transcription"]["draftDocument"]
    assert draft["title"] == "Consultation Draft"
    assert draft["clinicalSummary"] == "Stable progress"


def test_generate_pdf_and_download_artifacts(client) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={
            "patientName": "Kiran",
            "patientPhoneNumber": "4444444444",
            "patientAge": 31,
        },
        headers=headers,
    ).json()["session"]

    client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "6",
            "X-Audio-File-Name": "recording.webm",
        },
        content=b"\x1a\x45\xdf\xa3recording",
    )
    client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": "artifact-test-key"},
    )
    for _ in range(10):
        time.sleep(0.05)
        current = client.get(f"/sessions/{session['id']}", headers=headers).json()[
            "session"
        ]
        if current["transcription"]["status"] == "completed":
            break

    generated = client.post(f"/sessions/{session['id']}/pdf", headers=headers)
    assert generated.status_code == 200
    assert (
        generated.json()["session"]["documentArtifact"]["mimeType"] == "application/pdf"
    )

    transcript = client.get(
        f"/sessions/{session['id']}/artifacts/transcript", headers=headers
    )
    assert transcript.status_code == 200
    assert transcript.headers["content-type"].startswith("application/pdf")

    audio = client.get(f"/sessions/{session['id']}/artifacts/audio", headers=headers)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")

    pdf = client.get(f"/sessions/{session['id']}/artifacts/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
