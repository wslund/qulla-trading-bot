# SESSION_HANDOFF

Senast uppdaterad: 2026-11-01 (Claude, steg 3e). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3f + diagnostikfix mergade (PR #14–#19). **Steg 3e på grenen `feat/signals-orb`**: `signals/orb.py` (OpeningRange med LookAheadError, `simulate_orb_day`, `OrbOutcome` med `OrderPlan`), `data/intraday.py` (sessionsfilter från XNYS-kalendern, `SessionReport`, append-only `IntradayStore`, `compare_fetches`), `AlpacaProvider.fetch_minute`, `run_ticker(minute_bars=...)`, `scripts/fetch_minute.py`, `run_trades.py --entry-model orb`; 3d: `OrderPlan` i `assess_fill` (bakåtkompatibelt). 26 tester (228). `SCANNER.md` §14. Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/backtesting-trades`. Därefter: **funktionstest på de 19 pilotaktierna** med `scripts/run_trades.py` (Alpaca + Yahoo-splits, 10k/50k/200k SEK, 0,5/1 %, slippage 5/10/20, Tiered/Fixed, båda fx_mode) – rapportera andel `too_small`/`cost_to_risk`/`partial_skipped` (STEP3_DESIGN §12 punkt 3), samma affärer på Yahoo och Alpaca (punkt 1), dagar från första kandidatdag till fyllning (SCANNER §11-observationen). Resultat = funktionstest (D-012). Sedan minutdata-verifiering (§9) före 3e ORB.

## Nästa steg
1. 3a–3f klara, 3e på gren → minutdatahämtning + ORB-mot-dags-jämförelse (funktionstest) → fas 6 backtestmotor (portfölj, equity, §11-rapport, point-in-time-universum Q-002). Varje steg egen PR med STEP3_DESIGN §11-tester.
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
- ORB-jämförelse: `SCANNER.md` §13–14; `STEP3_DESIGN.md` §5, §9; utdata från `fetch_minute.py` och `run_trades.py` (båda modellerna).
