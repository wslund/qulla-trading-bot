# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, scanner steg 1). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Datapilot klar (PR #5–#7 mergade; hardening PR #8 låg **omergad** på GitHub när denna gren skapades – grenen `feat/scanner-features` utgår därför från `feat/data-pilot-hardening`). **Scanner steg 1 på grenen `feat/scanner-features`**: `scanner/features.py` (spec §2–3, T−1-konvention, splitsäker), `scanner/pipeline.py` + `scripts/build_features.py` (Parquet + metadata), `attach_splits()` för Alpaca raw med Yahoos splitlista, 11 tester (82 totalt), `docs/SCANNER.md`. Ej mergad.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Merge av PR #8 (hardening) och därefter PR `feat/scanner-features`. Sedan: projektägaren kör `build_features.py` på yahoo och alpaca (`--splits-from yahoo`) och granskar featurerader runt kända formationer.

## Nästa steg
1. Merge #8 och scanner-PR:en; kör `build_features.py`; jämför features yahoo vs alpaca (ska vara identiska upp till prisavvikelserna i DATA_PILOT §10).
2. Scanner steg 2: setup-detektion med spec-trösklar → kandidattabell med `candidate_id`; kalibrering av P-002 mot handplockade exempel.
3. Point-in-time-universum (Q-002) är fortsatt blockerande för trovärdiga resultat. Fas 1 (ChatGPT).
4. Q-009 (volymdefinitioner), MSFT 2020-03-18, D-010 – olösta. Beslut om spec §12-nivåer före fas 6.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
