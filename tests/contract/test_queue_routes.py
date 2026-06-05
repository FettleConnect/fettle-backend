from __future__ import annotations


def _auth_headers(client):
    doctor = client.get("/doctors").json()["doctors"][0]
    login = client.post("/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"})
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_public_intake_submission_and_queue_acceptance(client) -> None:
    headers = _auth_headers(client)
    intake_link = client.get("/intake-link", headers=headers).json()["intakeLink"]

    created = client.post(
        f"/public/intake/{intake_link['token']}",
        json={
            "patient": {
                "name": "Anita Sharma",
                "phoneNumber": "9876543210",
                "age": 32,
            }
        },
    )
    assert created.status_code == 201
    entry = created.json()["entry"]
    assert entry["status"] == "waiting"

    queue = client.get("/queue", headers=headers)
    assert queue.status_code == 200
    assert len(queue.json()["entries"]) == 1
    assert queue.json()["entries"][0]["patient"]["name"] == "Anita Sharma"

    accepted = client.post(f"/queue/{entry['id']}/accept", headers=headers)
    assert accepted.status_code == 201
    payload = accepted.json()
    assert payload["entry"]["status"] == "accepted"
    assert payload["entry"]["sessionId"]
    assert payload["session"]["patient"]["name"] == "Anita Sharma"
    assert payload["session"]["patient"]["age"] == 32


def test_queue_status_transitions_and_repeated_intake_deduping(client) -> None:
    headers = _auth_headers(client)
    intake_link = client.get("/intake-link", headers=headers).json()["intakeLink"]
    payload = {
        "patient": {
            "name": "Rahul Singh",
            "phoneNumber": "9999999991",
            "age": 41,
        }
    }

    first = client.post(f"/public/intake/{intake_link['token']}", json=payload)
    second = client.post(f"/public/intake/{intake_link['token']}", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["entry"]["id"] == first.json()["entry"]["id"]

    no_show = client.post(f"/queue/{first.json()['entry']['id']}/no-show", headers=headers)
    assert no_show.status_code == 200
    assert no_show.json()["entry"]["status"] == "no_show"

    queue = client.get("/queue", headers=headers)
    assert queue.status_code == 200
    assert queue.json()["entries"] == []


def test_public_intake_profile_lookup_and_cancel_conflict(client) -> None:
    headers = _auth_headers(client)
    session = client.post(
        "/sessions",
        json={"patientName": "Meera Nair", "patientPhoneNumber": "9999999992", "patientAge": 28},
        headers=headers,
    ).json()["session"]
    assert session["patient"]["name"] == "Meera Nair"

    intake_link = client.get("/intake-link", headers=headers).json()["intakeLink"]
    profile = client.get(
        f"/public/intake/{intake_link['token']}/patient-profile",
        params={"phone": "9999999992"},
    )
    assert profile.status_code == 200
    assert profile.json()["found"] is True
    assert profile.json()["profile"]["name"] == "Meera Nair"

    created = client.post(
        f"/public/intake/{intake_link['token']}",
        json={
            "patient": {
                "name": "Riya Shah",
                "phoneNumber": "9999999988",
                "age": 25,
            }
        },
    )
    queue_entry_id = created.json()["entry"]["id"]

    cancelled = client.post(f"/queue/{queue_entry_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["entry"]["status"] == "cancelled"

    repeated = client.post(f"/queue/{queue_entry_id}/no-show", headers=headers)
    assert repeated.status_code == 409
