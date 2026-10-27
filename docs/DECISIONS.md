# DECISIONS – beslutslogg och öppna frågor

Append-only. Nya beslut läggs sist med nästa nummer och datum. Öppna frågor ligger överst och flyttas ner som beslut när de avgörs.

## Öppna frågor

- **Q-001** Stöder Interactive Brokers API automatiserad orderläggning på svenskt ISK (lanserat 2025-09-30)? Vilka ordertyper, marknader och datafeeds? Finns andra svenska ISK-aktörer med officiellt API? Nordnet tar inte emot nya API-kunder. Verifieras mot officiell dokumentation. Fas 1. Tillägg 2026-10-25: se även Q-007 och Q-008.
- **Q-002** Dataleverantör: dagsdata OHLCV för valt universum inklusive avnoterade bolag, splitjustering, kostnad, licens och möjlighet till intradagsdata senare. Exempel att utvärdera: Norgate Data, EODHD, Polygon. Tillägg 2026-10-25: även rapportdatum (för blackout-variant) och sektorklassificering (för korrelationsgräns), se RISK_MODEL §2. Fas 1.
- **Q-003** Kostnadsmodell för vald mäklare: courtage, valutaväxling, antaganden om spread och slippage. Fas 1. Delvis verifierat 2026-10-25: IBKR ordinarie prislista för US-aktier anger minimum 0,35 USD/order (Tiered) och 1 USD/order (Fixed). Kontospecifika villkor, växling och övriga avgifter återstår.
- **Q-004** Universum. Preliminärt USA `[H]`; bekräftas när Q-001–Q-003 är avgjorda. Fas 1–2.
- **Q-005** Riskparametrar (risk per affär, exponering, förlustgränser). Startkapitalet avgjort i D-008; parametrarna är testvariabler enligt `RISK_MODEL.md` §3 och låses först efter backtest. Fas 2–6.
- **Q-006** Etikettkälla för Modell A: regelbaserade etiketter, manuell märkning eller kombination. Avgörs före ML-fasen.
- **Q-007** Stöder IBKR fraktionshandel i US-aktier på svenskt ISK? Antas **inte** förrän verifierat; positionsstorlek avrundas till hela aktier. Fas 1.
- **Q-009** Volymdefinition: vilka trade conditions exkluderar Alpaca respektive Yahoo vid aggregering av dagsvolym? Avgör om skillnaden (3–9 % median) är en stabil faktor. Mäts med `compare_sources.py` (volB/volA IQR). Datapilot.
- **Q-010** Alpaca rate limit och villkor för paper-/kostnadsfritt konto; vilka avnoterade symboler nås via `asof`. Datapilot.
- **Q-008** Kan USD-saldo hållas på svenskt ISK hos IBKR, eller tvingas växling per affär? Antas **inte** förrän verifierat. Avgör hur FX-kostnad modelleras. Fas 1.

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

### D-008 (2026-10-25) Simulerat startkapital
Startkapital för simulering är 10 000 SEK. Backtester körs även vid 50 000 och 200 000 SEK för att skilja kapitalets betydelse (fasta avgifter, hela aktier) från strategins edge. Beslut av projektägaren. Riskparametrarna låses inte i detta beslut; de testas enligt `RISK_MODEL.md` §3.

### D-009 (2026-10-25) Riskmodellens struktur
`docs/RISK_MODEL.md` är normativ källa för money management och delas i Qullamaggies dokumenterade principer, våra kompletterande regler, testbara parametrar och frågor som blockerar livehandel. Riskmotorn byggs som rena funktioner oberoende av ML. Inga parametervärden i dokumentet är beslutade; `risk_per_trade` testas som 0,5 % och 1 %, `max_cost_to_risk` som 10/15/25 %, rapportblackout som separat variant. ISK-skatt modelleras med användarspecifik grundnivå, inte som noll.

### D-010 (2026-10-26, FÖRESLAGEN – kräver projektägarens godkännande) Volymkälla
Volymbaserade regler (spec §1 likviditet, §4 eventuellt volymfilter) beräknas alltid på en och samma datakälla inom en körning, med relativa mått; källor blandas aldrig för volym. Vilken källa som är pilotens volymkälla avgörs när Q-009 (IQR-mätningen) är gjord. Motivering: Yahoo och Alpaca matchar i pris på 0,001 % men skiljer 3–9 % i volym, vilket är en definitionsskillnad.

### D-011 (2026-10-26) Alpaca som pilotens as-traded-källa för priser
Alpaca SIP raw (2016-01-04–) är pilotens referens för as-traded priser; Yahoo används för split- och utdelningsdata och som andra källa i jämförelser. Skäl: Alpaca levererar ojusterade priser direkt och täcker åtminstone en avnoterad serie (SIVB via `asof`); Yahoo kräver rekonstruktion och saknar SIVB. Inget av dem är survivorship-fritt (D-006, DATA_PILOT §6). Beslutet gäller piloten, inte valet av betald leverantör (Q-002).

### D-012 (2026-10-27) Acceptans- och avbrottskriterier
`STRATEGY_SPEC.md` §12 (v0.2.1) fastställs som förhandsbestämda acceptanskriterier, oförändrade: A – nedre gräns i 95 %-intervallet (blockbootstrap per månad) för EV efter kostnader > 0 R vid kostnad ×1,0 och punktskattning > 0 vid ×1,5; B – tabellen (≥ 300 affärer, EV ≥ 0,15 R, PF ≥ 1,3, ≥ 70 % walk-forward-fönster med positivt EV, max drawdown ≤ 20 % vid 0,5 % risk; motsvarande "inget edge"-kolumn). Nivåerna ändras inte under fas 6; resultat mellan kolumnerna kräver nytt beslut, inte fler parametersök. Pilotresultat utan point-in-time-universum (DATA_PILOT §6) är endast funktionstest och får inte tolkas som bevis på edge. Beslut av projektägaren efter ChatGPT-granskning av STEP3_DESIGN v0.3 (`ab98437`).

### D-013 (2026-10-27) Fyllningspolicy för simulering
STEP3_DESIGN §4.1 fastställs som konservativt simuleringsantagande: signal, antagen fyllning och observerat utfall är separata poster. Köp-stop fylls på första stapel med `high > trigger`, till `open` om `open > trigger` (`gap_fill`) annars `trigger`, plus slippage-scenario (5/10/20 bps, aldrig "uppmätt"). Stop-loss fylls till `open` om `open ≤ stop` (`stop_gap`; förlust > 1R modelleras som den är) annars `stop`, minus slippage. Entry och stop i samma stapel → stoppad, −1R, `ambiguous_same_bar`. Gap över `max_gap_entry` → ingen order. Antal och andel flaggade affärer rapporteras alltid. Förtydligande: reduceras `shares` efter att det antagna fyllningspriset blivit känt är det ett kontrafaktiskt scenario, inte en retroaktiv ändring av en fylld order; det flaggas `shares_reduced_at_fill` och redovisas skilt från affärer som klarade riskgränserna med ursprungligt orderantal. Beslut av projektägaren.

### D-014 (2026-10-27) Dedupe av setups
Nyckel `(ticker, prior_move_peak_date)`. En key förbrukas permanent vid fyllning. Ny affär kräver en ny giltig kandidatrad (alla aktiva regler godkända, vilket kräver ≥ 12 dagar efter toppen) med oförbrukad key och ingen öppen position i tickern. En ny topp är i sig ingen ny formation. Ingen tidsbaserad återaktivering i v1 (`rearm_after_days` är en `[H]`-variant för senare test). STEP3_DESIGN §3. Beslut av projektägaren.
