// Thin fetch wrapper: same-origin session cookie, CSRF header on writes, errors carry the server message.
export function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRFToken"] = csrfToken();
  }
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const msg = (data && (data.detail || firstError(data))) || `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return data;
}

function firstError(data) {
  const v = Object.values(data)[0];
  return Array.isArray(v) ? v[0] : typeof v === "string" ? v : null;
}

export const inr = (n) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(n || 0));

export const num = (n) => new Intl.NumberFormat("en-IN").format(Number(n || 0));

// Head-level screens are open to the Head and to the Admin.
export const isHead = (u) => !!u && (u.role === "head" || u.role === "admin");
