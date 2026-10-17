import { EmployeeTabs } from "./tabs";

export default function EmployeeLayout({ children }: LayoutProps<"/employee">) {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-10 px-4 py-10 font-sans">
      <header className="flex flex-col gap-4">
        <div>
          <p className="text-sm text-zinc-500">ctr</p>
          <h1 className="text-3xl font-semibold tracking-tight">
            Employee portal
          </h1>
        </div>
        <EmployeeTabs />
      </header>
      {children}
    </main>
  );
}
