const adminToken = window.sessionStorage.getItem("fettle_admin_token") || "";

if (!adminToken) {
  window.location.href = "/admin/login";
}

const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${adminToken}`,
});

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const formatNumber = (value) => Number(value || 0).toLocaleString();

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { ...authHeaders(), ...(opts.headers || {}) },
  });
  if (res.status === 401 || res.status === 403) {
    window.sessionStorage.removeItem("fettle_admin_token");
    window.location.href = "/admin/login";
  }
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : res.json();
}

function renderMetrics(metrics) {
  const items = [
    ["Organizations", metrics.organizations],
    ["Hospitals", metrics.hospitals],
    ["Patients", metrics.patients],
    ["Doctors", metrics.doctors],
    ["Users", metrics.users],
    ["Inbound calls", metrics.inbound_calls],
    ["Outbound calls", metrics.outbound_calls],
    ["Corporate", metrics.corporate_clients],
  ];
  document.querySelector("#metrics").innerHTML = items
    .map(
      ([label, value]) =>
        `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${formatNumber(value)}</strong></article>`,
    )
    .join("");
}

function renderHospitals(hospitals) {
  document.querySelector("#hospital-summary tbody").innerHTML = hospitals
    .slice(0, 8)
    .map(
      (hospital) =>
        `<tr><td>${escapeHtml(hospital.name)}<div class="muted">${escapeHtml(hospital.id)}</div></td><td>${formatNumber(hospital.patient_count)}</td><td>${formatNumber(hospital.doctor_count)}</td><td>${formatNumber(hospital.user_count)}</td><td>${formatNumber(hospital.inbound_call_count + hospital.outbound_call_count)}</td><td>${hospital.is_active ? "Active" : "Inactive"}</td><td><a href="/admin/hospital?id=${encodeURIComponent(hospital.id)}">Manage</a></td></tr>`,
    )
    .join("");
}

async function refresh() {
  const [metrics, hospitals] = await Promise.all([
    api("/admin/api/metrics"),
    api("/admin/api/hospitals/summary"),
  ]);
  renderMetrics(metrics);
  renderHospitals(hospitals);
}

refresh();
