# SESSION_HANDOFF

Senast uppdaterad: 2026-10-26 (Claude, datapilot, session 3). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Datapilot steg 1–2 mergade (PR #5, #6). **Steg 3 på grenen `feat/data-pilot-alpaca`**: Alpaca Market Data-adapter (bars, sip, raw, pagination, 429/5xx-retry, nycklar via `.env`), NaN som 'okänt' för split/utdelning med rebase-spärr, `--asof` för avnoterade, 48 offline-tester, `DATA_PILOT.md` v0.3. Ej mergad. Projektägaren har verifierat ett verkligt Alpaca-anrop (22 staplar).

## Senaste beslut
D-008 startkapital 10 000 SEK (testa även 50 000 / 200 000). D-009 riskmodellens struktur; inga parametervärden låsta.

## Pågående uppgift
Granskning av PR `feat/data-pilot-alpaca`. Efter merge: projektägaren kör `DATA_PILOT.md` §8 (Alpaca-raderna: historikstart, jämförelse mot Yahoo på `as_traded`, SIVB med och utan `--asof`) och Stooq-raderna om de inte är gjorda. Utfall in i §8.

## Nästa steg
1. Merge; lokala körningar enligt `DATA_PILOT.md` §8; resultat till Claude för tolkning.
2. Beslut om pilotens huvudkälla (Yahoo as-traded med splitlista vs Alpaca rå utan) och om splitdata ska hämtas till Alpaca.
3. Fas 1 (ChatGPT): Q-001–Q-003, Q-007, Q-008; Q-002 skärpt till survivorship-täckning och noteringsdatum.
4. Beslut om spec §12-nivåer före fas 6. Fas 3: dataset V0.2 enligt D-006.

## Öppna frågor
Q-001–Q-008 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
