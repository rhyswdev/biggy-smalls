import { AppBar } from "@/components/shell/AppBar";
import { api } from "@/lib/api";

interface Capabilities { mode: string; checkpoint: string; now: string; profile: string }

export default async function Dashboard() {
  const caps = await api<Capabilities>("/capabilities");
  return (
    <div className="v-dash">
      <AppBar current="dashboard" />
      <main style={{ maxWidth: 1160, margin: "0 auto", padding: "28px 24px 64px" }}>
        <p className="body">Mode {caps.mode}, profile {caps.profile}, now <span className="time">{caps.now}</span>.</p>
      </main>
    </div>
  );
}
