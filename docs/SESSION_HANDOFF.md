# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, design steg 3 godkänd). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Scanner steg 1–2, bilder och diagnostik mergade (PR #8–#11). **STEP3_DESIGN v0.3 godkänd** (gren `docs/step3-design`, PR att merga): D-012 acceptanskriterier, D-013 fyllningspolicy, D-014 dedupe fastställda i `DECISIONS.md`. Intradagsdata verifierad (Alpaca SIP 1Min, as-traded, sessionsfilter krävs). Hypoteser P-002a–i i spec §13. Inga trösklar ändrade.

## Senaste beslut
D-012 acceptanskriterier (§12 oförändrad; pilotresultat = funktionstest), D-013 fyllningspolicy (§4.1, `shares_reduced_at_fill` kontrafaktiskt), D-014 dedupe (key förbrukas permanent, ingen tidsåteraktivering). D-010 fortfarande föreslagen.

## Pågående uppgift
Merge av PR `docs/step3-design`. Därefter **3a: `scanner/levels.py`** – `OrderLevels` (breakout_level_at, low_prev_at, adr20) i as-traded-enheter från staplar ≤ T−1, split inuti fönstret via splitjusterad bas skalad till T−1, split på T delar nivåerna och flaggar `split_on_entry_day`, vägrar annan prisbas eller NaN-splittar. Tester: as-traded-priser, split i fönstret, split på T och T+1, look-ahead (invarianta fält), operatorgränser. Inga trösklar ändras.

## Nästa steg
1. 3a levels → 3b state/dedupe (D-014) → 3c daily_trigger (D-013) → 3d sizing/costs (RISK_MODEL, acceptans på antagen fyllning) → 3f trades. Varje steg egen PR < 300 rader med STEP3_DESIGN §11-tester.
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
- Steg 3a: `STEP3_DESIGN.md` §2, §8, §11 (Nivåer); `scanner/features.py` för basen.
