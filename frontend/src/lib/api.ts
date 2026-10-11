import "server-only";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

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
    throw new Error(`API ${response.status} ${response.statusText}: ${path}`);
  }
  return response.json() as Promise<T>;
}
