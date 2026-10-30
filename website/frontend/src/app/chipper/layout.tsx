import type { Metadata } from "next";
import { Nunito_Sans } from "next/font/google";
import Link from "next/link";
import { ChipperLogo } from "./logo";

const nunito = Nunito_Sans({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Chipper Card", template: "%s · Chipper Card" },
  description: "Chipper Card, the fare card for riding Caltreat.",
};

const NAV_LINKS = [
  { label: "How it works", href: "/how-it-works" },
  { label: "Get a card", href: "/get-a-card" },
];

// The Chipper Card site, served on chippercard.com (see src/proxy.ts). Links
// are relative to that host, so /chipper itself never appears in them. Light
// only, like the Caltreat site.
export default function ChipperLayout({ children }: LayoutProps<"/chipper">) {
  return (
    <div
      className={`${nunito.className} flex flex-1 flex-col bg-white text-[#1d2b36] accent-[#0077c8] selection:bg-[#0077c8] selection:text-white [&_:focus-visible]:outline-2 [&_:focus-visible]:outline-offset-2 [&_:focus-visible]:outline-[#0077c8]`}
    >
      <header className="border-b border-[#d6e4ee]">
        <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center justify-between gap-x-8 gap-y-3 px-4 py-5">
          <Link href="/">
            <ChipperLogo />
          </Link>
          <nav className="flex gap-6 text-lg font-semibold text-[#0a2f51]">
            {NAV_LINKS.map(({ label, href }) => (
              <Link key={href} href={href} className="hover:text-[#0077c8]">
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex flex-1 flex-col">{children}</main>

      <footer className="bg-[#0a2f51] py-8 text-center text-sm text-[#b9cfe0]">
        Chipper Card is the fare card for Caltreat, Mountain View to San
        Francisco.
      </footer>
    </div>
  );
}
