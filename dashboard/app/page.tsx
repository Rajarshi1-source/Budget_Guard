import { Suspense } from "react";
import { api } from "@/lib/api";
import { AutoRefresh } from "@/components/AutoRefresh";
import { ServiceCard } from "@/components/ServiceCard";

export default function Home() {
  return (
    <main>
      <h1>Service reliability</h1>
      <p className="demo-note">Demo environment: traffic is synthetic and faults are injected on a schedule.</p>
      <Suspense fallback={<p className="muted">Loading services…</p>}>
        <ServiceCards />
      </Suspense>
      <AutoRefresh seconds={15} />
    </main>
  );
}

async function ServiceCards() {
  const services = await api.services();
  return <section className="cards">{services.map((s) => <ServiceCard key={s.service} s={s} />)}</section>;
}
