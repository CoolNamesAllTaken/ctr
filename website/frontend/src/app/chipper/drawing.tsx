import { useId } from "react";

// Hand-drawn style illustrations, like the ones on clippercard.com: white
// line art on sky blue, with the birds and sun from the Chipper sketches.
// Strokes are run through a turbulence filter so they wobble like pen lines.

const SKY = "#3cb5ea";

type Props = { className?: string };

// A bird from the sketch: a round body with two spiky wings, one up-left and
// one rotated round to down-right.
function Bird({
  x,
  y,
  scale = 1,
  turn = 0,
}: {
  x: number;
  y: number;
  scale?: number;
  turn?: number;
}) {
  const wing =
    "M2 -15 L-6 -62 L-18 -36 L-34 -54 L-36 -30 L-60 -38 L-46 -17 L-72 -12 L-15 -3";
  return (
    <g
      transform={`translate(${x} ${y}) rotate(${turn}) scale(${scale})`}
      strokeWidth={4 / scale}
    >
      <circle r="15" />
      <path d={wing} />
      <path d={wing} transform="rotate(180)" />
    </g>
  );
}

// The sun from the sketch: a lopsided circle with broken rays.
function Sun({ x, y, r = 30 }: { x: number; y: number; r?: number }) {
  const rays = [0, 45, 90, 135, 180, 225, 270, 315];
  return (
    <g transform={`translate(${x} ${y})`}>
      <ellipse rx={r} ry={r * 1.12} transform="rotate(-12)" />
      {rays.map((a) => (
        <line
          key={a}
          x1={0}
          y1={-(r * 1.45)}
          x2={0}
          y2={-(r * 2.1)}
          transform={`rotate(${a + 8})`}
        />
      ))}
    </g>
  );
}

// A portrait Chipper Card with its chips.
function Card({
  x,
  y,
  turn = 0,
  scale = 1,
}: {
  x: number;
  y: number;
  turn?: number;
  scale?: number;
}) {
  return (
    <g
      transform={`translate(${x} ${y}) rotate(${turn}) scale(${scale})`}
      strokeWidth={4 / scale}
    >
      <rect x="-22" y="-34" width="44" height="68" rx="6" />
      <circle cx="-6" cy="16" r="5" />
      <circle cx="6" cy="20" r="3.5" />
    </g>
  );
}

// The contactless waves a card makes when it taps.
function TapWaves({ x, y, turn = 0 }: { x: number; y: number; turn?: number }) {
  return (
    <g transform={`translate(${x} ${y}) rotate(${turn})`}>
      <path d="M0 -8 Q6 0 0 8" />
      <path d="M9 -16 Q20 0 9 16" />
      <path d="M18 -24 Q34 0 18 24" />
    </g>
  );
}

// A platform kiosk: a reader on a post.
function Kiosk({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="-34" y="0" width="68" height="84" rx="12" />
      <rect x="-22" y="12" width="44" height="26" rx="4" />
      <circle cx="0" cy="60" r="11" />
      <path d="M-8 84 L-10 190 M8 84 L10 190" />
    </g>
  );
}

function Sparkle({ x, y, size = 8 }: { x: number; y: number; size?: number }) {
  return (
    <path
      transform={`translate(${x} ${y})`}
      d={`M0 ${-size} L0 ${size} M${-size} 0 L${size} 0`}
    />
  );
}

const SCENES = {
  // A hand reaching in from the left to tap a card on a kiosk.
  tap: (
    <>
      <Bird x={334} y={50} scale={0.7} turn={-10} />
      <Kiosk x={262} y={80} />
      <path d="M-10 214 C40 196 90 176 128 152 M-10 250 C46 232 100 206 140 176" />
      <path d="M112 164 C118 176 124 186 128 192" />
      <path d="M128 152 C140 140 156 136 160 148 M140 176 C154 172 168 162 160 148" />
      <Card x={176} y={124} turn={-18} />
      <TapWaves x={206} y={120} />
      <path d="M92 96 L116 102 M84 120 L108 122" />
    </>
  ),

  // Staff (in a cap) passing a card to a rider.
  handoff: (
    <>
      <Bird x={196} y={56} scale={0.6} turn={8} />
      <Bird x={296} y={40} scale={0.42} turn={-14} />
      <circle cx="104" cy="150" r="26" />
      <path d="M76 140 C78 112 130 110 132 138 L152 142" />
      <path d="M56 300 C56 168 152 168 152 300" />
      <path d="M140 222 C160 214 178 204 192 196" />
      <circle cx="296" cy="150" r="26" />
      <path d="M248 300 C248 168 344 168 344 300" />
      <path d="M260 222 C240 214 224 206 214 198" />
      <Card x={204} y={186} turn={70} scale={0.6} />
      <Sparkle x={180} y={150} />
      <Sparkle x={232} y={158} size={6} />
      <Sparkle x={208} y={132} size={5} />
    </>
  ),

  // A card with coins dropping onto it.
  topup: (
    <>
      <Sun x={352} y={50} r={22} />
      <Bird x={62} y={58} scale={0.6} turn={12} />
      <Card x={200} y={200} turn={-8} scale={1.3} />
      <path d="M200 52 L200 96 M178 74 L222 74" />
      <circle cx="132" cy="128" r="16" />
      <circle cx="132" cy="128" r="8" />
      <path d="M126 92 L128 104 M140 90 L139 102" />
      <circle cx="272" cy="118" r="13" />
      <circle cx="272" cy="118" r="6" />
      <path d="M268 86 L270 96 M280 84 L279 94" />
      <Sparkle x={140} y={222} />
      <Sparkle x={264} y={246} size={6} />
    </>
  ),

  // A train at the platform and a card tapping on at the kiosk.
  platform: (
    <>
      <Sun x={368} y={34} r={18} />
      <Bird x={210} y={52} scale={0.55} turn={-6} />
      <path d="M-10 256 L410 252" />
      <path d="M-10 110 L140 110 C166 110 180 128 180 156 L180 230 L-10 230" />
      <rect x="10" y="132" width="36" height="32" rx="5" />
      <rect x="60" y="132" width="36" height="32" rx="5" />
      <path d="M118 132 L140 132 C150 132 158 142 158 154 L158 164 L118 164 Z" />
      <path d="M108 128 L108 230" />
      <circle cx="34" cy="242" r="10" />
      <circle cx="146" cy="242" r="10" />
      <Kiosk x={326} y={100} />
      <Card x={232} y={140} turn={-14} scale={0.75} />
      <TapWaves x={256} y={136} turn={-6} />
    </>
  ),
};

export type DrawingName = keyof typeof SCENES;

export function Drawing({
  name,
  subject,
  className,
}: Props & { name: DrawingName; subject: string }) {
  const wobble = useId();
  return (
    <svg
      role="img"
      aria-label={subject}
      viewBox="0 0 400 300"
      className={`aspect-[4/3] w-full overflow-hidden rounded-xl ${className ?? ""}`}
      style={{ backgroundColor: SKY }}
    >
      <filter id={wobble}>
        <feTurbulence
          type="fractalNoise"
          baseFrequency="0.035"
          numOctaves="2"
          seed="7"
        />
        <feDisplacementMap in="SourceGraphic" scale="4" />
      </filter>
      <g
        filter={`url(#${wobble})`}
        fill="none"
        stroke="white"
        strokeWidth="4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {SCENES[name]}
      </g>
    </svg>
  );
}
