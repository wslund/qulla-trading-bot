# DECISIONS – beslutslogg och öppna frågor

Append-only. Nya beslut läggs sist med nästa nummer och datum. Öppna frågor ligger överst och flyttas ner som beslut när de avgörs.

## Öppna frågor

- **Q-001** Stöder Interactive Brokers API automatiserad orderläggning på svenskt ISK (lanserat 2025-09-30)? Vilka ordertyper, marknader och datafeeds? Finns andra svenska ISK-aktörer med officiellt API? Nordnet tar inte emot nya API-kunder. Verifieras mot officiell dokumentation. Fas 1.
- **Q-002** Dataleverantör: dagsdata OHLCV för valt universum inklusive avnoterade bolag, splitjustering, kostnad, licens och möjlighet till intradagsdata senare. Exempel att utvärdera: Norgate Data, EODHD, Polygon. Fas 1.
- **Q-003** Kostnadsmodell för vald mäklare: courtage, valutaväxling, antaganden om spread och slippage. Fas 1.
- **Q-004** Universum. Preliminärt USA `[H]`; bekräftas när Q-001–Q-003 är avgjorda. Fas 1–2.
- **Q-005** Startkapital och riskparametrar (max risk per affär, max exponering, förlustgränser). Behövs för spec och backtest. Avgörs av projektägaren. Fas 2.
- **Q-006** Etikettkälla för Modell A: regelbaserade etiketter, manuell märkning eller kombination. Avgörs före ML-fasen.

## Beslut

### D-001 (2026-10-12) Projektstruktur
Src-layout med paketet `tradingbot` och modulerna `data`, `scanner`, `backtesting`, `risk`. `ml` och `brokers` skapas när respektive fas börjar. `patterns` ingår i `scanner` tills setup-logiken växer. `research/` för experiment och externt material, importeras aldrig från `src/`. `data/` git-ignorerad. Skäl: minimal struktur som ändå gör ansvarsgränserna tydliga.

### D-002 (2026-10-12) Verktygskedja
Python 3.12, uv med committad `uv.lock`, Ruff (lint och format), pytest. GitHub Actions kör Ruff och pytest vid push till `main` och vid pull requests; grön CI krävs före merge. Ingen databas i forskningsfasen; Parquet räcker. Skäl: reproducerbarhet och en verifierad signal med minimal infrastruktur.

### D-003 (2026-10-12) Dokumentsystem och språk
Fyra dokument: `PROJECT_MASTER.md` (långsiktigt minne, ersätter projektbeskrivningen), `SESSION_HANDOFF.md` (skrivs över varje session, startar nya chattar), `STRATEGY_SPEC.md` (versionerad, taggar `[Q]`/`[T]`/`[H]`), `DECISIONS.md` (append-only). Dokumentation, commit-meddelanden och konfigkommentarer på svenska; kod, identifierare och docstrings på engelska.

### D-004 (2026-10-12) Utvecklingsordning
Regelbaserad scanner och dagsdata byggs före ML och intradagsdata. ML-baserad mönsterigenkänning (Modell A) och affärsutvärdering (Modell B) är fortsatt centrala mål, och arkitekturen ska inte försvåra dem (PROJECT_MASTER §3). ML införs bara där mätbart mervärde visas mot baseline.

### D-005 (2026-10-12) Entry-modeller och dagsdata
Backtest på dagsdata använder en förenklad entry-modell och får aldrig presenteras som ett exakt test av opening-range-breakout. Alla backtestresultat anger specversion och entry-modell. Exakt ORB-simulering kräver intradagsdata.

### D-006 (2026-10-12) Dataset V0.2
V0.2 är en tidig, inte fullständigt verifierad prototyp. Den läggs orörd i `research/dataset_v0_2/` på grenen `review/dataset-v0.2` och granskas mot checklistan nedan innan någon kod återanvänds. Utfall: adopteras som grund för `data/`, portas delvis, eller behålls som referens. Portning sker del för del med tester, en pull request per del.

Checklista: datakällor och licenser; universum och avnoterade bolag; point-in-time (splits, noteringsdatum); look-ahead i features och etiketter; setup-etikett skild från utfall; tidsbaserade splittar utan överlapp; reproducerbarhet (råfil → resultat med ett kommando); tester; format och storlek.

### D-007 (2026-10-12) Mäklare
Interactive Brokers är huvudkandidat efter ISK-lanseringen 2025-09-30. Nordnet utgår tills vidare (inga nya API-kunder). Mäklarbeslut tas när Q-001 är verifierad. Ingen livehandel utan uttryckligt beslut; paper trading först.
