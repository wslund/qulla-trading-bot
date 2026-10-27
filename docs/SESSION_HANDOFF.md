# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, design steg 3). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Scanner steg 1–2, bilder och diagnostik mergade (PR #8–#11). Bred visuell validering klar: 19 aktier, 162 kandidatdagar i 57 setup-grupper; hypoteser P-002a–i i spec §13. **Designutkast för steg 3 på grenen `docs/step3-design`** (`docs/STEP3_DESIGN.md`): nivåer i as-traded, tillståndsmaskin med dedupe på (ticker, topp), dagsmodell, ORB, risk/kostnader för 10 000 SEK, intradagsverifiering, 12 öppna beslut, testplan. **Ingen PR förrän designen granskats.** Inga trösklar ändrade, ingen kod.

## Senaste beslut
D-008 kapital, D-009 riskmodell, D-011 Alpaca som pilotens as-traded-källa. D-010 (volymkälla) föreslagen, ej godkänd.

## Pågående uppgift
Granskning av `STEP3_DESIGN.md` (ChatGPT + projektägaren). Fokus: §3 dedupe-nyckel, §5 ORB-definitioner, §8 look-ahead-mekanismer, §10 beslut 1 (§12-nivåer måste bli D-012 före första körning), §11 testplanens täckning. Därefter PR med design + P-002e–i.

## Nästa steg
1. Designgranskning → PR `docs/step3-design` → merge.
2. Beslut D-012 (§12-nivåer) innan 3f körs. Intradagsverifiering enligt STEP3_DESIGN §9 (sex körningar mot Alpaca).
3. Implementation 3a→3f i separata PR:er (dagsmodell komplett utan minutdata), sedan 3e (ORB).
4. Point-in-time-universum (Q-002), Q-009, MSFT 2020-03-18, D-010 – olösta.

## Öppna frågor
Q-001–Q-010 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
- Scanner: `SCANNER.md`, `STRATEGY_SPEC.md` §2–4.
- Steg 3: `STEP3_DESIGN.md`, `STRATEGY_SPEC.md` §4–8, `RISK_MODEL.md` §2.
