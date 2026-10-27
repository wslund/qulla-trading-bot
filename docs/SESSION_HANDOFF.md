# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, scanner steg 2). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Datapilot och scanner steg 1 mergade (PR #5–#8), features validerade över AAPL-splitten på Yahoo och Alpaca. **Scanner steg 2 på grenen `feat/scanner-rules`**: `scanner/rules.py` (spec §1–3 som regler med taggar, saknade värden = underkänt, [H]/P-001 av som default), `build_candidates` + `scripts/scan.py` (datum/period, near misses, `--set` för trösklar), additiv feature `dollar_volume_20d` (featureversion 1.1), 18 tester (100), `SCANNER.md` §7. Ej mergad.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Granskning av PR `feat/scanner-rules`. Efter merge: `build_features.py` måste köras om (featureversion 1.1), sedan `scan.py` på yahoo/alpaca för 2016–2026; granska kandidatdatum mot kurvor för AAPL/MSFT/NVDA som funktionstest, inte som edge.

## Nästa steg
1. Merge; kör om features och scan; notera antal kandidater och godkännandegrad per regel (`.meta.json`).
2. Kalibrering P-002: 10–20 handplockade historiska formationer → blir de kandidater? Dokumenterade justeringar.
3. Steg 3: trigger (§4) + `daily_stop_entry` (§5.2) med en kandidat per konsolidering. Före det: beslut om §12-nivåer.
4. Point-in-time-universum (Q-002), Q-009, MSFT 2020-03-18, D-010 – olösta.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
