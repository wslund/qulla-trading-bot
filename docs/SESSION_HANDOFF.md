# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, datapilot, session 4). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Datapilot steg 1–3 mergade (PR #5–#7), verifierade med verklig data: Alpaca 2016-01-04–, Yahoo–Alpaca pris matchar på 0,001 %, SIVB via `--asof`. **Hardening på grenen `feat/data-pilot-hardening`**: Alpaca `end` kapas till gårdagens NY-datum (403-fixen, noteras i metadata), kurshopp klassas splitliknande (WARN) eller verkligt (INFO, påverkar ej status), volymkvot median/IQR i jämförelsen, `scripts/inspect_day.py`, 13 regressionstester (61), DATA_PILOT v0.4 §10, D-010 (föreslagen) och D-011, Q-009/Q-010. Ej mergad.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Granskning av PR `feat/data-pilot-hardening`. Efter merge: `inspect_day.py --ticker MSFT --date 2020-03-18` och `compare_sources.py --a yahoo --b alpaca ... --basis as_traded`; observationer in i DATA_PILOT §10.1–10.2; godkänn eller ändra D-010.

## Nästa steg
1. Merge; körningar enligt DATA_PILOT §8 (två nya rader); D-010.
2. Fas 5-start, avgränsat: regelbaserad scanner steg 1 = features (ADR20, SMA10/20/50, prior move, konsolideringsmått enligt spec §2–3) på lagrade Alpaca-serier, med look-ahead-tester (spec §11.2). Ingen trigger, ingen backtest.
3. Point-in-time-universum: olöst, blockerar trovärdiga resultat (se handoff-svar 2026-10-26). Fas 1 (ChatGPT) Q-002 inriktas på noteringslistor med datum.
4. Beslut om spec §12-nivåer före fas 6. Fas 3: dataset V0.2 enligt D-006.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
