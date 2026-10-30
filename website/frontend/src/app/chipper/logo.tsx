// A fare card with its chip and a tap wave. Also drawn in ./icon.svg.
export function CardMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 44" aria-hidden="true" className={className}>
      <rect width="64" height="44" rx="7" fill="#0077c8" />
      <rect x="8" y="14" width="14" height="11" rx="2" fill="#ffc845" />
      <path
        d="M40 12a14 14 0 0 1 0 20M46 7a21 21 0 0 1 0 30"
        fill="none"
        stroke="#fff"
        strokeWidth="3.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function ChipperLogo() {
  return (
    <span className="flex items-center gap-2.5 text-[1.65rem] font-extrabold tracking-tight text-[#0a2f51]">
      <CardMark className="h-8 w-auto" />
      Chipper<span className="font-semibold text-[#0077c8]">Card</span>
    </span>
  );
}
