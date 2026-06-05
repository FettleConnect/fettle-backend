from __future__ import annotations


def _auth_headers(client):
    doctor = client.get("/doctors").json()["doctors"][0]
    login = client.post("/auth/pin/login", json={"doctorId": doctor["id"], "pin": "123456"})
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_list_get_and_update_session(client) -> None:
    headers = _auth_headers(client)

    created = client.post(
        "/sessions",
        json={"patientName": "Asha", "patientPhoneNumber": "9999999999", "patientAge": 32},
        headers=headers,
    )
    assert created.status_code == 201
    session = created.json()["session"]

    listed = client.get("/sessions", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == session["id"] for item in listed.json()["sessions"])

    fetched = client.get(f"/sessions/{session['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["session"]["patient"]["name"] == "Asha"

    updated = client.patch(
        f"/sessions/{session['id']}/case-status",
        json={"caseStatus": "closed"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["session"]["caseStatus"] == "closed"


def test_patient_history_is_scoped_to_same_doctor_and_phone(client) -> None:
    headers = _auth_headers(client)
    first = client.post(
        "/sessions",
        json={"patientName": "Ravi", "patientPhoneNumber": "8888888888", "patientAge": 28},
        headers=headers,
    ).json()["session"]
    second = client.post(
        "/sessions",
        json={"patientName": "Ravi", "patientPhoneNumber": "8888888888", "patientAge": 29},
        headers=headers,
    ).json()["session"]

    history = client.get(f"/sessions/{second['id']}/patient-history", headers=headers)
    assert history.status_code == 200
    sessions = history.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == first["id"]
