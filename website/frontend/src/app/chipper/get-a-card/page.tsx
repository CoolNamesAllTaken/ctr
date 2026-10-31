import type { Metadata } from "next";
import { InfoPage } from "../info-page";

export const metadata: Metadata = { title: "Get a card" };

export default function GetACardPage() {
  return (
    <InfoPage
      title="Get a card"
      intro="Chipper Cards come from Caltreat staff, already loaded and ready to tap."
      drawing="staff handing a rider a Chipper Card"
    >
      <section>
        <h2>Ask Caltreat staff</h2>
        <p>
          Staff register each card to a rider and load it with a starting
          balance. You can start riding as soon as you have it.
        </p>
      </section>
      <section id="adding-value" className="scroll-mt-6">
        <h2>Adding value</h2>
        <p>
          Running low? Caltreat staff can add value to your card. You need at
          least the maximum fare on your card to tap on.
        </p>
      </section>
    </InfoPage>
  );
}
