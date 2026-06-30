document.querySelector("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = document.querySelector("#login-error");
  error.textContent = "";
  const body = Object.fromEntries(new FormData(e.target));
  const res = await fetch("/admin/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    error.textContent = "Invalid admin credentials";
    return;
  }
  const payload = await res.json();
  window.sessionStorage.setItem("fettle_admin_token", payload.token);
  window.location.href = "/admin";
});
