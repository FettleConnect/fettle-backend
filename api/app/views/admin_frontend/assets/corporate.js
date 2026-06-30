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

async function refresh() {
  const organizations = await api("/admin/api/organizations");
  const corporates = organizations.filter((org) => org.type === "corporate");
  document.querySelector("#corporates tbody").innerHTML = corporates
    .map(
      (org) =>
        `<tr><td>${escapeHtml(org.name)}</td><td>${escapeHtml(org.subdomain)}</td><td>${org.is_active ? "Active" : "Inactive"}</td><td>${escapeHtml(org.id)}</td></tr>`,
    )
    .join("");
}

document.querySelector("#corporate-form").addEventListener("submit", async (e) => {
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
