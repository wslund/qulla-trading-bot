# SESSION_HANDOFF

Senast uppdaterad: 2026-10-12 (Claude, fas 0). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Fas 0 klar. Projektskelett skapat: struktur, `pyproject.toml` (uv, Ruff, pytest), `uv.lock`, CI-workflow och fyra dokument. Ruff och pytest passerar. Ingen tradinglogik, ingen ML, ingen mäklarkod.

## Senaste beslut
D-001–D-007 i `DECISIONS.md`: struktur, verktygskedja, dokumentsystem, utvecklingsordning, entry-modeller på dagsdata, hantering av dataset V0.2, mäklarläge.

## Pågående uppgift
Ingen. Projektägaren ska pusha repot till GitHub och kontrollera att CI blir grön.

## Nästa steg
1. Projektägaren: skapa privat repo, pusha, verifiera grön CI.
2. ChatGPT (fas 1, kan löpa parallellt): Q-001 IBKR API på ISK, Q-002 dataleverantör, Q-003 kostnadsmodell. Leverera som förslag till nya beslut (D-008 och framåt) med källor.
3. Claude (fas 2): utkast till `STRATEGY_SPEC.md` v0.1. Kräver svar på Q-005 (kapital, risk per affär) och preliminärt universum (Q-004). ChatGPT granskar utkastet.
4. Fas 3: överlämning och granskning av dataset V0.2 enligt D-006.

## Öppna frågor
Q-001–Q-006 i `DECISIONS.md`.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Fas 1 (ChatGPT): `DECISIONS.md`.
- Fas 2 (Claude): `STRATEGY_SPEC.md` samt `PROJECT_MASTER.md` §2 och §6.
