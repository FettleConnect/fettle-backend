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

function renderHospitals(hospitals) {
  document.querySelector("#hospitals tbody").innerHTML = hospitals
    .map(
      (hospital) =>
        `<tr><td>${escapeHtml(hospital.name)}<div class="muted">${escapeHtml(hospital.id)}</div></td><td>${escapeHtml(hospital.subdomain)}</td><td>${formatNumber(hospital.patient_count)}</td><td>${formatNumber(hospital.doctor_count)}</td><td>${formatNumber(hospital.user_count)}</td><td>${formatNumber(hospital.inbound_call_count)}</td><td>${formatNumber(hospital.outbound_call_count)}</td><td>${hospital.is_active ? "Active" : "Inactive"}</td><td><a href="/admin/hospital?id=${encodeURIComponent(hospital.id)}">Manage</a></td></tr>`,
    )
    .join("");
}

async function refresh() {
  renderHospitals(await api("/admin/api/hospitals/summary"));
}

document.querySelector("#hospital-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  await api("/admin/api/organizations", {
    method: "POST",
    body: JSON.stringify(body),
  });
  e.target.reset();
  refresh();
});

refresh();
