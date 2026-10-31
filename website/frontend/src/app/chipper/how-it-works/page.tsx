import type { Metadata } from "next";
import { InfoPage } from "../info-page";

export const metadata: Metadata = { title: "How it works" };

// Mirrors the fare rules in backend/core/fares.py.
export default function HowItWorksPage() {
  return (
    <InfoPage
      title="How it works"
      intro="Your fare depends on how many zones you travel through. Tapping on and off lets us charge you for exactly that."
      drawing={{
        name: "platform",
        subject: "A card tapping on at the platform kiosk",
      }}
    >
      <section>
        <h2>Tap on before you board</h2>
        <p>
          Tap your card on the kiosk on the platform. The kiosk takes the
          maximum fare (a trip across every zone) from your balance, so you need
          at least that much on your card to tap on.
        </p>
      </section>
      <section>
        <h2>Tap off when you arrive</h2>
        <p>
          Tap on the kiosk at your destination. If your trip covered fewer zones
          than the maximum, the difference goes straight back onto your card.
        </p>
      </section>
      <section>
        <h2>Changed your mind?</h2>
        <p>
          Tap off at the station where you tapped on and the whole fare is
          refunded.
        </p>
      </section>
      <section>
        <h2>Forgot to tap off?</h2>
        <p>
          If you don&apos;t tap off within four hours, you keep paying the
          maximum fare and your next tap starts a new trip.
        </p>
      </section>
    </InfoPage>
  );
}
