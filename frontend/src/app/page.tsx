import { Suspense } from "react";
import { apiFetch } from "@/lib/api";

async function BackendStatus() {
  let ok = false;
  try {
    const health = await apiFetch<{ status: string }>("/api/health/");
    ok = health.status === "ok";
  } catch {
    // Backend unreachable; fall through to the "unreachable" state.
  }

  return (
    <p className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
      <span
        className={`size-2 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
      />
      Backend {ok ? "connected" : "unreachable"}
    </p>
  );
}

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 font-sans">
      <h1 className="text-3xl font-semibold tracking-tight">ctr</h1>
      <Suspense fallback={<p className="text-zinc-500">Checking backend…</p>}>
        <BackendStatus />
      </Suspense>
    </main>
  );
}
