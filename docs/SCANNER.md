# SCANNER – regelbaserad scanner

**Version:** steg 1 (features), 2026-10-27. Följer `STRATEGY_SPEC.md` v0.2.1 §2–3. Ingen trigger, inga utfall, ingen positionslogik.

> Features beräknade på tre–fyra överlevande tickers är ett tekniskt funktionstest. Point-in-time-universum saknas (DATA_PILOT §6); inget här är bevis för strategins lönsamhet.

## 1. Vad steg 1 gör

`scanner/features.py` beräknar per ticker en rad per handelsdag T med allt som spec §2–3 behöver, strikt på data t.o.m. T−1. `scanner/pipeline.py` läser en lagrad serie, beräknar och skriver `data/features/<source>/<TICKER>.parquet` + `.meta.json`. Trösklar (`min_prior_move`, `tightness_ratio`, `max_consol_depth` …) tillämpas **inte** här; det är steg 2 (setup-detektion), så att samma featuretabell kan användas för kalibrering mot handplockade exempel och senare som ML-indata (PROJECT_MASTER §3).

## 2. Tidskonvention och look-ahead

Raden daterad T använder staplar ≤ T−1 (`asof_date`). Utdata har `len(serie) − 1` rader: sista stapeln ger raden för nästa handelsdag, vars datum inte är känt utan kalender och därför utelämnas.

Splittar är den subtila läckan: bakåtjusterade serier skalar om *tidigare* staplar med *senare* splittar. Därför är varje feature antingen en kvot (slopes, ADR, prior move, konsolideringsmått) eller uttryckt i T−1:s egna prisenheter (`close_prev`, `sma10/20/50`). En split på T eller senare skalar alla staplar ≤ T−1 lika och lämnar varje feature för T oförändrad. `tests/test_features.py` bevisar det genom att mutera priser, volym och splittar på T och senare och kräva identiska rader ≤ T.

## 3. Prisbas och splittar

| Indata `price_basis` | Hantering |
|---|---|
| `as_traded` med känd `split_ratio` (Yahoo) | rebasas till `split_adjusted` för beräkning; nivåer skalas tillbaka till as-traded-enheter per rad |
| `as_traded` med `split_ratio` NaN (Alpaca raw) | **vägras** – `attach_splits()` kopierar splitlista från annan källa (`--splits-from yahoo`), med krav på samma ticker, full datumtäckning och att splitdagar finns i målserien |
| `split_adjusted` (Stooq) | används som den är; `close_prev` och SMA är då splitjusterade, inte as-traded |
| `total_return` | vägras (utdelningsjusterade nivåer) |

## 4. Kolumner

`date` (T), `asof_date` (T−1), `ticker`, `source`, `input_price_basis`, `close_prev`, `sma10/20/50` (nivåer i samma enheter som `close_prev`), `sma10/20/50_slope` (SMA[T−1]/SMA[T−11] − 1), `close_vs_sma20/50`, `adr20`, `prior_move_pct` (§2), `prior_move_peak_date`, `consol_days`, `consol_tightness` (§3, median av range5 sista/första tredjedel; NaN om fönstret < 12 dagar), `consol_higher_lows`, `consol_depth`, `consol_pct_below_high`, `consol_closes_below_sma20`, `spec_version`, `feature_version`.

Konsolideringsfönstret är (toppdag, T−1]. Toppen är första förekomsten av max close i `prior_move_window` (63). Ligger toppen i fönstrets första stapel är `prior_move_pct` NaN (ingen "före"-del).

## 5. Metadata

`.meta.json`: källa, ticker, indatans prisbas, rader och täckning, indatans kvalitetsstatus och hämtningstid, `splits_from`, antal featurerader, första datum med kompletta features (SMA50, ADR20 och prior move), parametrar, spec- och featureversion, byggtid, förbehåll.

## 6. Körning

```bash
uv run python scripts/build_features.py --source yahoo --tickers AAPL MSFT NVDA
uv run python scripts/build_features.py --source alpaca --tickers AAPL MSFT NVDA --splits-from yahoo
```

## 7. Nästa steg (steg 2)

Setup-detektion: tillämpa spec §2–3-trösklar på featuretabellen, skriv kandidater med `candidate_id`; kalibrera P-002 mot handplockade historiska exempel. Kräver inget nytt i datalagret men blir meningsfullt först med ett point-in-time-universum (Q-002).
