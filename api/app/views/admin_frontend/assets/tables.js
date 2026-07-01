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
  const tables = await api("/admin/api/db/tables");
  document.querySelector("#tables tbody").innerHTML = tables
    .map(
      (table) =>
        `<tr><td>${escapeHtml(table.name)}</td><td>${escapeHtml(table.primary_key.join(", "))}</td><td>${table.columns.length}</td><td><a href="/admin/table?name=${encodeURIComponent(table.name)}">Open</a></td></tr>`,
    )
    .join("");
}

refresh();
