// The layout shared by the Chipper info pages: a title band, then prose.
export function InfoPage({
  title,
  intro,
  children,
}: {
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <section className="bg-[#0a2f51] text-white">
        <div className="mx-auto w-full max-w-[1120px] px-4 py-12">
          <h1 className="text-4xl font-extrabold tracking-tight">{title}</h1>
          <p className="mt-3 max-w-2xl text-lg text-[#d3e3ef]">{intro}</p>
        </div>
      </section>
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-10 px-4 py-12 text-lg leading-relaxed [&_h2]:mb-2 [&_h2]:text-2xl [&_h2]:font-extrabold [&_h2]:text-[#0a2f51]">
        {children}
      </div>
    </>
  );
}
