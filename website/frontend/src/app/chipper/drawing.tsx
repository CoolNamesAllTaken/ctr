// Holds the place of a hand-drawn illustration, like the ones on
// clippercard.com, until it's drawn. Swap each use for an <Image> of the
// drawing once it exists.
export function DrawingPlaceholder({
  subject,
  className,
}: {
  subject: string;
  className?: string;
}) {
  return (
    <div
      role="img"
      aria-label={`Drawing to come: ${subject}`}
      className={`flex aspect-[4/3] items-center justify-center rounded-xl border-2 border-dashed border-[#a9c6ec] bg-[#e8f6fc] p-4 text-center text-sm font-semibold text-[#5b7fb5] ${className ?? ""}`}
    >
      Drawing: {subject}
    </div>
  );
}
