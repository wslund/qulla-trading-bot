# SESSION_HANDOFF

Senast uppdaterad: 2026-10-31 (Claude, steg 3f). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3d mergade (PR #14–#17). **Steg 3f på grenen `feat/backtesting-trades`**: `backtesting/trades.py` (`step_position`, `Position`, `Trade`, `run_ticker`, `Rejection`, `summarize`, splitjustering, cost_estimate/cost_actual), `scripts/run_trades.py`; 16 tester (199). `SCANNER.md` §13. Ej mergad. Inga trösklar eller riskparametrar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/backtesting-trades`. Därefter: **funktionstest på de 19 pilotaktierna** med `scripts/run_trades.py` (Alpaca + Yahoo-splits, 10k/50k/200k SEK, 0,5/1 %, slippage 5/10/20, Tiered/Fixed, båda fx_mode) – rapportera andel `too_small`/`cost_to_risk`/`partial_skipped` (STEP3_DESIGN §12 punkt 3), samma affärer på Yahoo och Alpaca (punkt 1), dagar från första kandidatdag till fyllning (SCANNER §11-observationen). Resultat = funktionstest (D-012). Sedan minutdata-verifiering (§9) före 3e ORB.

## Nästa steg
1. 3a–3f klara → pilotkörning som funktionstest (D-012) → minutdata-verifiering → 3e ORB → fas 6 backtestmotor (portfölj, equity, §11-rapport). Varje steg egen PR med STEP3_DESIGN §11-tester.
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
- Pilotkörning: `SCANNER.md` §11–13; `DATA_PILOT.md` §6; utdata från `scripts/run_trades.py` (summary.json).
