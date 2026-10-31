# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, design steg 3 godkänd). Skrivs över i slutet av varje chatt. Max en sida.

## Status
STEP3_DESIGN v0.3 mergad med D-012–D-014 (PR #12–#13). **Steg 3a på grenen `feat/scanner-levels`**: `scanner/levels.py` – `OrderLevels` i as-traded-enheter från staplar ≤ T−1, split i fönstret via skuren+rebasad serie, split på T delar nivåer och flaggar, vägran vid fel bas/NaN-splittar, operatorer enligt D-013; 16 tester (129). `SCANNER.md` §9. Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/scanner-levels`. Därefter **3b: `signals/state.py`** – tillstånd per key `(ticker, prior_move_peak_date)` enligt D-014 (ARMED per dag, CONSUMED permanent vid fyllning, en position per ticker, EXPIRED i logg), tester för avbrutna sekvenser, nya toppar i samma formation, tre formationer under samma topp. Ingen fyllningslogik (3c).

## Nästa steg
1. 3a klar (PR) → 3b state/dedupe (D-014) → 3c daily_trigger (D-013) → 3d sizing/costs (RISK_MODEL, acceptans på antagen fyllning) → 3f trades. Varje steg egen PR < 300 rader med STEP3_DESIGN §11-tester.
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
- Steg 3b: `STEP3_DESIGN.md` §3, §11 (Tillstånd/dedupe); `scanner/diagnostics.py` (grupper) och `scanner/levels.py`.
