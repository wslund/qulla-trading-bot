# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, visuell setup-validering). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Scanner steg 1–2 mergade (PR #8–#9). Första verkliga scan: AAPL 0, MSFT 0, NVDA 3 kandidatdagar (2016-08-31, 2016-09-01, 2017-06-27), identiskt i Yahoo och Alpaca. **Visuell validering på grenen `feat/setup-plots`**: `scanner/plot.py` + `scripts/plot_setup.py` (PNG per beslutsrad med fönster, topp, breakoutnivå, SMA t.o.m. T−1, regler och värden; near misses), matplotlib som extra `viz`, 5 tester (105), `SCANNER.md` §8. Ej mergad.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Granskning av PR `feat/setup-plots`. Efter merge: projektägaren ritar NVDA 2016-08-31 och 2017-06-27 samt near misses 2016–2018 och bedömer kvalitativt om formationerna liknar specens beskrivning (§3). Observationer → kalibrering av P-002.

## Nästa steg
1. Merge; bilder för NVDA-kandidaterna och near misses; anteckna vilka regler som oftast fäller nästan-formationer (`scan.py --near-misses`).
2. Kalibrering P-002 mot fler tickers (kräver bara ingest av t.ex. 10–20 kända momentumaktier 2016–2024); inga tröskeländringar utan dokumenterad motivering i DECISIONS.
3. Steg 3: trigger (§4) + `daily_stop_entry` (§5.2), en kandidat per konsolidering. Före det: beslut om §12-nivåer.
4. Point-in-time-universum (Q-002), Q-009, MSFT 2020-03-18, D-010 – olösta.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
