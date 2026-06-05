from __future__ import annotations

import time


def _auth_headers(client):
    doctor = client.get("/doctors").json()["doctors"][0]
    login = client.post("/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"})
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_completed_session(client):
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={"patientName": "Nisha", "patientPhoneNumber": "3333333333", "patientAge": 36},
        headers=headers,
    ).json()["session"]
    client.post(
        f"/sessions/{session['id']}/audio",
        headers={
            **headers,
            "Content-Type": "audio/webm",
            "X-Audio-Duration-Seconds": "10",
            "X-Audio-File-Name": "report-context.webm",
        },
        content=b"\x1a\x45\xdf\xa3report-context-audio",
    )
    client.post(
        f"/sessions/{session['id']}/transcription",
        headers={**headers, "X-Idempotency-Key": "report-context-transcription"},
    )
    for _ in range(10):
        time.sleep(0.05)
        current = client.get(f"/sessions/{session['id']}", headers=headers).json()["session"]
        if current["transcription"]["status"] == "completed":
            return headers, current
    raise AssertionError("transcription did not complete")


def test_upload_and_list_report_context_files(client) -> None:
    headers, session = _create_completed_session(client)

    uploaded = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "application/pdf",
            "X-Report-File-Name": "lab-report.pdf",
        },
        content=b"%PDF-1.4 report bytes",
    )
    assert uploaded.status_code == 201
    upload = uploaded.json()["upload"]
    assert upload["fileName"] == "lab-report.pdf"
    assert upload["mimeType"] == "application/pdf"

    listed = client.get(f"/sessions/{session['id']}/report-context/uploads", headers=headers)
    assert listed.status_code == 200
    uploads = listed.json()["uploads"]
    assert len(uploads) == 1
    assert uploads[0]["id"] == upload["id"]

    content = client.get(
        f"/sessions/{session['id']}/report-context/uploads/{upload['id']}/content",
        headers=headers,
    )
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("application/pdf")
    assert content.headers["content-disposition"].startswith("inline;")
    assert content.content == b"%PDF-1.4 report bytes"


def test_extract_report_context_returns_ready_session(client) -> None:
    headers, session = _create_completed_session(client)
    upload = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "image/png",
            "X-Report-File-Name": "scan.png",
        },
        content=b"\x89PNG\r\n\x1a\nimage-bytes",
    ).json()["upload"]

    extracted = client.post(
        f"/sessions/{session['id']}/report-context/extract",
        headers=headers,
        json={"uploadIds": [upload["id"]]},
    )
    assert extracted.status_code == 200
    report_context = extracted.json()["session"]["reportContext"]
    assert report_context["status"] == "ready"
    assert report_context["extractedUploadIds"] == [upload["id"]]
    assert len(report_context["chunks"]) == 1
    assert "Imported scanned report image 'scan.png'" in report_context["chunks"][0]["text"]


def test_extract_report_context_async_job_completes(client) -> None:
    headers, session = _create_completed_session(client)
    upload = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "image/webp",
            "X-Report-File-Name": "scan.webp",
        },
        content=b"RIFF....WEBPVP8 ",
    ).json()["upload"]

    created = client.post(
        f"/sessions/{session['id']}/report-context/extract-async",
        headers=headers,
        json={"uploadIds": [upload["id"]]},
    )
    assert created.status_code == 202
    job = created.json()["job"]
    assert job["status"] == "processing"

    polled = None
    for _ in range(10):
        time.sleep(0.05)
        polled = client.get(
            f"/sessions/{session['id']}/report-context/jobs/{job['id']}",
            headers=headers,
        )
        if polled.json()["job"]["status"] == "completed":
            break

    assert polled is not None
    assert polled.status_code == 200
    assert polled.json()["job"]["status"] == "completed"

    fetched = client.get(f"/sessions/{session['id']}", headers=headers)
    report_context = fetched.json()["session"]["reportContext"]
    assert report_context["status"] == "ready"
    assert report_context["extractedUploadIds"] == [upload["id"]]


def test_report_context_upload_delete_and_job_controls(client) -> None:
    headers, session = _create_completed_session(client)
    upload = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "application/pdf",
            "X-Report-File-Name": "delete-me.pdf",
        },
        content=b"%PDF-1.4 delete me",
    ).json()["upload"]

    deleted = client.delete(
        f"/sessions/{session['id']}/report-context/uploads/{upload['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204

    listed = client.get(f"/sessions/{session['id']}/report-context/uploads", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["uploads"] == []

    async_upload = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "image/png",
            "X-Report-File-Name": "job.png",
        },
        content=b"\x89PNG\r\n\x1a\njob-bytes",
    ).json()["upload"]

    created = client.post(
        f"/sessions/{session['id']}/report-context/extract-async",
        headers=headers,
        json={"uploadIds": [async_upload["id"]]},
    )
    assert created.status_code == 202
    job = created.json()["job"]

    streamed = client.get(
        f"/sessions/{session['id']}/report-context/jobs/{job['id']}/stream",
        headers=headers,
    )
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert f'"id":"{job["id"]}"' in streamed.text

    cancelled = client.post(
        f"/sessions/{session['id']}/report-context/jobs/{job['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"


def test_report_context_suggestions_and_apply_update_draft_document(client) -> None:
    headers, session = _create_completed_session(client)
    upload = client.post(
        f"/sessions/{session['id']}/report-context/uploads",
        headers={
            **headers,
            "Content-Type": "image/webp",
            "X-Report-File-Name": "suggest.webp",
        },
        content=b"RIFF....WEBPVP8 suggest",
    ).json()["upload"]

    extracted = client.post(
        f"/sessions/{session['id']}/report-context/extract",
        headers=headers,
        json={"uploadIds": [upload["id"]]},
    )
    assert extracted.status_code == 200
    chunk_id = extracted.json()["session"]["reportContext"]["chunks"][0]["id"]

    suggested = client.post(
        f"/sessions/{session['id']}/report-context/suggestions",
        headers=headers,
        json={"chunkIds": [chunk_id], "customChunks": ["Extra note from doctor."]},
    )
    assert suggested.status_code == 200
    suggestion = suggested.json()
    assert suggestion["chunkIds"] == [chunk_id]
    assert "Imported scanned report image 'suggest.webp'" in suggestion["draftDocument"]["clinicalSummary"]
    assert "Extra note from doctor." in suggestion["draftDocument"]["clinicalSummary"]

    applied = client.post(
        f"/sessions/{session['id']}/report-context/apply",
        headers=headers,
        json={
            "chunkIds": [chunk_id],
            "customChunks": ["Applied custom context."],
            "draftDocument": {
                "title": "Consultation Draft",
                "clinicalSummary": "Applied summary",
                "nextSteps": ["Order repeat labs"],
            },
        },
    )
    assert applied.status_code == 200
    draft = applied.json()["session"]["transcription"]["draftDocument"]
    assert draft["title"] == "Consultation Draft"
    assert draft["clinicalSummary"] == "Applied summary"
    assert draft["nextSteps"] == ["Order repeat labs"]
