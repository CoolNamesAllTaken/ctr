import Image from "next/image";
import Link from "next/link";
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
      <h1 className="w-full max-w-2xl px-4">
        <Image
          src="/brand/caltreat-banner.svg"
          alt="Caltreat"
          width={1000}
          height={300}
          priority
          unoptimized
          className="h-auto w-full"
        />
      </h1>
      <Suspense fallback={<p className="text-zinc-500">Checking backend…</p>}>
        <BackendStatus />
      </Suspense>
      <Link
        href="/schedule"
        className="font-medium underline underline-offset-4"
      >
        View the train schedule
      </Link>
    </main>
  );
}
