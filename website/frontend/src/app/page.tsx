import { redirect } from "next/navigation";

// The Caltreat site opens on the schedule. (chippercard.com's / is rewritten to
// /chipper in proxy.ts, so it never reaches this page.)
export default function Home() {
  redirect("/schedule");
}
