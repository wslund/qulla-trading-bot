# SESSION_HANDOFF

Senast uppdaterad: 2026-10-25 (Claude, fas 2, andra sessionen). Skrivs över i slutet av varje chatt. Max en sida.

## Status
Fas 0 klar. Fas 2 pågår. `STRATEGY_SPEC.md` v0.1 mergad till `main` (PR #1) och granskad av ChatGPT. **v0.2 ligger som utkast på grenen `docs/strategy-spec-v02`**, ej mergad, väntar på projektägarens godkännande.

## Senaste beslut
D-001–D-007. Inga nya formella beslut. Acceptansnivåerna i spec §12 är fortfarande förslag.

## Pågående uppgift
Godkännande av v0.2 (PR från `docs/strategy-spec-v02`). Ändringar mot v0.1 står i specens versionshistorik. Claude avvisade en punkt i granskningen (att stop kan hamna ≥ entry; följer inte av definitionerna) men lade ändå in invarianten som testkrav.

## Nästa steg
1. Projektägaren läser v0.2, särskilt §6.1 (exekveringsordning) och §12 (A/B-uppdelning), och mergar eller begär ändringar.
2. Projektägaren besvarar Q-005 (kapital, risk per affär) → P-005 låses.
3. Beslut om §12-nivåerna → nytt D-nummer. Måste ske före fas 6.
4. Fas 1 (ChatGPT, parallellt): Q-001–Q-003 med källor, som förslag till D-008+.
5. Fas 3: dataset V0.2 enligt D-006. Fas 4 (datalager) kan påbörjas när Q-002 är avgjord.

## Öppna frågor
Q-001–Q-006 i `DECISIONS.md`. P-001–P-011 i `STRATEGY_SPEC.md` §13.

## Filer att bifoga i nästa chatt
- Alltid: denna fil.
- Spec-arbete: `STRATEGY_SPEC.md`.
- Fas 1: `DECISIONS.md`.
- Fas 4-start: `PROJECT_MASTER.md` §3–4 och `STRATEGY_SPEC.md` §1–3.
