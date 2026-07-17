"""AttentionLayer — mechanizm uwagi Liry.

Scoruje sygnały z busa według istotności (salience) i składa focus_text
dla warstwy Phi. Używa attention_threshold z policy do bramkowania.

Hierarchia sygnałów:
  1. intent / last_user_message  — gdy rozmowa aktywna (decay gdy nieaktywna)
  2. error_pressure              — gdy serwisy padają
  3. queue_depth                 — pending tasks
  4. phi_shift                   — nagła zmiana paradygmatu
  5. phase                       — faza cyklu (kontekst operacyjny)
  6. cognitive_predictions       — predykcje z cognitive night cycle
  7. cognitive_questions         — otwarte pytania wymagające uwagi
  8. causal_density              — gęstość grafu kauzalnego (złożoność myślenia)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List


class AttentionLayer:
    """Oblicza mapę uwagi i fokus-tekst dla bieżącego ticku."""

    # Maksymalne wagi surowych sygnałów
    _W = {
        "intent":            0.92,
        "last_user_message": 0.85,
        "error_pressure":    0.80,
        "queue_depth":       0.65,
        "phi_shift":         0.72,
        "phase_cognitive":   0.55,
        # Cognitive layer signals (Faza C — 2026-04-10)
        "cognitive_predictions": 0.70,
        "cognitive_questions":   0.68,
        "causal_density":        0.50,
    }

    def compute(
        self,
        *,
        conv: Dict[str, Any],
        bus: Dict[str, Any],
        world: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Zwraca attention_state z focus_text i mapą saliency."""

        threshold = float(
            ((policy or {}).get("params") or {}).get("attention_threshold", 0.66)
        )
        now = time.time()

        scores: Dict[str, float] = {}
        fragments: List[str] = []

        # ------------------------------------------------------------------
        # 1. Sygnały konwersacyjne
        # ------------------------------------------------------------------
        conv_active = bool(((world or {}).get("conversation") or {}).get("active"))
        intent = str((conv or {}).get("last_intent") or "").strip()
        last_msg = str((conv or {}).get("last_user_message") or "").strip()

        if conv_active:
            decay = 1.0
        else:
            last_ts = float((conv or {}).get("last_user_ts") or 0.0)
            age_s = now - last_ts if last_ts > 0 else 9999.0
            decay = max(0.0, 1.0 - age_s / 120.0)  # pełny zanik po 2 min

        s_intent = round(self._W["intent"] * decay if intent else 0.0, 4)
        s_msg = round(self._W["last_user_message"] * decay if last_msg else 0.0, 4)

        scores["intent"] = s_intent
        scores["last_user_message"] = s_msg

        if s_intent >= threshold and intent:
            fragments.append(intent)
        if s_msg >= threshold and last_msg and last_msg != intent:
            fragments.append(last_msg)

        # ------------------------------------------------------------------
        # 2. Error pressure (porty)
        # ------------------------------------------------------------------
        ports = (world or {}).get("ports") or {}
        api_ok = bool(((ports.get("api") or {}) if isinstance(ports.get("api"), dict) else {}).get("open"))
        oll_ok = bool(((ports.get("ollama") or {}) if isinstance(ports.get("ollama"), dict) else {}).get("open"))

        err_raw = 0.0
        err_parts: List[str] = []
        if not api_ok:
            err_raw += 0.5
            err_parts.append("api_down")
        if not oll_ok:
            err_raw += 0.5
            err_parts.append("ollama_down")
        s_err = round(min(1.0, err_raw) * self._W["error_pressure"], 4)
        scores["error_pressure"] = s_err
        if s_err >= threshold and err_parts:
            fragments.append("system_error:" + "+".join(err_parts))

        # ------------------------------------------------------------------
        # 3. Queue depth
        # ------------------------------------------------------------------
        qlen = 0
        try:
            qlen = int(((world or {}).get("queue") or {}).get("len") or 0)
        except Exception:
            qlen = 0
        s_queue = round(min(1.0, qlen / 10.0) * self._W["queue_depth"], 4)
        scores["queue_depth"] = s_queue
        if s_queue >= threshold:
            fragments.append(f"queue:{qlen}_tasks")

        # ------------------------------------------------------------------
        # 4. Phi shift (nagła zmiana paradygmatu)
        # ------------------------------------------------------------------
        phi_bus = (bus or {}).get("phi") or {}
        phi_shift = bool(phi_bus.get("shift"))
        phi_paradigm = str(phi_bus.get("paradigm") or "")
        s_phi = round(self._W["phi_shift"] if phi_shift else 0.0, 4)
        scores["phi_shift"] = s_phi
        if s_phi >= threshold and phi_paradigm:
            fragments.append(f"paradigm_shift→{phi_paradigm}")

        # ------------------------------------------------------------------
        # 5. Faza kognitywna
        # ------------------------------------------------------------------
        phase_name = str(((world or {}).get("system") or {}).get("phase", {}).get("phase") or "")
        cognitive_phases = {"INTEGRATE", "DECIDE", "ACT"}
        s_phase = round(self._W["phase_cognitive"] if phase_name in cognitive_phases else 0.0, 4)
        scores["phase_cognitive"] = s_phase
        if s_phase >= threshold:
            fragments.append(f"phase:{phase_name}")

        # ------------------------------------------------------------------
        # 6. Cognitive layer signals (predictions, questions, density)
        # ------------------------------------------------------------------
        cog = (bus or {}).get("cognitive") or {}
        pred_count = int(cog.get("predictions_count") or 0)
        q_count = int(cog.get("open_questions") or 0)
        density = float(cog.get("density") or 0.0)

        # Predictions: scale 0→1 at 5+ predictions
        s_pred = round(min(1.0, pred_count / 5.0) * self._W["cognitive_predictions"], 4)
        scores["cognitive_predictions"] = s_pred
        if s_pred >= threshold:
            fragments.append(f"cog_predictions:{pred_count}")

        # Open questions: scale 0→1 at 3+ questions
        s_questions = round(min(1.0, q_count / 3.0) * self._W["cognitive_questions"], 4)
        scores["cognitive_questions"] = s_questions
        if s_questions >= threshold:
            fragments.append(f"cog_questions:{q_count}")

        # Causal density: raw 0.0-1.0, direct multiply
        s_density = round(min(1.0, density) * self._W["causal_density"], 4)
        scores["causal_density"] = s_density
        if s_density >= threshold:
            fragments.append(f"causal_density:{density:.2f}")

        # Include meta_insights in focus if present and above threshold
        meta = cog.get("meta_insights") or []
        if meta and (s_pred >= threshold or s_questions >= threshold):
            fragments.append(f"insight:{meta[0][:80]}")

        # ------------------------------------------------------------------
        # Złóż focus_text i mapę
        # ------------------------------------------------------------------
        focus_text = " | ".join(fragments)
        above = {k: v for k, v in scores.items() if v >= threshold}

        return {
            "schema": "lira.attention_state.v1",
            "threshold": round(threshold, 4),
            "scores": scores,
            "above_threshold": above,
            "signal_count": len(above),
            "focus_text": focus_text,
            "conv_active": conv_active,
        }
