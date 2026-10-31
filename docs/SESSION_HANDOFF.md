# SESSION_HANDOFF

Senast uppdaterad: 2026-10-31 (Claude, steg 3d). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3c mergade (PR #14–#16). **Steg 3d på grenen `feat/risk-sizing-costs`**: `risk/sizing.py` (`position_size`, `assess_fill`, `RiskDecision`, `RiskApprover`, `account_from_sek`), `risk/costs.py` (`CommissionModel` Tiered/Fixed, `CostParams`, `AssumedCosts`, orderben, FX-lägen, `FX_RATE_PILOT`), `Approval`-protokoll + `as_approval` i `daily_trigger.py` (bakåtkompatibelt); 22 tester (183). `SCANNER.md` §12. Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/risk-sizing-costs`. Därefter **3f: `backtesting/trades.py`** – affärspost från godkänd `RiskDecision` + `FillCandidate`: exits enligt spec §6/§8 (stop-gap vid open med förlust > 1R, delvinst efter `partial_days` → fylls nästa open, breakeven, trailing SMA10), R-multipel, kostnader per fyllning (`AssumedCosts`), rapport per orsak (avstådda per `zero_shares`/`too_small`/`risk_exceeded`/`insufficient_capital`/`cost_to_risk`, `shares_reduced_at_fill` och `partial_skipped` separat, slippage-scenario 5/10/20). Equity-uppdatering efter fyllning hör hit, inte till 3d.

## Nästa steg
1. 3a–3d klara → 3f trades (exits, R, rapport) → körning på de 19 pilotaktierna som funktionstest (D-012) → 3e ORB. Varje steg egen PR med STEP3_DESIGN §11-tester.
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
- Steg 3f: `STEP3_DESIGN.md` §7, §11; `STRATEGY_SPEC.md` §6–8; `SCANNER.md` §11–12; `signals/daily_trigger.py`, `risk/sizing.py`.
