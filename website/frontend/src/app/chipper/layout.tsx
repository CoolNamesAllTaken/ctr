import type { Metadata } from "next";
import { Montserrat } from "next/font/google";
import Link from "next/link";
import { ChipperLogo } from "./logo";

// The card art is set in Century Gothic, which isn't a web font. Montserrat is
// the closest match, and it's what the Chipper readers use.
const montserrat = Montserrat({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Chipper Card", template: "%s · Chipper Card" },
  description: "Chipper Card, the fare card for riding Caltreat.",
};

const NAV_LINKS = [
  { label: "How it works", href: "/how-it-works" },
  { label: "Get a card", href: "/get-a-card" },
];

const FOOTER_LINKS = [
  { label: "How it works", href: "/how-it-works" },
  { label: "Get a card", href: "/get-a-card" },
  { label: "Adding value", href: "/get-a-card#adding-value" },
];

// The Chipper Card site, served on chippercard.com (see src/proxy.ts) and
// styled after clippercard.com, in the colours of the card art. Links are
// relative to that host, so /chipper itself never appears in them. Light
// only, like the Caltreat site.
export default function ChipperLayout({ children }: LayoutProps<"/chipper">) {
  return (
    <div
      className={`${montserrat.className} flex flex-1 flex-col bg-white text-[#1b2433] accent-[#2979d9] selection:bg-[#2979d9] selection:text-white [&_:focus-visible]:outline-2 [&_:focus-visible]:outline-offset-2 [&_:focus-visible]:outline-[#2979d9]`}
    >
      <header className="border-b border-[#dbe6f4]">
        <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center justify-between gap-x-8 gap-y-3 px-4 py-4">
          <Link href="/">
            <ChipperLogo />
          </Link>
          <nav className="flex items-center gap-6 font-semibold text-[#036298]">
            {NAV_LINKS.map(({ label, href }) => (
              <Link key={href} href={href} className="hover:text-[#2979d9]">
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex flex-1 flex-col">{children}</main>

      <footer className="border-t border-[#dbe6f4]">
        <div className="mx-auto grid w-full max-w-[1120px] gap-10 px-4 py-12 sm:grid-cols-3">
          <div className="flex flex-col gap-3">
            <Link href="/" className="w-fit">
              <ChipperLogo />
            </Link>
            <p className="text-sm">The fare card for Caltreat.</p>
          </div>
          <div>
            <h2 className="mb-3 text-sm font-bold tracking-wider text-[#132a67] uppercase">
              Getting started
            </h2>
            <ul className="flex flex-col gap-2">
              {FOOTER_LINKS.map(({ label, href }) => (
                <li key={href}>
                  <Link href={href} className="hover:text-[#2979d9]">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h2 className="mb-3 text-sm font-bold tracking-wider text-[#132a67] uppercase">
              Customer support
            </h2>
            <p>Ask Caltreat staff at any station.</p>
          </div>
        </div>
        <div className="border-t border-[#dbe6f4] text-[#5f6b7a]">
          <p className="mx-auto w-full max-w-[1120px] px-4 py-5 text-sm">
            Chipper Card is the fare card for Caltreat, Mountain View to San
            Francisco.
          </p>
        </div>
      </footer>
    </div>
  );
}
