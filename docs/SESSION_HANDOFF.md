# SESSION_HANDOFF

Senast uppdaterad: 2026-10-25 (Claude, fas 2, tredje sessionen). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Fas 2 nästan klar. `STRATEGY_SPEC.md` v0.2 mergad (PR #2). **`RISK_MODEL.md` v0.1 ligger som utkast på grenen `docs/risk-model-v01`** tillsammans med D-008/D-009, Q-007/Q-008 och spec v0.2.1 (§7 hänvisar till riskmodellen). Ej mergad.

## Senaste beslut
D-008 startkapital 10 000 SEK (testa även 50 000 / 200 000). D-009 riskmodellens struktur; inga parametervärden låsta.

## Pågående uppgift
Granskning av `RISK_MODEL.md` v0.1 (PR från `docs/risk-model-v01`). Fokus: §2.2 portföljkontroller, §2.6 drawdown-stopp (behåll vs stäng positioner), §2.8 skatt och valuta, §3 testmatris (är den för stor för fas 6?).

## Nästa steg
1. Projektägaren granskar och mergar PR:en eller begär ändringar.
2. Fas 1 (ChatGPT): Q-001–Q-003 samt nya Q-007 (fraktionshandel på ISK) och Q-008 (USD-saldo på ISK). Svar som förslag till D-010+.
3. Beslut om spec §12-nivåer före fas 6.
4. Fas 3: dataset V0.2 enligt D-006. Fas 4 (datalager) när Q-002 är avgjord; datalagret ska även rymma rapportdatum och sektor (RISK_MODEL §2.2, §2.7).

## Öppna frågor
Q-001–Q-008 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Fas 4-start: `PROJECT_MASTER.md` §3–4, `STRATEGY_SPEC.md` §1–3, `RISK_MODEL.md` §2.2.
