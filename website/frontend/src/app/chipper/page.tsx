import Link from "next/link";
import { CardMark } from "./logo";

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

export default function ChipperHome() {
  return (
    <>
      <section className="overflow-hidden bg-[#0a2f51] text-white">
        <div className="mx-auto grid w-full max-w-[1120px] items-center gap-10 px-4 py-14 md:grid-cols-[3fr_2fr] md:py-20">
          <div className="flex flex-col gap-6">
            <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
              One card for every Caltreat ride.
            </h1>
            <p className="max-w-xl text-lg text-[#d3e3ef]">
              Tap on when you board, tap off when you arrive. Chipper Card
              charges by zone, so you only pay for the distance you travel.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                href="/how-it-works"
                className="rounded-full bg-[#ffc845] px-6 py-3 font-bold text-[#0a2f51] hover:bg-[#ffd76e]"
              >
                How it works
              </Link>
              <Link
                href="/get-a-card"
                className="rounded-full border-2 border-white px-6 py-3 font-bold hover:bg-white hover:text-[#0a2f51]"
              >
                Get a card
              </Link>
            </div>
          </div>
          <CardMark className="mx-auto w-full max-w-[240px] -rotate-6 md:max-w-[320px] drop-shadow-2xl" />
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1120px] px-4 py-14">
        <h2 className="mb-8 text-3xl font-extrabold tracking-tight text-[#0a2f51]">
          Riding with Chipper
        </h2>
        <ol className="grid gap-6 md:grid-cols-3">
          {STEPS.map(({ title, body }, i) => (
            <li
              key={title}
              className="flex flex-col gap-3 rounded-xl bg-[#eef5fb] p-6"
            >
              <span className="flex size-10 items-center justify-center rounded-full bg-[#0077c8] text-lg font-extrabold text-white">
                {i + 1}
              </span>
              <h3 className="text-xl font-bold text-[#0a2f51]">{title}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
