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
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      body?.detail,
      `API ${response.status} ${response.statusText}: ${path}`,
    );
  }
  return response.json() as Promise<T>;
}
