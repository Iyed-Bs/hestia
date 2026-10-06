// Minimal API client. Same origin only: the session lives in an HttpOnly
// cookie the page cannot read; state-changing calls add the CSRF token the
// gateway gave us at sign-in (kept in memory, never in storage).

let csrfToken = "";

export function setCsrf(token: string): void {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function messageOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      return first.msg ?? fallback;
    }
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const body: unknown = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") {
      window.dispatchEvent(new CustomEvent("hestia:signed-out"));
    }
    throw new ApiError(response.status, messageOf(body, response.statusText));
  }
  return body as T;
}

export const post = <T>(path: string, data: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: JSON.stringify(data) });
