# SESSION_HANDOFF

Senast uppdaterad: 2026-11-22 (Claude, U2B-FREE-2B). Skrivs över i slutet av varje chatt. Max en sida.

## Status
3a–3f och 3e mergade (PR #14–#20). ORB-pilot (16 aktier, 10k, 1 %): 6 affärer, +9,93 USD, 15 `orb_incomplete` (31/75 öppningsminuter saknas, bl.a. SMCI/CELH) – femminutersregeln behålls. PORTFOLIO_DESIGN v0.2 mergad (PR #21). P1–P4, P6 mergade (PR #22–#26). Första portföljpilot (16 aktier, 10k, 1 %): 9 affärer, −33,23 USD, slut-equity 9 651 SEK, DD 4,02 %, inga portföljavslag. P5 mergad (PR #27). ORB-portföljpilot: 4 affärer, −12,11 USD, 6 `reservation_exceeded`; MRNA 2026-09-21 `zero_shares` = D-016 (dagsplan 11,86 USD/aktie mot ORB 4,90). D-018 mergad (PR #28); båda piloterna omkörda utan ändring (daily 9 affärer −33,23 USD; ORB 4 affärer −12,11 USD; `days_prevented=0`). UNIVERSE_DESIGN v0.1 mergad (PR #29). U0 mergad (PR #30): `data/snapshots.py` + `scripts/snapshot_listings.py`, D-019; första riktiga körning 2026-11-01 komplett (5 616 + 7 670 Nasdaq-rader, 33 567 Alpaca-assets). **Gren `feat/u1a-universe-model`** (ej mergad när U1B skrevs): U1 steg A – `data/universe_model.py`, `backtesting/universe.py`, D-024 fastställer D-020–D-023; 22 tester (333). **Gren `feat/u1b-universe-engine`** (ovanpå U1A): `run_portfolio(..., universe=, instrument_ids=)`, default `PilotUniverse` bitidentiskt; `not_in_universe` (T−1-medlemskap, key ARMED); händelser i öppna positioner observeras, avnotering → `valid_for_evaluation=False` + banner; `universe_audit`/rapportsektion `universe`/`config.json`; `--universe`/`--universe-mode` i CLI; serienyckel → `instrument_id` via ägare på sista stapeldatum, tvetydighet stoppar; `report_version 2`, `PORTFOLIO_VERSION 4`; 16 tester (349). Ingen leverantör, ingen avnoteringsexekvering.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
U2B-FREE-1 mergad (main `54706a9`, 373 tester); första riktiga Massive-hämtning 2026-11-20 komplett (36 938 poster, 38 sidor, ~7,5 min) och analyserad lokalt (U2B-FREE-2A, rapport under `data/research/`, ej i Git). **Gren `feat/u2b-free-2b-massive-inventory`**: `data/universe_massive.py` – verifierad `load_snapshot`, strikt urval med orsaker, FIGI-identitet (exact/derived/surrogat, delad FIGI → uncertain), tickeråteranvändning kontra tvetydighet, `InstrumentObservation` utan startdatum, `ImportReport`, `MassiveInventory` under `data/universe_obs/`, `observed_members`; `to_universe_model` medvetet oimplementerad; `scripts/build_universe_inventory.py`; 11 tester (384). Nästa: **U2B-FREE-3** – välj källa för startdatum (andra `effective_date` och/eller Alpacas första stapel) och bygg då `UniverseModel` ur inventeringen; §8-jämförelse mot U0-snapshoten samma dag; W0-rapporten (watchlist: avstå) ligger utanför repot.

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
