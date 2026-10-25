# SESSION_HANDOFF

Senast uppdaterad: 2026-10-25 (Claude, datapilot, session 1). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Fas 2 klar: `STRATEGY_SPEC.md` v0.2.1 och `RISK_MODEL.md` v0.2 mergade (PR #4). **Datapilot påbörjad på grenen `feat/data-pilot-yahoo`**: providergränssnitt, Yahoo-adapter med as-traded-rekonstruktion, kvalitetskontroller, Parquet-lagring med metadata, jämförelsefunktion, CLI-skript, 24 offline-tester, `docs/DATA_PILOT.md`. Ej mergad. Claudes miljö saknar nätverk till Yahoo/Stooq – verkliga hämtningar körs av projektägaren.

## Senaste beslut
D-008 startkapital 10 000 SEK (testa även 50 000 / 200 000). D-009 riskmodellens struktur; inga parametervärden låsta.

## Pågående uppgift
Granskning av PR `feat/data-pilot-yahoo`. Därefter kör projektägaren `DATA_PILOT.md` §8 lokalt (verify_split, ingest, avnoterad ticker) och rapporterar utfall.

## Nästa steg
1. Merge av datapiloten efter granskning; lokala körningar enligt `DATA_PILOT.md` §8.
2. Stooq-adapter (ingen nyckel) → riktigt jämförelsetest. Sedan Alpaca (nyckel via `.env`).
3. Fas 1 (ChatGPT): Q-001–Q-003, Q-007, Q-008.
4. Beslut om spec §12-nivåer före fas 6. Fas 3: dataset V0.2 enligt D-006.

## Öppna frågor
Q-001–Q-008 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13. Testmatris i `RISK_MODEL.md` §3.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Riskgranskning: `RISK_MODEL.md`.
- Fas 1: `DECISIONS.md`.
- Datapilot: `DATA_PILOT.md` och utdata från lokala körningar.
