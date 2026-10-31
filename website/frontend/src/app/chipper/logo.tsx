import Image from "next/image";

// The chips from the card front beside the CHIPPER® wordmark, both #036298.
// The chips alone are also ./icon.svg. The wordmark is Century Gothic Bold
// converted to outlines, as on the card.
export function ChipperLogo() {
  return (
    <span className="flex items-center gap-2.5">
      <Image
        src="/brand/chipper-chips.svg"
        alt=""
        width={308}
        height={513}
        priority
        unoptimized
        className="h-11 w-auto"
      />
      <Image
        src="/brand/chipper-wordmark.svg"
        alt="Chipper"
        width={797}
        height={143}
        priority
        unoptimized
        className="h-6 w-auto"
      />
    </span>
  );
}
