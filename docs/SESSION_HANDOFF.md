# SESSION_HANDOFF

Senast uppdaterad: 2026-10-25 (Claude, datapilot, session 2). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Datapilot steg 1 mergad (PR #5) och verifierad lokalt: AAPL/MSFT/NVDA 2 970 rader, AAPL-split korrekt, SIVB 0 rader. **Steg 2 på grenen `feat/data-pilot-stooq-calendar`**: Stooq-adapter, `price_basis` i schemat (v2) med `rebase()`, NYSE-kalender via `exchange_calendars`, kvalitetsrapport, 38 offline-tester, `DATA_PILOT.md` v0.2. Ej mergad.

## Senaste beslut
D-008 startkapital 10 000 SEK (testa även 50 000 / 200 000). D-009 riskmodellens struktur; inga parametervärden låsta.

## Pågående uppgift
Granskning av PR `feat/data-pilot-stooq-calendar`. Efter merge: projektägaren läser Stooqs villkor, kör om Yahoo-ingest (schema v2), kör Stooq-ingest, `compare_sources.py --infer` och `quality_report.py`; utfallet in i `DATA_PILOT.md` §8. Stooqs prisbas är okänd tills `--infer` körts.

## Nästa steg
1. Merge; lokala körningar enligt `DATA_PILOT.md` §8; justera Stooq-adapterns `price_basis` om `--infer` kräver det.
2. Alpaca-adapter bara om Stooq inte duger som andra källa.
3. Fas 1 (ChatGPT): Q-001–Q-003, Q-007, Q-008. Q-002 kan nu formuleras skarpare: det betalda abonnemanget ska leverera avnoterade bolag och noteringsdatum; priser och splittar finns gratis.
4. Beslut om spec §12-nivåer före fas 6. Fas 3: dataset V0.2 enligt D-006.

## Öppna frågor
Q-001–Q-008 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
