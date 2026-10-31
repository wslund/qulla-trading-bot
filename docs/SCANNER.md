# SCANNER – regelbaserad scanner

**Version:** steg 3c (dagsmodellens trigger), 2026-10-31. Följer `STRATEGY_SPEC.md` v0.2.1 §2–3. Ingen trigger, inga utfall, ingen positionslogik.

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

**Diagnostik (inte regler), `scanner/diagnostics.py`:**
- *Prior move start → topp.* Startpunkten (lägsta stängning i 63-dagarsfönstret före toppen) och toppen markeras, med den faktiskt beräknade procenten. Värdet räknas om ur serien t.o.m. T−1 med §2-definitionen och jämförs med det lagrade `prior_move_pct`; avviker de skrivs `!! differs from stored` i rutan. Fönstrets startdatum visas när prior move saknas.
- *close vs SMA10* och *5-bar change*: korttidsmomentum vid T−1. Diagnostiska mått för hypotes P-002a; de blockerar inget.
- *Setup-grupper.* Kandidatrader som följer på varandra i tabellen och delar `prior_move_peak_date` får samma `setup_group_id` (`TICKER:peak<datum>:from<datum>`), `group_rank` och `group_size`. Kolumnerna är additiva i kandidattabellen; regelkolumner och `is_candidate` rörs inte. `scan.py` skriver antal grupper, och kandidatmetadata får `setup_groups`. En kandidat per konsolidering är en §4-fråga (steg 3), inte filtrets.

Alla diagnostiska mått använder bara staplar ≤ T−1; `tests/test_diagnostics.py` muterar T och senare och kräver oförändrade värden.

Syfte: kvalitativ granskning av att reglerna fångar det slags formationer specen beskriver – inte utvärdering av avkastning. Observationer från granskningen dokumenteras som hypoteser under P-002 i `STRATEGY_SPEC.md` §13. Bilder hamnar under `data/plots/` (git-ignorerat).

## 9. Steg 3a – ordernivåer (`scanner/levels.py`)

`compute_levels(series, row)` ger en frusen `OrderLevels` per beslutsrad: `breakout_level_at` (högsta `high` i fönstret (topp, T−1]), `low_prev_at` (`low[T−1]`), `adr20`, fönsterdatum, `split_ratio_on_t`, `split_on_entry_day`, `units="as_traded"`. Regler (STEP3_DESIGN §2, D-013/D-014):

- Bara staplar ≤ T−1 används för priser. Serien skärs vid `asof_date` och rebasas; eftersom inga senare splittar finns i den skurna serien blir värdena automatiskt i T−1:s aktieenheter – en split *inuti* fönstret hanteras utan separat skalfaktor (test: 2:1 fem dagar före T ger osplittad nivå / 2, medan naivt max över råa as-traded-high hade blivit fel).
- Split med ex-dag T delar alla nivåer med `split_ratio[T]` och sätter `split_on_entry_day` – antagandet att ex-dagen var känd före öppning kan inte verifieras ur data och flaggas därför. Split på T+1 påverkar inte T.
- Vägrar `price_basis ≠ as_traded`, NaN i `split_ratio`, saknad topp, tom fönster eller saknad T−1-stapel. `plot.py` importerar inte modulen; diagrammets splitjusterade nivåer kan inte bli ordernivåer.
- Operatorer (D-013): `breakout_triggered(high, level)` = `high > level` (tangering räknas inte), `stop_hit(low, stop)` = `low ≤ stop`, `gap_exceeds(open, level, max_gap)` = `open > level × (1 + max_gap)` (exakt gränsen tillåten). Gränsvärdestestade.
- `compute_levels_table(series, candidates)` ger en rad per kandidatrad med `levels_error` där nivåer saknas (de första ~63 raderna). Ingen lagring i v1 – nivåerna är billiga och deterministiska och beräknas i signalsteget.

Ingen trigger, fyllning, risk eller tillstånd här; det är 3b–3d.

## 10. Steg 3b – setup-tillstånd och dedupe (`signals/state.py`)

`SetupRegistry` håller tillstånd per key `(ticker, prior_move_peak_date)` enligt D-014, för en hel körning över alla tickers.

- `decide(row)` för en kandidat-/featurerad på dag T ger `Decision(allow_order, reason, status)`. Ordningen på avslag: `no_peak` → `not_candidate` → `key_consumed` → `position_open`; annars `armed`. ARMED är en egenskap per dag: en dag som fäller en regel ger `not_candidate` utan att något återställs, och samma key är `armed` igen nästa kandidatdag.
- `record_fill(key, date)` förbrukar keyn permanent och öppnar tickerns position (en per ticker). `record_no_fill` loggar en utebliven trigger utan att förbruka. `record_close` stänger positionen; keyn förblir förbrukad (`CLOSED`). `record_expiry` är bara logg (`EXPIRED`) för en armerad key som aldrig fylldes.
- Ogiltiga övergångar (fyllning på förbrukad key, fyllning utan ARMED-beslut, fyllning med öppen position, stängning utan position, expiry av förbrukad key) ger `InvalidTransition` – de är programfel uppströms, inte data.
- Varje övergång loggas (`Transition`: datum, ticker, key, från, till, orsak); `log_frame()` ger spårbarheten som tabell. `walk_candidates(cands, fill_dates, close_dates)` spelar upp en kandidattabell med skriptade fyllningar/stängningar – för tester och torrkörning; 3c ersätter skriptet med verklig trigger-utvärdering.
- Dedupe-nyckeln är toppen, inte `setup_group_id`: en kandidatsekvens som bryts av en underkänd dag får två grupp-id:n men en key (testat mot scannerns verkliga utdata).

Tester (12): fem kandidatdagar med fyllning dag 3 → dag 4–5 `key_consumed`; utebliven trigger förbrukar inte; lucka i sekvensen behåller keyn; stoppad position blockerar samma key för alltid men inte ny key; ny topp under öppen position → `position_open` utan förbrukning, `armed` efter stängning; tre formationer under samma topp → en affär; rader utan topp; två tickers oberoende; alla ogiltiga övergångar; expiry; avslagsordning; verklig kandidattabell med bruten grupp.

## 11. Steg 3c – `daily_stop_entry` (`signals/daily_trigger.py`)

Spec §5.2/§6 och D-013 som kod, med tre skilda objekt:

- **`Signal`** – känt före öppning dag T: key, `breakout_level`, `low_prev`, `adr20`, `split_on_entry_day` (allt från `compute_levels`, dvs. staplar ≤ T−1) och `TriggerParams` (`max_gap_entry` 0,05, `slippage_bps` 10 som **scenario**, `min_stop_pct` 0,01). Byggs bara från ett `armed`-beslut i registret.
- **`evaluate_day(signal, open, high, low)`** → `TriggerOutcome` med kod `gap_too_large` (`open > level × 1,05`), `no_trigger` (`high ≤ level`), `stop_invalid` (`stop ≥ fill` eller avstånd < `min_stop_pct`) eller `fill_candidate`. Funktionen får bara T:s open/high/low; inget annat finns att läsa.
- **`FillCandidate`** – antagen fyllning, status `PRELIMINARY`: `fill = (open om open > level annars level) × (1 + slip)` med `gap_fill`-flagga; `stop = max(low_prev, fill × (1 − adr20))`; `ambiguous_same_day = low[T] ≤ stop` med konservativt stopp-pris `stop × (1 − slip)`.
- **`approve_and_record(registry, outcome, approved, reason)`** är enda vägen till `record_fill`. Godkännandet kommer från 3d (risk/sizing) – tills dess är default-godkännaren `pending_risk_approval`, som aldrig godkänner: fyllningskandidater loggas som `pending_risk_approval` och **förbrukar inte keyn**. `approve_all_for_tests` är en uttrycklig teststubb. Icke-fyllningar (`no_trigger`, `gap_too_large`, `stop_invalid`) loggas via `record_no_fill` oavsett godkännande. En godkänd fyllning med `ambiguous_same_day` stängs samma dag i registret (`filled_stopped_same_day`) – den enda exit som modelleras här.
- **`walk_daily(series, cands, params, approver, close_dates)`** spelar upp en ticker dag för dag: skriptade stängningar först (exit-order fylls vid öppningen, §6.1), därefter beslut → nivåer → trigger → godkännande. Ger en spårbar tabell per kandidatrad (beslut, utfall, slutkod, nivå, fyllning, stop, flaggor).

**Inte i 3c:** exits efter entrydagen (stop-gap med förlust > 1R, delvinst, trailing) – 3f; positionsstorlek, kostnader och acceptans på antagen fyllning – 3d; ORB – 3e.

Observation från den syntetiska formationen: i en stigande konsolidering överskrids fönstrets högsta nästan varje dag, så första kandidatdagen triggar direkt. Det är en egenskap hos "högsta high i fönstret" som nivå, värd att titta på i verkliga kandidater (3f-rapporten visar hur många dagar efter första kandidatdagen fyllningen sker).

Tester (20, totalt 161): operatorer och gränser, fyllning vid nivå/öppning, ADR-tak på stoppen, för snäv stop, samma-dagsstopp flaggad och prissatt, fyllningskandidat förbrukar aldrig key utan godkännande, no_trigger/gap förbrukar inte, godkänd fyllning förbrukar och öppnar position, tvetydig godkänd fyllning stängs, godkännande utan ARMED är ogiltigt, `walk_daily` default/stubb/gap/krasch, mutation av framtida staplar och split efter T, split på T skalar nivå/fyllning/stop.

## 12. Nästa steg (3d–3f)

Breakout-trigger enligt spec §4 (`high[T] > breakout_level`) och entry-modellen `daily_stop_entry` (§5.2), med en kandidat per konsolidering. Kräver beslut om §12-nivåer före första backtest och ett point-in-time-universum (Q-002) för trovärdiga resultat. Kalibrering av P-002: välj 10–20 handplockade historiska formationer och kontrollera att de blir kandidater; justera trösklar med dokumenterad motivering.
