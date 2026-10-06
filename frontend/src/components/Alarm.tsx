// Gas banners, shown above everything while hydrogen is around.
//   stage 2 (alarm, latched): red, with the reset. The reset asks for a
//     written reason (10 characters or more, enforced by the controller too):
//     it goes into the tamper-evident journal with the operator's name.
//   stage 1 (warning): amber, no action needed; it clears by itself after
//     five minutes of clean air.
// Used for the live installation and for a private simulation (`commandPath`).
import { useRef, useState } from "react";
import { SirenIcon, WindIcon } from "@phosphor-icons/react";
import { useI18n } from "../lib/i18n";
import type { Snapshot } from "../lib/types";
import { useAction } from "./ui";

export function AlarmBanner({ snap, commandPath, canReset }: { snap: Snapshot | null; commandPath: string; canReset: boolean }) {
  const { t, fmt } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const { run, error, busy } = useAction();

  const tel = snap?.telemetry;
  if (!tel) return null;

  if (!tel.h2_alarm_latched) {
    if (!tel.h2_warning) return null;
    return (
      <div className="banner warning" role="status" aria-live="polite">
        <WindIcon size={26} weight="bold" className="icon" aria-hidden />
        <div className="text">
          <strong>{t("warning.title")}</strong> · <span className="num">{fmt(tel.h2_ppm)} ppm</span>
          <div className="dim">{t("warning.body")}</div>
        </div>
      </div>
    );
  }

  const submit = async () => {
    const ok = await run(commandPath, { kind: "reset_h2_alarm", reason: reason.trim() });
    if (ok !== null) {
      dialog.current?.close();
      setReason("");
    }
  };

  return (
    <div className="banner critical" role="alert" aria-live="assertive">
      <SirenIcon size={30} weight="fill" className="icon" aria-hidden />
      <div className="text">
        <strong>{t("alarm.title")}</strong> · <span className="num">{fmt(tel.h2_ppm)} ppm</span>
        <div className="dim">{t("alarm.body")}</div>
      </div>
      {canReset && (
        <button className="btn danger" onClick={() => dialog.current?.showModal()}>
          {t("alarm.reset")}
        </button>
      )}
      <dialog ref={dialog} aria-labelledby="reset-title">
        <div className="stack">
          <h2 id="reset-title">{t("alarm.reset")}</h2>
          <div className="field">
            <label htmlFor="reset-reason">{t("alarm.reason")}</label>
            <textarea id="reset-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} autoFocus />
            <span className="hint">{reason.trim().length}/10+</span>
          </div>
          {error && <div className="error">{error}</div>}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => dialog.current?.close()}>
              {t("common.cancel")}
            </button>
            <button className="btn danger" disabled={busy || reason.trim().length < 10} onClick={() => void submit()}>
              {t("common.confirm")}
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
