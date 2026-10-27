# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, setup-diagnostik). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Scanner steg 1–2 och setup-bilder mergade (PR #8–#10). 15 NVDA-diagram granskade. **Diagnostik på grenen `feat/setup-diagnostics`**: prior-move start/topp med omräknad procent (kontroll mot lagrat värde), close vs SMA10 och 5-dagarsförändring som diagnostiska mått, setup-grupper för återkommande kandidatdagar (additiva kolumner, regelutfall orörda), observationerna som hypoteser P-002a–d i spec §13, 8 tester (113). Ej mergad. Inga trösklar ändrade.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Granskning av PR `feat/setup-diagnostics`. Efter merge: kör om `scan.py` (nya kolumner) och rita NVDA-kandidaterna igen; kontrollera att diagnostiken bekräftar observationerna (2017-06-27: close vs SMA10 och 5-bar change negativa?).

## Nästa steg
1. Merge; omritning; ingest av 10–20 kända momentumaktier 2016–2024 för kalibrering av P-002a–d (hypoteser, inte beslut). Tröskeländringar bara via DECISIONS med motivering.
2. Steg 3: trigger (§4) + `daily_stop_entry` (§5.2), en kandidat per setup-grupp. Före det: beslut om §12-nivåer.
3. Point-in-time-universum (Q-002), Q-009, MSFT 2020-03-18, D-010 – olösta.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
