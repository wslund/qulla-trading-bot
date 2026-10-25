# DATA_PILOT – leverantörsoberoende datapilot

**Version:** 0.1 (2026-10-25). Syfte: verifiera insamling, kvalitet och format för dagsdata från kostnadsfria källor innan ett betalt abonnemang väljs (Q-002). Ingen strategi- eller ML-kod.

> **Pilotdatasetet är inte verifierat survivorship-fritt.** Kostnadsfria källor levererar i huvudsak bolag som fortfarande är noterade. Data från piloten får användas för att testa pipelinen och jämföra källor, **inte** för definitiva lönsamhetsanspråk om strategin. Se §6.

## 1. Arkitektur

```
src/tradingbot/data/
├── schema.py          normaliserat schema (NORMALIZED_COLUMNS) och FetchMeta
├── providers/
│   ├── base.py        MarketDataProvider: fetch_daily() → RawBars, normalize() → DataFrame
│   └── yahoo.py       yfinance-adapter (klar)
├── quality.py         check_quality() → QualityReport (OK / WARN / FAIL)
├── store.py           ParquetStore: raw, processed, metadata
├── compare.py         compare_series(): två källor, samma ticker
└── pipeline.py        process() (offline-testbar) och ingest() (nätverk)
scripts/ingest.py, compare_sources.py, verify_split.py
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

Motiv: as-traded behövs för realistisk simulering (hela aktier, kostnader, stoppnivåer); justerade serier behövs för indikatorer över splittar. Med `adj_factor` lagras båda utan dubbla kolumner.

## 3. Justeringsmetod per källa

**Yahoo (yfinance, `auto_adjust=False`, `actions=True`).** Yahoo levererar inte as-traded priser: `Open/High/Low/Close/Volume` är splitjusterade bakåt (ej utdelningsjusterade), `Adj Close` är split- och utdelningsjusterad, `Stock Splits` anger kvoten på splitdagen. Adaptern rekonstruerar as-traded genom att multiplicera varje pris med produkten av alla **senare** splitkvoter och dividera volymen med samma faktor. Begränsningar: rekonstruktionen är bara så bra som Yahoos splitlista; omvända splittar och saknade splittar ger fel som kvalitetskontrollen försöker fånga (§4). Antagandet om Yahoos semantik ska bekräftas lokalt med `scripts/verify_split.py --ticker AAPL --split-date 2020-08-31` (förväntat: as-traded ≈ 500 USD före, ≈ 129 efter; Yahoo-rå ≈ 125 på båda sidor). yfinance-versionen sparas i metadata eftersom beteendet har ändrats mellan versioner.

**Alpaca, Stooq:** ej implementerade. Alpaca levererar råa (ojusterade) priser med valbar justering via parameter; Stooq levererar justerade priser utan separat splitinformation. Dokumenteras när adaptrarna byggs.

## 4. Kvalitetskontroller (`quality.py`)

| Kontroll | Nivå |
|---|---|
| Schema, tom serie, mer än en ticker | FAIL |
| Dubblerade datum, osorterat | FAIL |
| Saknade värden i pris/volym | FAIL |
| OHLC-konsistens: priser > 0, `low ≤ min(open, close)`, `high ≥ max(open, close)`, volym ≥ 0 | FAIL |
| Datum på helg | FAIL |
| Nollvolym | WARN |
| Saknade vardagar (antal) och luckor > 4 vardagar | WARN |
| Rapporterad split där prisrörelsen inte matchar kvoten (±20 %) | WARN |
| Oförklarat hopp i as-traded close > 40 % utan rapporterad split | WARN |

Ingen börskalender används i piloten; helgdagar räknas som saknade vardagar och rapporteras som WARN med antal. En kalender (t.ex. `exchange_calendars`) läggs till när det behövs för point-in-time-universum.

## 5. Lagring och metadata

```
data/raw/<source>/<TICKER>.parquet                  rå leverantörsdata
data/processed/daily/<source>/<TICKER>.parquet      normaliserad
data/processed/daily/<source>/<TICKER>.meta.json    metadata
```

Metadata: källa, ticker, begärd och faktisk period, hämtningstidpunkt (UTC), leverantörsversion, justeringsmetod, antal rader, kvalitetsstatus och lista över fynd, schemaversion. Allt under `data/` är git-ignorerat.

## 6. Kända begränsningar

- **Survivorship bias:** Yahoo tar bort eller döper om de flesta avnoterade tickers. Ett universum byggt från dagens tickerlistor saknar därför konkurser, uppköp och avlistningar. Pilotens resultat kan inte användas för att skatta strategins edge. Betald källa med avnoterade bolag krävs (Q-002).
- **Avnoterade bolag, test:** kör `scripts/ingest.py --tickers <avnoterad ticker>` och dokumentera utfallet i §8. Förväntat: tom serie (FAIL "no rows") eller partiell historik.
- **Point-in-time:** inga noterings- eller avnoteringsdatum, ingen historisk tickeromdöpning, ingen sektor- eller rapportdata. Universumfilter (spec §1) kan inte byggas korrekt på piloten.
- **Datakorrigeringar:** Yahoo ändrar historik retroaktivt (korrigerade splittar, utdelningar). Två hämtningar vid olika tidpunkter kan skilja sig; därför sparas hämtningstidpunkt och rådata.
- **Villkor:** se §7.

## 7. Användningsvillkor

- **Yahoo Finance via yfinance.** yfinance är inte knutet till, godkänt av eller granskat av Yahoo; paketet anger att det är avsett för forskning och utbildning och att Yahoos API "is intended for personal use only", med hänvisning till Yahoos villkor. Tolkning för projektet: piloten och privat forskning är förenliga med detta; en kommersiellt driven livebot på Yahoo-data är det sannolikt inte. Yahoo-data får inte vidaredistribueras (inte heller i repot). Projektägaren bör läsa Yahoos aktuella villkor själv – länkar finns i yfinance-paketets README.
- **Alpaca.** Kräver konto och API-nyckel (via `.env`, se `.env.example`). Villkor läses när adaptern byggs.
- **Stooq.** Kostnadsfri nedladdning för personligt bruk med begränsningar; villkor läses när adaptern byggs.

## 8. Körlogg

Fylls i av projektägaren efter lokala körningar (nätverk saknas i Claudes miljö). Mall:

| Datum | Kommando | Resultat |
|---|---|---|
| | `scripts/verify_split.py --ticker AAPL --split-date 2020-08-31` | |
| | `scripts/ingest.py --source yahoo --tickers AAPL MSFT NVDA --start 2015-01-01` | |
| | `scripts/ingest.py --source yahoo --tickers <avnoterad>` | |
| | `scripts/compare_sources.py --a yahoo --b <källa 2> --tickers AAPL MSFT` | kräver andra adaptern |

## 9. Nästa steg

1. Projektägaren kör §8 lokalt och fyller i utfallet; avvikelser blir issues.
2. Andra adaptern (Stooq – ingen nyckel, enklast) så att jämförelsetestet kan köras på riktigt. Därefter Alpaca.
3. Beslut Q-002 utifrån pilotens fynd och fas 1-researchen.
