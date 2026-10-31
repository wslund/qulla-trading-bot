# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, design steg 3 godkänd). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a och 3b mergade (PR #14–#15). **Steg 3c på grenen `feat/signals-daily-trigger`**: `signals/daily_trigger.py` – Signal / FillCandidate (PRELIMINARY) / registerintegration via `approve_and_record`, default-godkännare `pending_risk_approval` som aldrig förbrukar keys, `walk_daily`-driver; 20 tester (161). `SCANNER.md` §11. Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/signals-daily-trigger`. Därefter **3d: `risk/sizing.py` + `risk/costs.py`** enligt RISK_MODEL §2.1–2.2 och STEP3_DESIGN §6: `position_size`, `CostModel` (Tiered/Fixed, slippage-scenario, FX-läge), acceptans på antagen fyllning (`actual_risk`, `cost_to_risk`, kapital) som returnerar `(approved, reason)` i `Approver`-gränssnittet; `shares_reduced_at_fill` som kontrafaktiskt. 10 000 SEK, `fx_rate` parameter.

## Nästa steg
1. 3a–3c klara → 3d sizing/costs (Approver) → 3c daily_trigger (D-013) → 3d sizing/costs (RISK_MODEL, acceptans på antagen fyllning) → 3f trades. Varje steg egen PR < 300 rader med STEP3_DESIGN §11-tester.
2. Minutdata: öppna verifieringspunkter (historikstart, rate limit, SIVB, dubbelhämtning för sena korrigeringar) före 3e (ORB).
3. Point-in-time-universum (Q-002) blockerar fortfarande tolkning enligt D-012; Q-009, MSFT 2020-03-18, D-010 olösta.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
- Steg 3d: `STEP3_DESIGN.md` §6, §11 (Risk/kostnader); `RISK_MODEL.md` §2–3; `signals/daily_trigger.py` (Approver, FillCandidate).
