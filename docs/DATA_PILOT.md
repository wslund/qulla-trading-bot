# DATA_PILOT – leverantörsoberoende datapilot

**Version:** 0.4 (2026-10-26). Syfte: verifiera insamling, kvalitet och format för dagsdata från kostnadsfria källor innan ett betalt abonnemang väljs (Q-002). Ingen strategi- eller ML-kod.

> **Pilotdatasetet är inte verifierat survivorship-fritt.** Kostnadsfria källor levererar i huvudsak bolag som fortfarande är noterade. Data från piloten får användas för att testa pipelinen och jämföra källor, **inte** för definitiva lönsamhetsanspråk om strategin. Se §6.

## 1. Arkitektur

```
src/tradingbot/data/
├── schema.py          normaliserat schema (NORMALIZED_COLUMNS) och FetchMeta
├── providers/
│   ├── base.py        MarketDataProvider: fetch_daily() → RawBars, normalize() → DataFrame
│   ├── yahoo.py       yfinance-adapter
│   ├── stooq.py       Stooq CSV-adapter
│   └── alpaca.py      Alpaca Market Data v2 (bars), nycklar via .env
├── quality.py         check_quality() → QualityReport (OK / WARN / FAIL), XNYS-kalender
├── store.py           ParquetStore: raw, processed, metadata
├── compare.py         rebase() till gemensam prisbas, compare_series()
├── report.py          kvalitetsrapport per leverantör från metadata
└── pipeline.py        process() (offline-testbar) och ingest() (nätverk)
src/tradingbot/config.py  load_dotenv(): läser .env utan beroenden, loggar aldrig värden
scripts/ingest.py, compare_sources.py, verify_split.py, quality_report.py
```

Principer: nätverksanrop sker bara i `fetch_daily()`. `normalize()` och allt efteråt är rena funktioner och testas mot `tests/fixtures/`. Rådata sparas oförändrad innan normalisering. Nya källor läggs till som en klass i `providers/` plus en rad i `pipeline.PROVIDERS`.

## 2. Normaliserat schema

| Kolumn | Innehåll |
|---|---|
| `date` | handelsdag (datum, ingen tidszon) |
| `ticker`, `source` | identifierare |
| `open`, `high`, `low`, `close`, `volume` | **as-traded**: priser och volym som de faktiskt var den dagen |
| `adj_close` | split- och utdelningsjusterad stängning enligt källan |
| `adj_factor` | `adj_close / close`; `x × adj_factor` ger justerad version av valfri priskolumn |
| `split_ratio` | splitkvot på splitdagen (4.0 för 4:1), annars 1.0 |
| `dividend` | utdelning per aktie på ex-dagen, annars 0 |
| `price_basis` | vad OHLCV uttrycker: `as_traded`, `split_adjusted` eller `total_return` (schemaversion 2) |

Motiv: as-traded behövs för realistisk simulering (hela aktier, kostnader, stoppnivåer); justerade serier behövs för indikatorer över splittar. Med `adj_factor` lagras båda utan dubbla kolumner. Källor utan split-/utdelningsdata (Stooq) kan inte ge as-traded; då anger `price_basis` vad som faktiskt lagrats. **Serier med olika `price_basis` jämförs aldrig direkt** – `compare.rebase()` uttrycker en as-traded-serie på valfri bas, och `compare_series()` vägrar om baserna inte går att förena. Serier lagrade med schemaversion 1 (utan `price_basis`) avvisas vid läsning; kör `ingest` igen.

## 3. Justeringsmetod per källa

**Yahoo (yfinance, `auto_adjust=False`, `actions=True`).** Yahoo levererar inte as-traded priser: `Open/High/Low/Close/Volume` är splitjusterade bakåt (ej utdelningsjusterade), `Adj Close` är split- och utdelningsjusterad, `Stock Splits` anger kvoten på splitdagen. Adaptern rekonstruerar as-traded genom att multiplicera varje pris med produkten av alla **senare** splitkvoter och dividera volymen med samma faktor. Begränsningar: rekonstruktionen är bara så bra som Yahoos splitlista; omvända splittar och saknade splittar ger fel som kvalitetskontrollen försöker fånga (§4). Antagandet om Yahoos semantik ska bekräftas lokalt med `scripts/verify_split.py --ticker AAPL --split-date 2020-08-31` (förväntat: as-traded ≈ 500 USD före, ≈ 129 efter; Yahoo-rå ≈ 125 på båda sidor). yfinance-versionen sparas i metadata eftersom beteendet har ändrats mellan versioner.

**Stooq (CSV, `https://stooq.com/q/d/l/?s=aapl.us&d1=…&d2=…&i=d`).** Kolumner `Date,Open,High,Low,Close,Volume`; inga split- eller utdelningskolumner. Stooq dokumenterar inte sin justering. Tredjepartskällor beskriver priserna som splitjusterade; om de också är utdelningsjusterade är okänt. Adaptern lagrar därför priserna oförändrade med `price_basis = split_adjusted`, `split_ratio = 1`, `dividend = 0`, `adj_factor = 1`, och as-traded rekonstrueras **inte**. Basen avgörs empiriskt: `scripts/compare_sources.py --a yahoo --b stooq --infer` jämför Stooq mot Yahoo uttryckt som `split_adjusted` respektive `total_return`; den bas som ger minst avvikelse är Stooqs. Om det visar sig vara `total_return` ändras adapterns etikett. Volym: Stooqs volym antas vara splitjusterad på samma sätt som priset (kontrolleras i samma jämförelse). Åtkomst: enligt tredjepartsprofil krävs sedan början av 2026 en API-nyckel från stooq.com (`STOOQ_API_KEY` i `.env`), det finns en opublicerad daglig kvot, och överskriden kvot ger ett felmeddelande med HTTP 200 – adaptern kastar `StooqError` i det fallet i stället för att lagra tomma serier.

| | Yahoo (yfinance) | Stooq | Alpaca (`adjustment=raw`) |
|---|---|---|---|
| Pris i råfil | splitjusterat, ej utdelningsjusterat | justerat, bas odokumenterad | ojusterat (as-traded) |
| Utdelningsjusterad stängning | `Adj Close` | saknas | saknas |
| Splitinformation | `Stock Splits` (kvot på splitdag) | saknas | saknas i bars |
| Utdelningar | `Dividends` | saknas | saknas i bars |
| Volym i råfil | splitjusterad | antas splitjusterad | ojusterad |
| As-traded rekonstruerbar | ja | nej | är redan as-traded |
| Rebas till annan bas | ja | nej | nej (NaN) |
| `price_basis` i lagrad serie | `as_traded` | `split_adjusted` (tills --infer säger annat) | `as_traded` |

**Alpaca (Market Data API v2, `GET /v2/stocks/{symbol}/bars`).** Endast dataendpoints; inga trading-anrop. Nycklar `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` från miljö eller `.env`; paper-nycklar fungerar. Pilotens konfiguration: `timeframe=1Day`, `feed=sip`, `adjustment=raw`, `limit=10000`, `sort=asc`, pagination via `next_page_token`. Alpacas dokumentation: `raw` "applies no adjustments"; övriga värden `split`, `dividend`, `spin-off`, `all` (kombinerbara). Med `raw` är OHLCV as-traded → `price_basis = as_traded`. Bars innehåller ingen split- eller utdelningsinformation, så `split_ratio`, `dividend`, `adj_close`, `adj_factor` lagras som NaN ("okänt") och serien kan **inte** rebasas till `split_adjusted` eller `total_return`; `rebase()` vägrar. Konsekvens för kvalitetskontrollen: en verklig split syns som "unexplained close jump" (WARN) tills splitdata hämtas (Alpacas corporate-actions-endpoint eller Yahoos splitlista – öppen fråga). Tidsstämplar `t` är UTC och konverteras till New York-datum. Felhantering: 429 och 5xx → ny försök med `Retry-After` eller exponentiell backoff, max 5; övriga 4xx → `AlpacaError` direkt; tomt svar (`bars: null/[]`) → tom serie → FAIL "no rows" i pipeline. Nycklar förekommer aldrig i metadata, fel eller loggar. `asof=YYYY-MM-DD` (`ingest.py --asof`) styr Alpacas symbolmappning för omdöpta/avnoterade tickers och används i SIVB-testet.

Jämförelse Yahoo–Alpaca sker på basen `as_traded`: Yahoo är rekonstruerad as-traded, Alpaca är rå. Det är samma bas men olika väg dit; avvikelser avslöjar fel i Yahoos splitlista eller i rekonstruktionen. Yahoo och Alpaca antas **inte** ha samma justeringsbas i andra lägen (Alpacas `split`/`all` är odokumenterade i detalj och används inte).

## 4. Kvalitetskontroller (`quality.py`)

| Kontroll | Nivå |
|---|---|
| Schema, tom serie, mer än en ticker | FAIL |
| Dubblerade datum, osorterat | FAIL |
| Saknade värden i pris/volym | FAIL |
| OHLC-konsistens: priser > 0, `low ≤ min(open, close)`, `high ≥ max(open, close)`, volym ≥ 0 | FAIL |
| Rad på dag som inte är handelsdag enligt NYSE-kalendern (helg, helgdag, särskild stängning) | FAIL |
| Nollvolym | WARN |
| NYSE-handelsdagar utan data (antal och första datum) | WARN |
| Rapporterad split där prisrörelsen inte matchar kvoten (±20 %) | WARN |
| Hopp i close > 40 % utan rapporterad split, **splitliknande**: kvoten nära 1/k eller k (k = 1,5, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 50, 100, ±3 %) och volymen rör sig åt motsatt håll (>1,3×) | WARN |
| Hopp i close > 40 % som **inte** är splitliknande (krasch, squeeze, nyhet) | INFO |

Nivån INFO ändrar inte status: ett stort men internt konsekvent kursfall (SIVB 2023-03-09, −60 %) är inte ett bevisat datafel utan något att granska. För källor utan splitdata (Alpaca raw) betyder WARN "splitliknande, källan saknar splitdata" – en verklig split som behöver kompletteras med splitlista, inte ett fel i priserna.

Handelsdagar kontrolleras mot `exchange_calendars` (XNYS, laddad från 1990), som även innehåller särskilda stängningar (t.ex. 2001-09-11–14, orkanen Sandy 2012). Helgdagar räknas därmed inte längre som saknade dagar. `check_quality(..., calendar=None)` ger den gamla vardagskontrollen för andra marknader tills deras kalenderkod anges.

## 5. Lagring och metadata

```
data/raw/<source>/<TICKER>.parquet                  rå leverantörsdata
data/processed/daily/<source>/<TICKER>.parquet      normaliserad
data/processed/daily/<source>/<TICKER>.meta.json    metadata
```

Metadata: källa, ticker, begärd och faktisk period, hämtningstidpunkt (UTC), leverantörsversion, justeringsmetod, antal rader, kvalitetsstatus och lista över fynd, schemaversion. Allt under `data/` är git-ignorerat.

## 6. Kända begränsningar

- **Survivorship bias:** Yahoo tar bort eller döper om de flesta avnoterade tickers. Ett universum byggt från dagens tickerlistor saknar därför konkurser, uppköp och avlistningar. Pilotens resultat kan inte användas för att skatta strategins edge. Betald källa med avnoterade bolag krävs (Q-002).
- **Avnoterade bolag:** SIVB (Silicon Valley Bank, avnoterad 2023) ger 0 rader från Yahoo (verifierat 2026-10-25). Det visar att Yahoo tar bort *denna* ticker; det bevisar inte att alla avnoterade bolag saknas – Yahoo behåller vissa under omdöpta tickers (t.ex. med suffix) och en del uppköpta bolag finns kvar. Stooq testas på `sivb.us` enligt §8; Stooq har rykte om att behålla fler avnoterade serier, vilket ska verifieras, inte antas. Slutsatsen för projektet står oavsett: inget av dessa universum kan byggas point-in-time utan en lista över historiska noteringar.
- **Point-in-time:** inga noterings- eller avnoteringsdatum, ingen historisk tickeromdöpning, ingen sektor- eller rapportdata. Universumfilter (spec §1) kan inte byggas korrekt på piloten.
- **Datakorrigeringar:** Yahoo ändrar historik retroaktivt (korrigerade splittar, utdelningar). Två hämtningar vid olika tidpunkter kan skilja sig; därför sparas hämtningstidpunkt och rådata.
- **Alpaca – verifierat 2026-10-26 (paper-nycklar, SIP, raw):** dagsdata börjar **2016-01-04** (AAPL, MSFT, NVDA: 2 718 handelsdagar t.o.m. 2026-10-23; begäran från 2000-01-01). Begäran som når in i de senaste 15 minuterna ger **HTTP 403** "subscription does not permit querying recent SIP data"; adaptern kapar därför `end` till gårdagens New York-datum när `end` ≥ dagens (noteras i metadata `notes`). Avnoterad SIVB: **1 808 dagar 2016-01-04–2023-03-09 med `--asof 2023-03-01`**; utan `asof` saknas symbolen. Det visar att Alpaca behåller åtminstone denna avnoterade serie – inte att täckningen är fullständig; en lista över historiska noteringar krävs fortfarande för ett universum.
- **Alpaca – öppet:** exakt rate limit (`X-RateLimit-*`-headrar, ej avläst); vilka trade conditions Alpaca exkluderar vid aggregering av dagsstaplar (påverkar volym, se §10); villkor för kontotypen.
- **Villkor:** se §7.

## 7. Användningsvillkor

- **Yahoo Finance via yfinance.** yfinance är inte knutet till, godkänt av eller granskat av Yahoo; paketet anger att det är avsett för forskning och utbildning och att Yahoos API "is intended for personal use only", med hänvisning till Yahoos villkor. Tolkning för projektet: piloten och privat forskning är förenliga med detta; en kommersiellt driven livebot på Yahoo-data är det sannolikt inte. Yahoo-data får inte vidaredistribueras (inte heller i repot). Projektägaren bör läsa Yahoos aktuella villkor själv – länkar finns i yfinance-paketets README.
- **Alpaca.** Kräver konto och API-nyckel (via `.env`, se `.env.example`). Data via API för kontoinnehavarens eget bruk; vidaredistribution av SIP-data regleras av börsernas avtal. Projektägaren bekräftar aktuella villkor för sin kontotyp (öppen fråga i §8). Inga nycklar i git; `.env` är git-ignorerad.
- **Stooq.** Stooqs egna villkor kunde inte hämtas från Claudes miljö (sajten blockerad). Känt från tredjepartsbeskrivningar: kostnadsfri CSV-nedladdning, daglig kvot, API-nyckel via CAPTCHA sedan 2026. **Projektägaren läser villkoren på stooq.com innan verkliga hämtningar görs** och noterar i §8 om personligt/icke-kommersiellt bruk och vidaredistribution. Tills dess: utvecklingsändamål, låg volym (tre tickers), ingen data i repot.

## 8. Körlogg

Fylls i av projektägaren efter lokala körningar (nätverk saknas i Claudes miljö).

| Datum | Kommando | Resultat |
|---|---|---|
| 2026-10-25 | `verify_split.py --ticker AAPL --split-date 2020-08-31` | As-traded-rekonstruktionen korrekt |
| 2026-10-25 | `ingest.py --source yahoo --tickers AAPL MSFT NVDA --start 2015-01-01` | 2 970 rader vardera, 2015–2026 (schema v1) |
| 2026-10-25 | `ingest.py --source yahoo --tickers SIVB` | 0 rader, FAIL |
| | Läs Stooqs villkor på stooq.com; skaffa nyckel om det krävs (`STOOQ_API_KEY`) | |
| | `ingest.py --source yahoo --tickers AAPL MSFT NVDA SIVB --start 2015-01-01` (schema v2, kör om) | |
| | `ingest.py --source stooq --tickers AAPL MSFT NVDA SIVB --start 2015-01-01` | |
| | `compare_sources.py --a yahoo --b stooq --tickers AAPL MSFT NVDA --infer` | vilken bas matchar? max/mean-avvikelse? |
| | `quality_report.py > data/quality_report.md` | |
| 2026-10-26 | Alpaca: manuellt anrop `/v2/stocks/AAPL/bars`, sip, raw, 2024-01-02..02-01 | 22 staplar, giltiga värden, ingen nästa sida |
| 2026-10-26 | `ingest.py --source alpaca --tickers AAPL MSFT NVDA --start 2000-01-01` | utan `--end`: HTTP 403 (recent SIP); med `--end 2026-10-23`: 2 718 dagar vardera från 2016-01-04 |
| 2026-10-26 | `compare_sources.py --a yahoo --b alpaca --tickers AAPL MSFT NVDA --basis as_traded` | 2 718 gemensamma datum. AAPL max 0,14 % / medel 0,001 % / volym-median 5,6 % / 0 dagar > tol. MSFT max 2,23 % / 0,001 % / 8,8 % / 1 dag (2020-03-18). NVDA 0,11 % / 0,000 % / 3,4 % / 0 |
| 2026-10-26 | `ingest.py --source alpaca --tickers SIVB --asof 2023-03-01` | 1 808 dagar 2016-01-04–2023-03-09; INFO-hopp 2023-03-09 (verkligt fall) |
| 2026-10-26 | Alpaca AAPL/NVDA kvalitet | WARN splitliknande hopp (raw-serie saknar splitdata) – förväntat |
| | `inspect_day.py --ticker MSFT --date 2020-03-18 --sources yahoo alpaca` | råa OHLCV båda källor, se §10.1 |
| | `compare_sources.py --a yahoo --b alpaca --tickers AAPL MSFT NVDA --basis as_traded` (efter denna PR) | volB/volA median och IQR, se §10.2 |
| | Läs Alpacas villkor för kontotypen; notera rate limit ur `X-RateLimit-*` | |

## 10. Pågående undersökningar

### 10.1 MSFT 2020-03-18: 2,23 % skillnad i close

Fakta: en enda dag av 2 718 avviker mer än 0,5 %; medelavvikelsen är 0,001 %. 2020-03-18 var en av de mest volatila dagarna under covid-kraschen med extremt intradagsintervall. Hypoteser som **inte** antas utan kontrolleras med `scripts/inspect_day.py`: (a) olika definition av dagens stängningskurs – officiellt stängningsauktionspris mot sista SIP-print; (b) en sen korrigerad print (trade correction) som en källa tagit med; (c) ett datafel i endera källan. Avgörande test: om Alpacas `h`/`l` innehåller Yahoos close och vice versa, och om open nästa dag ligger närmare den ena källans close. Observationer fylls i här efter körningen. Tills dess: ingen källa markeras som fel; dagen flaggas i jämförelsen och exkluderas inte.

### 10.2 Volym: Yahoo mot Alpaca

Fakta: medianavvikelsen i volym är 3–9 % per aktie medan priserna matchar på 0,001 %. Det är alltså en systematisk definitionsskillnad, inte brus. Trolig orsak (hypotes): källorna aggregerar olika trade conditions – t.ex. udda poster, sena/korrigerade prints, prints utanför ordinarie handel. Alpaca dokumenterar en lista över exkluderade conditions för stapelaggregering; Yahoos definition är odokumenterad. `compare_sources.py` rapporterar nu `volB/volA` median och IQR: ett smalt IQR runt ett värde < 1 bekräftar en stabil faktor, ett brett IQR betyder dagvarierande skillnader.

Konsekvens för strategin: spec §1 (likviditetsfilter) och eventuella volymregler i §4 använder **relativa** mått (volym mot egen 20-dagars medelvolym, dollaromsättning över tröskel). En stabil faktor på 0,9 påverkar sådana mått marginellt; blandning av källor gör det inte. Regel: **volym tas alltid från en och samma källa inom en körning**, och källan anges i resultatet. Vilken källa som blir pilotens volymkälla avgörs av IQR-resultatet (föreslaget D-010).

## 9. Nästa steg

1. Projektägaren kör §8 (Stooq-raderna) och fyller i utfallet. Om `--infer` pekar på `total_return` ändras Stooq-adapterns `price_basis`.
2. Kör §10.1 och §10.2; fyll i observationerna. Därefter beslut D-010 (volymkälla) och om splitdata ska hämtas till Alpaca-serier.
3. Beslut Q-002 utifrån pilotens fynd och fas 1-researchen. Pilotens tydligaste resultat hittills: ingen kostnadsfri källa ger avnoterade bolag eller point-in-time-universum; det är det som ett betalt abonnemang måste köpa.
