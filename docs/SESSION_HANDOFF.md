# SESSION_HANDOFF

Senast uppdaterad: 2026-11-22 (Claude, U2B-FREE-3A). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3f och 3e mergade (PR #14–#20). ORB-pilot (16 aktier, 10k, 1 %): 6 affärer, +9,93 USD, 15 `orb_incomplete` (31/75 öppningsminuter saknas, bl.a. SMCI/CELH) – femminutersregeln behålls. PORTFOLIO_DESIGN v0.2 mergad (PR #21). P1–P4, P6 mergade (PR #22–#26). Första portföljpilot (16 aktier, 10k, 1 %): 9 affärer, −33,23 USD, slut-equity 9 651 SEK, DD 4,02 %, inga portföljavslag. P5 mergad (PR #27). ORB-portföljpilot: 4 affärer, −12,11 USD, 6 `reservation_exceeded`; MRNA 2026-09-21 `zero_shares` = D-016 (dagsplan 11,86 USD/aktie mot ORB 4,90). D-018 mergad (PR #28); båda piloterna omkörda utan ändring (daily 9 affärer −33,23 USD; ORB 4 affärer −12,11 USD; `days_prevented=0`). UNIVERSE_DESIGN v0.1 mergad (PR #29). U0 mergad (PR #30): `data/snapshots.py` + `scripts/snapshot_listings.py`, D-019; första riktiga körning 2026-11-01 komplett (5 616 + 7 670 Nasdaq-rader, 33 567 Alpaca-assets). **Gren `feat/u1a-universe-model`** (ej mergad när U1B skrevs): U1 steg A – `data/universe_model.py`, `backtesting/universe.py`, D-024 fastställer D-020–D-023; 22 tester (333). **Gren `feat/u1b-universe-engine`** (ovanpå U1A): `run_portfolio(..., universe=, instrument_ids=)`, default `PilotUniverse` bitidentiskt; `not_in_universe` (T−1-medlemskap, key ARMED); händelser i öppna positioner observeras, avnotering → `valid_for_evaluation=False` + banner; `universe_audit`/rapportsektion `universe`/`config.json`; `--universe`/`--universe-mode` i CLI; serienyckel → `instrument_id` via ägare på sista stapeldatum, tvetydighet stoppar; `report_version 2`, `PORTFOLIO_VERSION 4`; 16 tester (349). Ingen leverantör, ingen avnoteringsexekvering.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
U2B-FREE-2B mergad (main `be6365a`, 384 tester). Designrapport `U2B_FREE_3_DESIGN.md` (utanför repot). **Gren `feat/u2b-free-3a-universe-timeline`**: `data/universe_timeline.py` – `select_revisions`/`link_inventories` (en inventering per `effective_date`, FIGI-länkning, surrogat olänkade, delad FIGI/CIK-konflikt → uncertain), `InstrumentTimeline` med `dates_not_observed`, `TickerChangeBound (D1, D2]`, `DelistingFact` (consistent/inconsistent/unknown), flaggor för återaktivering/revision/attributbyte/återanvänd ticker, `LinkReport`, `revision_report`; 11 tester (395). Nästa: ägaren hämtar ett andra Massive-snapshot (~2025-11-20) och omhämtar 2026-11-20 senare; **U2B-FREE-3B** `timeline_to_model` med deklarerad policy; **3C** U0-jämförelse samma `effective_date`.

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
