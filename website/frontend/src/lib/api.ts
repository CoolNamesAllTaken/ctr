import "server-only";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    // Django Ninja's `detail`: a message string, or a list of validation errors.
    readonly detail: unknown,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Fetch from the Django API in server code (Server Components, Route Handlers,
 * Server Actions). Client components should call the same-origin `/api/...`
 * path instead, which next.config.ts proxies to Django.
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  // Django's employee endpoints need the passphrase. This is server code, so
  // it's never sent to the browser; src/lib/employee.ts gates the employees.
  const headers = new Headers(init?.headers);
  headers.set("X-Employee-Passphrase", process.env.EMPLOYEE_PASSPHRASE ?? "");
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      body?.detail,
      `API ${response.status} ${response.statusText}: ${path}`,
    );
  }
  // Deletes answer 204 No Content.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
