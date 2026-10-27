# qulla-trading-bot

Automatiserad tradingbot baserad på Kristjan Kullamägis (Qullamaggie) breakout-strategi.
Målet är långsiktigt positiv nettoavkastning efter alla kostnader, med strikt money management.

**Status:** Fas 2 klar (strategispec och riskmodell). Datapilot pågår (`docs/DATA_PILOT.md`). Ingen tradinglogik, ML eller mäklarkod finns ännu.

## Komma igång lokalt

Kräver [uv](https://docs.astral.sh/uv/) och git. Python 3.12 laddas ner automatiskt av uv om den saknas.

```bash
uv sync --all-extras         # skapar .venv och installerar beroenden (dev, yfinance, matplotlib)
uv run pytest                # kör tester
uv run ruff check .          # lint
uv run ruff format --check . # formateringskontroll (uv run ruff format . rättar)
```

Datahämtning (kräver nätverk, se `docs/DATA_PILOT.md`):

```bash
uv run python scripts/ingest.py --source yahoo --tickers AAPL MSFT --start 2015-01-01
uv run python scripts/ingest.py --source stooq --tickers AAPL MSFT --start 2015-01-01
uv run python scripts/ingest.py --source alpaca --tickers AAPL MSFT --start 2016-01-01   # nycklar i .env
uv run python scripts/compare_sources.py --a yahoo --b alpaca --tickers AAPL MSFT --basis as_traded
uv run python scripts/inspect_day.py --ticker MSFT --date 2020-03-18
uv run python scripts/build_features.py --source yahoo --tickers AAPL MSFT NVDA
uv run python scripts/scan.py --source yahoo --tickers AAPL MSFT NVDA --start 2020-01-01 --end 2020-12-31
uv run python scripts/plot_setup.py --source yahoo --ticker NVDA --dates 2016-08-31 2017-06-27
uv run python scripts/quality_report.py
```

Samma tre kontroller körs i CI (`.github/workflows/ci.yml`) vid push till `main` och vid pull requests.

## Struktur

```
qulla-trading-bot/
├── pyproject.toml            # paket, beroenden, Ruff- och pytest-konfig
├── uv.lock                   # låsta versioner (committas)
├── .python-version           # 3.12
├── .env.example              # mall för hemligheter (.env är git-ignorerad)
├── .github/workflows/ci.yml  # Ruff + pytest
├── docs/
│   ├── PROJECT_MASTER.md     # långsiktigt minne: mål, krav, arkitektur, arbetssätt
│   ├── SESSION_HANDOFF.md    # aktuell status och nästa steg; startar varje ny AI-chatt
│   ├── STRATEGY_SPEC.md      # mekanisk strategispecifikation, versionerad
│   ├── RISK_MODEL.md         # money management: principer, regler, testparametrar
│   ├── DATA_PILOT.md         # datapilot: arkitektur, justering, kvalitet, begränsningar
│   ├── SCANNER.md            # scanner: features (steg 1), regler (steg 2), tidskonvention
│   └── DECISIONS.md          # beslutslogg och öppna frågor
├── src/tradingbot/
│   ├── data/                 # hämtning, Parquet, point-in-time, kvalitet
│   ├── scanner/              # features (steg 1), kandidatfiltrering (steg 2), setup-bilder
│   ├── backtesting/          # simulering, kostnader, R-multiplar, walk-forward
│   └── risk/                 # positionsstorlek, exponeringsgränser
├── configs/                  # YAML: universum, kostnader, strategiparametrar
├── scripts/                  # CLI-ingångar: ingest, scan, backtest
├── tests/
├── research/                 # experiment och externt material – importeras aldrig av src/
└── data/                     # git-ignorerad (utom README och .gitkeep)
```

`ml/` och `brokers/` skapas när respektive fas börjar.

## Konventioner

- Dokumentation, commit-meddelanden och kommentarer i konfigfiler: svenska.
- Kod, identifierare och docstrings: engelska.
- Inga datafiler, modellvikter eller nycklar i git.
- En gren per uppgift, pull request mot `main`, grön CI före merge.

Läs `docs/PROJECT_MASTER.md` för helheten och `docs/SESSION_HANDOFF.md` för aktuellt läge.
