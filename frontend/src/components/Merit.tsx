// Where surplus sunshine goes on this site, best first (backend domain/merit.py):
// CO₂ avoided, counted at 300 € a tonne, plus the money each kWh saves or earns.
// Shown in the simulator and the planner so the order is never a mystery.
import { useI18n, type Key } from "../lib/i18n";
import type { MeritRow } from "../lib/types";

export function Merit({ rows }: { rows: MeritRow[] }) {
  const { t, fmt } = useI18n();
  return (
    <div className="merit">
      <h3>{t("merit.title")}</h3>
      <ol>
        {rows.map((r) => (
          <li key={r.destination}>
            <span className="merit-name">{t(`merit.${r.destination}` as Key)}</span>
            <span className="num">{fmt(r.kg_co2, 2)} kg CO₂</span>
            <span className="num">{fmt(r.eur, 3)} €</span>
          </li>
        ))}
        <li className="merit-last">
          <span className="merit-name">{t("merit.curtail")}</span>
          <span className="num">0</span>
          <span className="num">0</span>
        </li>
      </ol>
      <p className="hint">{t("merit.hint")}</p>
    </div>
  );
}
