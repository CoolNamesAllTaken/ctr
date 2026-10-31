import { Drawing, type DrawingName } from "./drawing";

// The layout shared by the Chipper info pages: a title band with a drawing,
// then prose.
export function InfoPage({
  title,
  intro,
  drawing,
  children,
}: {
  title: string;
  intro: string;
  drawing: { name: DrawingName; subject: string };
  children: React.ReactNode;
}) {
  return (
    <>
      <section className="bg-[#e8f6fc]">
        <div className="mx-auto grid w-full max-w-[1120px] items-center gap-8 px-4 py-12 md:grid-cols-[3fr_2fr]">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-[#132a67]">
              {title}
            </h1>
            <p className="mt-3 max-w-2xl text-lg">{intro}</p>
          </div>
          <Drawing {...drawing} className="max-w-[320px] md:justify-self-end" />
        </div>
      </section>
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-10 px-4 py-12 text-lg leading-relaxed [&_h2]:mb-2 [&_h2]:text-2xl [&_h2]:font-extrabold [&_h2]:text-[#132a67]">
        {children}
      </div>
    </>
  );
}
