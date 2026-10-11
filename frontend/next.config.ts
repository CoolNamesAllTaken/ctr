import type { NextConfig } from "next";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Django URLs end in a slash; don't let Next redirect it away before proxying.
  skipTrailingSlashRedirect: true,
  // Proxy /api to Django so the browser only ever talks to one origin (no CORS,
  // and session/CSRF cookies just work).
  async rewrites() {
    return [
      { source: "/api/:path*/", destination: `${API_URL}/api/:path*/` },
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
