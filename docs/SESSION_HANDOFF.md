# SESSION_HANDOFF

Senast uppdaterad: 2026-11-01 (Claude, portfölj P5). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3f och 3e mergade (PR #14–#20). ORB-pilot (16 aktier, 10k, 1 %): 6 affärer, +9,93 USD, 15 `orb_incomplete` (31/75 öppningsminuter saknas, bl.a. SMCI/CELH) – femminutersregeln behålls. PORTFOLIO_DESIGN v0.2 mergad (PR #21). P1–P4, P6 mergade (PR #22–#26). Första portföljpilot (16 aktier, 10k, 1 %): 9 affärer, −33,23 USD, slut-equity 9 651 SEK, DD 4,02 %, inga portföljavslag. **Gren `feat/portfolio-p5-orb`**: `--entry-model daily|orb` i portföljmotorn och CLI (reservation på dagsplanen, ORB-plan vid 09:35, `reservation_exceeded`, intradagsstopp, `orb_incomplete`-kvot per ticker/år, `candidates`-diagnostik i rapporten); 8 tester (294). Dagsmodellen oförändrad.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/backtesting-trades`. Därefter: **funktionstest på de 19 pilotaktierna** med `scripts/run_trades.py` (Alpaca + Yahoo-splits, 10k/50k/200k SEK, 0,5/1 %, slippage 5/10/20, Tiered/Fixed, båda fx_mode) – rapportera andel `too_small`/`cost_to_risk`/`partial_skipped` (STEP3_DESIGN §12 punkt 3), samma affärer på Yahoo och Alpaca (punkt 1), dagar från första kandidatdag till fyllning (SCANNER §11-observationen). Resultat = funktionstest (D-012). Sedan minutdata-verifiering (§9) före 3e ORB.

## Nästa steg
1. Portföljmotor P1–P8 enligt PORTFOLIO_DESIGN §13 → funktionstest på piloten efter P6 → point-in-time-universum (Q-002) före någon tolkning enligt D-012. Varje steg egen PR med STEP3_DESIGN §11-tester.
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
- Portföljmotor: `PORTFOLIO_DESIGN.md`, `RISK_MODEL.md` §2–3, `STRATEGY_SPEC.md` §6.1/§11.
