# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, design steg 3 godkänd). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Steg 3a (`scanner/levels.py`) mergad (PR #14). **Steg 3b på grenen `feat/signals-state`**: `signals/state.py` – `SetupRegistry` med key `(ticker, prior_move_peak_date)`, ARMED per dag, permanent förbrukning vid fyllning, en position per ticker, CLOSED/EXPIRED, `InvalidTransition`, transition-logg, `walk_candidates` för torrkörning; 12 tester (141). `SCANNER.md` §10. Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Granskning och merge av PR `feat/signals-state`. Därefter **3c: `signals/daily_trigger.py`** – `daily_stop_entry` enligt spec §5.2/§6/§6.1 och D-013: trigger `high > level`, gapregel, antagen fyllning (`Signal`/`AssumedFill`), initial stop `max(low_prev_at, fill×(1−adr20))`, `min_stop_pct`, samma-dagsstopp `ambiguous_same_day`, kopplat till `SetupRegistry` och `compute_levels`. Ingen risk/sizing (3d) – antal aktier = 1 som platshållare tills 3d.

## Nästa steg
1. 3a, 3b klara → 3c daily_trigger (D-013) → 3c daily_trigger (D-013) → 3d sizing/costs (RISK_MODEL, acceptans på antagen fyllning) → 3f trades. Varje steg egen PR < 300 rader med STEP3_DESIGN §11-tester.
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
- Steg 3c: `STEP3_DESIGN.md` §4, §4.1, §8, §11 (Dagsmodell); `scanner/levels.py`, `signals/state.py`.
