const adminToken = window.sessionStorage.getItem("fettle_admin_token") || "";
const hospitalId = new URLSearchParams(window.location.search).get("id");
let activeTable = null;
let activeTableMeta = null;
let activeRow = null;

const hospitalTables = [
  { name: "patients", columns: ["patient_name", "mobile_no", "department", "visit_date"] },
  { name: "users", columns: ["name", "username", "role", "is_active"] },
  { name: "inbound_calls", columns: ["from_phone_number", "to_phone_number", "status", "created_at"] },
  { name: "outbound_calls", columns: ["status", "calling_process", "started_at", "created_at"] },
  { name: "call_feedback", columns: ["call_status", "call_outcome", "called_by", "created_at"] },
];

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

function primaryKey(table, row) {
  return Object.fromEntries(table.primary_key.map((name) => [name, row[name]]));
}

function renderMetrics(hospital) {
  const items = [
    ["Patients", hospital.patient_count],
    ["Doctors", hospital.doctor_count],
    ["Users", hospital.user_count],
    ["Inbound calls", hospital.inbound_call_count],
    ["Outbound calls", hospital.outbound_call_count],
  ];
  document.querySelector("#hospital-metrics").innerHTML = items
    .map(
      ([label, value]) =>
        `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${formatNumber(value)}</strong></article>`,
    )
    .join("");
}

function renderUsers(users) {
  document.querySelector("#hospital-users tbody").innerHTML = users
    .map(
      (user) =>
        `<tr><td>${escapeHtml(user.name)}</td><td>${escapeHtml(user.username)}</td><td>${escapeHtml(user.role)}</td><td>${user.is_active ? "Active" : "Inactive"}</td><td><button class="secondary" type="button" data-user="${escapeHtml(user.id)}">Reset password</button></td></tr>`,
    )
    .join("");
  document.querySelector("#password-user").innerHTML = users
    .map((user) => `<option value="${escapeHtml(user.id)}">${escapeHtml(user.name)} - ${escapeHtml(user.username)}</option>`)
    .join("");
  document.querySelectorAll("[data-user]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#password-user").value = button.dataset.user;
      document.querySelector("#password-form input[name='password']").focus();
    });
  });
}

function scopedRows(rows) {
  return rows.filter((row) => row.organization_id === hospitalId).slice(0, 10);
}

function renderTablePreview(config, payload) {
  const rows = scopedRows(payload.rows);
  const columns = config.columns.filter((name) =>
    payload.table.columns.some((column) => column.name === name),
  );
  const body = rows.length
    ? rows
        .map(
          (row, index) =>
            `<tr>${columns
              .map((name) => `<td>${escapeHtml(row[name])}</td>`)
              .join("")}<td><button class="secondary" type="button" data-table="${escapeHtml(config.name)}" data-index="${index}">Edit</button></td></tr>`,
        )
        .join("")
    : `<tr><td colspan="${columns.length + 1}" class="muted">No rows</td></tr>`;
  return `
    <article class="subtable" data-subtable="${escapeHtml(config.name)}">
      <div class="section-heading">
        <h3>${escapeHtml(config.name)}</h3>
        <a href="/admin/table?name=${encodeURIComponent(config.name)}">Open full table</a>
      </div>
      <div class="table-scroll compact-scroll">
        <table>
          <thead><tr>${columns.map((name) => `<th>${escapeHtml(name)}</th>`).join("")}<th></th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </article>`;
}

async function renderHospitalTables() {
  const payloads = await Promise.all(
    hospitalTables.map((table) =>
      api(`/admin/api/db/tables/${encodeURIComponent(table.name)}?limit=500`),
    ),
  );
  const stack = document.querySelector("#hospital-table-stack");
  stack.innerHTML = payloads
    .map((payload, index) => renderTablePreview(hospitalTables[index], payload))
    .join("");
  stack.querySelectorAll("[data-table]").forEach((button) => {
    button.addEventListener("click", () => {
      const payload = payloads.find((item) => item.table.name === button.dataset.table);
      const rows = scopedRows(payload.rows);
      openDialog(payload.table, rows[Number(button.dataset.index)]);
    });
  });
}

function openDialog(table, row) {
  activeTable = table.name;
  activeTableMeta = table;
  activeRow = row;
  document.querySelector("#dialog-title").textContent = `Edit ${table.name}`;
  document.querySelector("#dialog-json").value = JSON.stringify(row, null, 2);
  document.querySelector("#row-dialog").showModal();
}

async function saveDialogRow() {
  const values = JSON.parse(document.querySelector("#dialog-json").value);
  await api(`/admin/api/db/tables/${encodeURIComponent(activeTable)}/rows`, {
    method: "PUT",
    body: JSON.stringify({ pk: primaryKey(activeTableMeta, activeRow), values }),
  });
  document.querySelector("#row-dialog").close();
  await renderHospitalTables();
}

async function deleteDialogRow() {
  await api(`/admin/api/db/tables/${encodeURIComponent(activeTable)}/rows`, {
    method: "DELETE",
    body: JSON.stringify({ pk: primaryKey(activeTableMeta, activeRow) }),
  });
  document.querySelector("#row-dialog").close();
  await renderHospitalTables();
}

async function refresh() {
  if (!hospitalId) return;
  const detail = await api(`/admin/api/hospitals/${encodeURIComponent(hospitalId)}`);
  document.querySelector("#hospital-name").textContent = detail.hospital.name;
  renderMetrics(detail.hospital);
  renderUsers(detail.users);
  await renderHospitalTables();
}

document.querySelector("#hospital-user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  await api(`/admin/api/hospitals/${encodeURIComponent(hospitalId)}/users`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  e.target.reset();
  refresh();
});

document.querySelector("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  await api(
    `/admin/api/hospitals/${encodeURIComponent(hospitalId)}/users/${encodeURIComponent(body.user_id)}/password`,
    {
      method: "PUT",
      body: JSON.stringify({ password: body.password }),
    },
  );
  e.target.reset();
  refresh();
});

document.querySelector("#dialog-close").addEventListener("click", () => {
  document.querySelector("#row-dialog").close();
});
document.querySelector("#dialog-save").addEventListener("click", saveDialogRow);
document.querySelector("#dialog-delete").addEventListener("click", deleteDialogRow);

refresh();
