# SESSION_HANDOFF

Senast uppdaterad: 2026-11-15 (Claude, U2B-FREE-1). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3f och 3e mergade (PR #14–#20). ORB-pilot (16 aktier, 10k, 1 %): 6 affärer, +9,93 USD, 15 `orb_incomplete` (31/75 öppningsminuter saknas, bl.a. SMCI/CELH) – femminutersregeln behålls. PORTFOLIO_DESIGN v0.2 mergad (PR #21). P1–P4, P6 mergade (PR #22–#26). Första portföljpilot (16 aktier, 10k, 1 %): 9 affärer, −33,23 USD, slut-equity 9 651 SEK, DD 4,02 %, inga portföljavslag. P5 mergad (PR #27). ORB-portföljpilot: 4 affärer, −12,11 USD, 6 `reservation_exceeded`; MRNA 2026-09-21 `zero_shares` = D-016 (dagsplan 11,86 USD/aktie mot ORB 4,90). D-018 mergad (PR #28); båda piloterna omkörda utan ändring (daily 9 affärer −33,23 USD; ORB 4 affärer −12,11 USD; `days_prevented=0`). UNIVERSE_DESIGN v0.1 mergad (PR #29). U0 mergad (PR #30): `data/snapshots.py` + `scripts/snapshot_listings.py`, D-019; första riktiga körning 2026-11-01 komplett (5 616 + 7 670 Nasdaq-rader, 33 567 Alpaca-assets). **Gren `feat/u1a-universe-model`** (ej mergad när U1B skrevs): U1 steg A – `data/universe_model.py`, `backtesting/universe.py`, D-024 fastställer D-020–D-023; 22 tester (333). **Gren `feat/u1b-universe-engine`** (ovanpå U1A): `run_portfolio(..., universe=, instrument_ids=)`, default `PilotUniverse` bitidentiskt; `not_in_universe` (T−1-medlemskap, key ARMED); händelser i öppna positioner observeras, avnotering → `valid_for_evaluation=False` + banner; `universe_audit`/rapportsektion `universe`/`config.json`; `--universe`/`--universe-mode` i CLI; serienyckel → `instrument_id` via ägare på sista stapeldatum, tvetydighet stoppar; `report_version 2`, `PORTFOLIO_VERSION 4`; 16 tester (349). Ingen leverantör, ingen avnoteringsexekvering.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
D-025 mergad (main `1be9e16`, 358 tester). Nytt krav **0 kr datakostnader** → D-026; rapporterna `U2A_DATAKALLOR.md` (betalda källor, arkiv) och `U2A_FREE_DATAKALLOR.md` (gratisvägen) ligger utanför repot tills ägaren lägger in dem. **Gren `feat/u2b-free-1-massive-snapshots`**: ny snapshotkälla `massive_tickers` (`snapshot_massive_tickers`, aktiva + inaktiva listor per `effective_date`, paginering via `next_url`, 12 s takt, nyckel bara i header, `REDACTED` vid eko, `extra.label = effective_dated_reconstructed`), CLI `--sources massive --date`, `MASSIVE_API_KEY` i `.env.example`, DATA_PILOT §7.2, UNIVERSE_DESIGN §9.3; 15 tester (373). **Inga verkliga Massive-anrop gjorda.** Nästa: ägaren läser Massives villkor och kör `--sources massive --date <igår>` en gång för att mäta räckvidd och rate-limit-headrar; därefter U2B-FREE-2 (laddare snapshot → `UniverseModel`, `point_in_time=False`), U2B-FREE-3 (§8-mätning mot U0 samma dag), U2B-FREE-4 (EDGAR 25-NSE/8-A12B-index). Ägaren kan också köra Internet Archive-kontrollen i `U2A_FREE_DATAKALLOR.md` §2.4.

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
