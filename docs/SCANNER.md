# SCANNER – regelbaserad scanner

**Version:** steg 2 + visuell validering, 2026-10-27. Följer `STRATEGY_SPEC.md` v0.2.1 §2–3. Ingen trigger, inga utfall, ingen positionslogik.

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

`date` (T), `asof_date` (T−1), `ticker`, `source`, `input_price_basis`, `close_prev`, `sma10/20/50` (nivåer i samma enheter som `close_prev`), `sma10/20/50_slope` (SMA[T−1]/SMA[T−11] − 1), `close_vs_sma20/50`, `adr20`, `dollar_volume_20d` (featureversion 1.1, additiv; median av close × volym i as-traded-enheter, för §1-filtret), `prior_move_pct` (§2), `prior_move_peak_date`, `consol_days`, `consol_tightness` (§3, median av range5 sista/första tredjedel; NaN om fönstret < 12 dagar), `consol_higher_lows`, `consol_depth`, `consol_pct_below_high`, `consol_closes_below_sma20`, `spec_version`, `feature_version`.

Konsolideringsfönstret är (toppdag, T−1]. Toppen är första förekomsten av max close i `prior_move_window` (63). Ligger toppen i fönstrets första stapel är `prior_move_pct` NaN (ingen "före"-del).

## 5. Metadata

`.meta.json`: källa, ticker, indatans prisbas, rader och täckning, indatans kvalitetsstatus och hämtningstid, `splits_from`, antal featurerader, första datum med kompletta features (SMA50, ADR20 och prior move), parametrar, spec- och featureversion, byggtid, förbehåll.

## 6. Körning

```bash
uv run python scripts/build_features.py --source yahoo --tickers AAPL MSFT NVDA
uv run python scripts/build_features.py --source alpaca --tickers AAPL MSFT NVDA --splits-from yahoo
```

## 7. Steg 2 – regelbaserad kandidatfiltrering

`scanner/rules.py` tillämpar spec §1–3 på featuretabellen. Varje regel läser bara raden daterad T (som redan följer T−1-konventionen); ingen ny look-ahead införs. En regel vars indata saknas (NaN/None) är **underkänd**, aldrig godkänd, och regelnamnet skrivs i `missing_rules`. Avstängda regler (`None`) räknas inte i `rules_total`. Kandidat = alla aktiva regler godkända.

| Regel | Villkor (spec) | Tröskel i `Thresholds` | Tagg |
|---|---|---|---|
| `prior_move` | `prior_move_pct ≥ min_prior_move` | 0,30 | `[T]` §2, värdet är nedre gränsen i `[Q]` "30–100 %+" |
| `trend` | `close[T−1] > SMA50` och `SMA50[T−1] > SMA50[T−11]` | på | `[T]` §2 |
| `consol_length` | `consol_days ∈ [12, 45]` | 12, 45 | `[T]` §3, ur `[Q]` "2 veckor–2 månader" |
| `tightness` | `consol_tightness ≤ tightness_ratio` | 0,6 | `[T]` §3, P-002 |
| `higher_lows` | `consol_higher_lows` | på | `[T]` §3, ur `[Q]` "higher lows" |
| `depth` | `consol_depth ≤ max_consol_depth` | 0,25 | `[T]` §3, P-002 |
| `position` | `consol_pct_below_high ≤ max_pct_below_high` | 0,10 | `[T]` §3, P-002 |
| `closes_below_sma20` | `≤ max_closes_below_sma20` | 3 | `[T]` §3, P-002 |
| `above_sma20` | `close[T−1] > SMA20` | på | `[T]` §3 |
| `adr` | `min_adr ≤ adr20 ≤ max_adr` | **av** | `[H]` – källan säger uttryckligen att ADR inte används för urval; finns för test |
| `price` | `close_prev ≥ min_price` | **av** | `[T]` §1, P-001 – specen anger inget värde |
| `liquidity` | `dollar_volume_20d ≥ min_dollar_volume` | **av** | `[T]` §1, P-001 – specen anger inget värde |

Inga nya tröskelvärden har införts; P-001/P-002-värdena är specens startförslag och kalibreras mot handplockade exempel innan de blir beslut. `[H]`- och P-001-regler slås på med `scripts/scan.py --set min_adr=0.03` etc.

Utdata per rad: alla features + `rule_<namn>` (True/False/NA), `rules_passed`, `rules_total`, `missing_rules`, `is_candidate`, `candidate_id` (`TICKER:datum:spec0.2.1:rules1`, bara för kandidater), `rules_version`. Lagras i `data/candidates/<source>/<TICKER>.parquet` + `.meta.json` med källa, prisbas, indatans kvalitetsstatus (FAIL vägras, WARN noteras), täckning, `splits_from`, feature- och regelversion, specversion, alla trösklar med taggar, antal kandidater och godkännandegrad per regel.

Observation från syntetiska tester: en formation som uppfyller alla villkor ger en kandidat **varje dag** så länge den håller (i testet 9 dagar i rad). Steg 3 (trigger, spec §4) avgör vilken dag som räknas; dedupe per konsolidering ("en kandidat triggas bara en gång", spec §4) hör dit.

CLI:
```bash
uv run python scripts/scan.py --source yahoo --tickers AAPL MSFT NVDA --date 2020-08-31
uv run python scripts/scan.py --source yahoo --tickers AAPL MSFT NVDA --start 2020-01-01 --end 2020-12-31 --near-misses
uv run python scripts/scan.py --source alpaca --tickers AAPL --splits-from yahoo --start 2024-01-01 --set min_adr=0.03
```

## 8. Visuell setup-validering (`scanner/plot.py`)

`scripts/plot_setup.py` ritar en statisk PNG per beslutsrad: OHLC-staplar och volym (`lookback` 120 staplar före, `lookahead` 20 efter), SMA10/20/50, konsolideringsfönstret (grönt), prior-move-toppen (lila), breakoutnivån (fönstrets högsta, streckad), beslutsdagen T (röd linje), samt en ruta med PASS/FAIL per aktiv regel och feature-värdena. Kräver `uv sync --extra viz` (matplotlib) och lagrade kandidater.

Regler för vad bilden får visa:
- Allt som markerar setupen (SMA-linjer, fönster, topp, breakoutnivå) stannar vid T−1. Staplar från T och framåt ritas grå som kontext och används inte för någon markering. Testat: `sma_last_date == asof_date`, `consol_end == asof_date`.
- Feature-värden och regelutfall tas ur kandidattabellen som de är; inget räknas om i ritkoden, så bilden kan inte motsäga scannern.
- Diagrammet ritas på splitjusterade priser (kontinuerligt över splittar); `close_prev` och SMA-nivåer i rubriken är i as-traded-enheter för T−1 och skalfaktorn visas.
- `--near-misses` tar med rader som fallerar exakt en regel; titeln anger `near miss (8/9)` och rutan visar vilken.

Syfte: kvalitativ granskning av att reglerna fångar det slags formationer specen beskriver – inte utvärdering av avkastning. Bilder hamnar under `data/plots/` (git-ignorerat).

## 9. Nästa steg (steg 3)

Breakout-trigger enligt spec §4 (`high[T] > breakout_level`) och entry-modellen `daily_stop_entry` (§5.2), med en kandidat per konsolidering. Kräver beslut om §12-nivåer före första backtest och ett point-in-time-universum (Q-002) för trovärdiga resultat. Kalibrering av P-002: välj 10–20 handplockade historiska formationer och kontrollera att de blir kandidater; justera trösklar med dokumenterad motivering.
