const adminToken = window.sessionStorage.getItem("fettle_admin_token") || "";
let currentTable = null;
let currentRows = [];
let selectedRow = null;
let offset = 0;
const limit = 100;
const tableName = new URLSearchParams(window.location.search).get("name");

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

function primaryKey(row) {
  return Object.fromEntries(currentTable.primary_key.map((name) => [name, row[name]]));
}

function selectRow(row) {
  selectedRow = row;
  document.querySelector("#editor-title").textContent = "Edit row";
  document.querySelector("#row-json").value = JSON.stringify(row, null, 2);
  document.querySelector("#editor-help").textContent = `Primary key: ${JSON.stringify(primaryKey(row))}`;
}

function newRow() {
  selectedRow = null;
  const blank = Object.fromEntries(currentTable.columns.map((column) => [column.name, null]));
  document.querySelector("#editor-title").textContent = "New row";
  document.querySelector("#row-json").value = JSON.stringify(blank, null, 2);
  document.querySelector("#editor-help").textContent = "Remove generated/default columns before saving if the database should fill them.";
}

function renderRows() {
  const columns = currentTable.columns.map((column) => column.name);
  document.querySelector("#rows thead").innerHTML = `<tr>${columns
    .map((name) => `<th>${escapeHtml(name)}</th>`)
    .join("")}<th></th></tr>`;
  document.querySelector("#rows tbody").innerHTML = currentRows
    .map(
      (row, index) =>
        `<tr>${columns
          .map((name) => `<td>${escapeHtml(JSON.stringify(row[name] ?? ""))}</td>`)
          .join("")}<td><button type="button" data-row="${index}">Edit</button></td></tr>`,
    )
    .join("");
  document.querySelectorAll("[data-row]").forEach((button) => {
    button.addEventListener("click", () => selectRow(currentRows[Number(button.dataset.row)]));
  });
}

async function refresh() {
  if (!tableName) return;
  const data = await api(
    `/admin/api/db/tables/${encodeURIComponent(tableName)}?limit=${limit}&offset=${offset}`,
  );
  currentTable = data.table;
  currentRows = data.rows;
  document.querySelector("#table-title").textContent = currentTable.name;
  renderRows();
}

document.querySelector("#row-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const values = JSON.parse(document.querySelector("#row-json").value);
  if (selectedRow) {
    await api(`/admin/api/db/tables/${encodeURIComponent(tableName)}/rows`, {
      method: "PUT",
      body: JSON.stringify({ pk: primaryKey(selectedRow), values }),
    });
  } else {
    await api(`/admin/api/db/tables/${encodeURIComponent(tableName)}/rows`, {
      method: "POST",
      body: JSON.stringify({ values }),
    });
  }
  await refresh();
});

document.querySelector("#delete-row").addEventListener("click", async () => {
  if (!selectedRow) return;
  await api(`/admin/api/db/tables/${encodeURIComponent(tableName)}/rows`, {
    method: "DELETE",
    body: JSON.stringify({ pk: primaryKey(selectedRow) }),
  });
  selectedRow = null;
  document.querySelector("#row-json").value = "";
  await refresh();
});

document.querySelector("#new-row").addEventListener("click", newRow);
document.querySelector("#prev-page").addEventListener("click", () => {
  offset = Math.max(0, offset - limit);
  refresh();
});
document.querySelector("#next-page").addEventListener("click", () => {
  offset += limit;
  refresh();
});

refresh();
