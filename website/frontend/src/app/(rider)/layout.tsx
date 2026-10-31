import { Roboto, Roboto_Mono } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import { SiteTabs } from "./site-tabs";

const roboto = Roboto({
  subsets: ["latin"],
  weight: ["400", "500", "700", "900"],
});

const robotoMono = Roboto_Mono({
  variable: "--font-roboto-mono",
  subsets: ["latin"],
});

// Caltrain's top menu. Sections without a page here yet aren't links.
const NAV_LINKS = [
  { label: "Stations", href: "/schedule" },
  { label: "Fares", href: null },
  { label: "Rider Information", href: "/alerts" },
  { label: "Projects", href: null },
  { label: "About Caltreat", href: "/" },
];

// The rider-facing site's chrome, styled after caltrain.com. Light only, like
// the original.
export default function RiderLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      className={`${roboto.className} ${robotoMono.variable} flex flex-1 flex-col bg-white text-[#212529] accent-[#dd1f29] selection:bg-[#dd1f29] selection:text-white [&_:focus-visible]:outline-2 [&_:focus-visible]:outline-offset-2 [&_:focus-visible]:outline-[#dd1f29]`}
    >
      <header>
        <div className="mx-auto w-full max-w-[1160px] px-4">
          <div className="flex h-[112px] items-center">
            <Link href="/">
              <Image
                src="/brand/caltreat-logo.svg"
                alt="Caltreat"
                width={104}
                height={87}
                priority
                unoptimized
              />
            </Link>
          </div>
          <nav className="flex flex-wrap justify-between gap-x-6 gap-y-1 pb-4 text-base text-[#dd1f29] sm:text-lg">
            {NAV_LINKS.map(({ label, href }) =>
              href ? (
                <Link key={label} href={href} className="hover:underline">
                  {label}
                </Link>
              ) : (
                <span key={label}>{label}</span>
              ),
            )}
          </nav>
          <SiteTabs />
        </div>
        <div className="h-2 bg-[#dd1f29]" />
      </header>

      <main className="flex flex-1 flex-col">{children}</main>

      <footer className="py-6 text-center text-sm text-[#515151]">
        Caltreat · San Jose to San Francisco
      </footer>
    </div>
  );
}
