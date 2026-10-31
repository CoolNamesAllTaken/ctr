"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/employee/live", label: "Live" },
  { href: "/employee", label: "Trains" },
  { href: "/employee/stations", label: "Stations & kiosks" },
  { href: "/employee/cards", label: "Chipper cards" },
  { href: "/employee/alerts", label: "Alerts & delays" },
  { href: "/employee/status", label: "Train status" },
] as const;

export function EmployeeTabs() {
  const pathname = usePathname();
  // None of the tabs open until they've logged in.
  if (pathname === "/employee/login") return null;

  return (
    <nav className="flex flex-wrap gap-1 border-b border-zinc-200 dark:border-zinc-800">
      {TABS.map(({ href, label }) => {
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              active
                ? "border-zinc-900 dark:border-zinc-100"
                : "border-transparent text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
