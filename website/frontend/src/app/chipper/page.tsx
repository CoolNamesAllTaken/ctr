import Image from "next/image";
import Link from "next/link";
import { Drawing, type DrawingName } from "./drawing";

const TILES: {
  title: string;
  body: string;
  href: string;
  drawing: { name: DrawingName; subject: string };
}[] = [
  {
    title: "New to Chipper?",
    body: "Find out how tapping on and off works.",
    href: "/how-it-works",
    drawing: { name: "tap", subject: "A rider tapping a card on a kiosk" },
  },
  {
    title: "Get a card",
    body: "Caltreat staff issue cards, loaded and ready to ride.",
    href: "/get-a-card",
    drawing: {
      name: "handoff",
      subject: "Staff handing a rider a Chipper Card",
    },
  },
  {
    title: "Adding value",
    body: "Running low? Staff can top up your card.",
    href: "/get-a-card#adding-value",
    drawing: { name: "topup", subject: "Value being added to a card" },
  },
];

// Numbered like the "How to pay" steps on the back of the card.
const STEPS = [
  {
    title: "Get a card",
    body: "Caltreat staff issue Chipper Cards, loaded with a balance and ready to ride.",
  },
  {
    title: "Tap on",
    body: "Tap your card on the kiosk on the platform before you board.",
  },
  {
    title: "Tap off",
    body: "Tap again at your destination and we work out the fare for the zones you rode.",
  },
];

// Real cards have 3.18 mm corners on an 85.6 × 54 mm card.
const PORTRAIT_CORNERS = "5.9% / 3.7%";
const LANDSCAPE_CORNERS = "3.7% / 5.9%";

export default function ChipperHome() {
  return (
    <>
      <section className="overflow-hidden bg-[#e8f6fc]">
        <div className="mx-auto grid w-full max-w-[1120px] items-center gap-12 px-4 py-14 md:grid-cols-2 md:py-20">
          <div className="flex flex-col gap-6">
            <h1 className="text-4xl font-extrabold tracking-tight text-[#132a67] sm:text-5xl">
              One card for every Caltreat ride.
            </h1>
            <p className="max-w-xl text-lg">
              Tap on when you board, tap off when you arrive. Chipper Card
              charges by zone, so you only pay for the distance you travel.
            </p>
            <Link
              href="/get-a-card"
              className="flex w-full max-w-sm items-center justify-between rounded-xl bg-linear-to-b from-[#132a67] to-[#00689b] px-6 py-5 text-xl font-bold text-white shadow-md hover:brightness-125"
            >
              Get started
              <span aria-hidden="true">→</span>
            </Link>
          </div>

          <div className="relative mx-auto aspect-[10/9] w-full max-w-[440px]">
            <Image
              src="/brand/chipper-back.svg"
              alt=""
              width={993}
              height={644}
              unoptimized
              style={{ borderRadius: LANDSCAPE_CORNERS }}
              className="absolute top-[6%] right-0 w-[78%] rotate-6 shadow-xl"
            />
            <Image
              src="/brand/chipper-front.svg"
              alt="The Chipper Card"
              width={644}
              height={993}
              priority
              unoptimized
              style={{ borderRadius: PORTRAIT_CORNERS }}
              className="absolute bottom-0 left-0 w-[45%] -rotate-6 shadow-2xl"
            />
          </div>
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1120px] px-4 py-14">
        <ul className="grid gap-6 md:grid-cols-3">
          {TILES.map(({ title, body, href, drawing }) => (
            <li key={href}>
              <Link
                href={href}
                className="group flex h-full flex-col gap-4 rounded-2xl border border-[#dbe6f4] p-5 shadow-sm transition-shadow hover:shadow-lg"
              >
                <Drawing {...drawing} />
                <h2 className="text-xl font-bold text-[#132a67] group-hover:text-[#2979d9]">
                  {title} <span aria-hidden="true">→</span>
                </h2>
                <p>{body}</p>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="bg-[#e8f6fc]">
        <div className="mx-auto w-full max-w-[1120px] px-4 py-14">
          <h2 className="mb-8 text-3xl font-extrabold tracking-tight text-[#2979d9] uppercase">
            How to pay
          </h2>
          <ol className="grid gap-8 md:grid-cols-3">
            {STEPS.map(({ title, body }, i) => (
              <li key={title} className="flex gap-4">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[#2979d9] text-lg font-extrabold text-white">
                  {i + 1}
                </span>
                <div className="flex flex-col gap-1">
                  <h3 className="text-xl font-bold text-[#132a67]">{title}</h3>
                  <p>{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </>
  );
}
