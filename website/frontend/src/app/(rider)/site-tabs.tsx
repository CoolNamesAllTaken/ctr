"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

function CalendarIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
      className="size-5"
    >
      <rect x="2.5" y="4" width="15" height="13.5" rx="1.5" />
      <path d="M2.5 8h15M6.5 2v4M13.5 2v4M6 11h2M9 11h2M12 11h2M6 14h2M9 14h2" />
    </svg>
  );
}

function MapIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-5"
    >
      <path d="M1.5 4.5 7 2.5l6 2 5.5-2v13l-5.5 2-6-2-5.5 2v-13ZM7 2.5v13M13 4.5v13" />
    </svg>
  );
}

export function AlertIcon({ className = "size-5" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <path d="M10 1.5 19 18H1L10 1.5Zm-1 6v5h2v-5H9Zm0 6.5v2h2v-2H9Z" />
    </svg>
  );
}

const TABS = [
  {
    href: "/schedule",
    label: "Schedules",
    short: "Schedules",
    Icon: CalendarIcon,
  },
  {
    href: "/map",
    label: "Live Map",
    short: "Map",
    Icon: MapIcon,
  },
  {
    href: "/alerts",
    label: "Real-Time Alerts",
    short: "Alerts",
    Icon: AlertIcon,
  },
] as const;

export function SiteTabs() {
  const pathname = usePathname();

  return (
    <nav className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {TABS.map(({ href, label, short, Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`flex h-[58px] items-center justify-center gap-2 rounded-t text-lg font-medium text-white ${
              active ? "bg-[#dd1f29]" : "bg-[#515151] hover:bg-[#3d3d3d]"
            }`}
          >
            <Icon />
            <span className="sm:hidden">{short}</span>
            <span className="hidden sm:inline">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
