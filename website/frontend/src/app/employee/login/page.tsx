import type { Metadata } from "next";
import { mutedClass } from "../ui";
import { LoginForm } from "./login-form";

export const metadata: Metadata = {
  title: "Log in · Employee portal · ctr",
};

export default function LoginPage() {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">Log in</h2>
      <p className={mutedClass}>Enter the employee passphrase.</p>
      <LoginForm />
    </section>
  );
}
