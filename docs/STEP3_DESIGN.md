# Scanner steg 3 – teknisk design: trigger, ORB, dedupe, risk

**Status:** designutkast v0.2, 2026-10-27, reviderad efter intradagsverifiering och projektägarens granskning (åtta punkter); ingen implementation. Bygger på `STRATEGY_SPEC.md` v0.2.1 §4–8, §11–12, `RISK_MODEL.md` v0.2, `SCANNER.md` steg 1–2, `DATA_PILOT.md`. Taggar som i specen: `[Q]` källa, `[T]` tolkning, `[H]` hypotes. **Designval** markeras `[D]` och är förslag som granskas innan kod skrivs.

> Steg 3 producerar *signaler och simulerade affärer*, inte order. Resultat från steg 3 är fortfarande ett funktionstest på överlevande aktier utan point-in-time-universum (DATA_PILOT §6) och får inte kallas edge.

## 0. Avgränsning och vad som redan finns

Finns: dagsserier as-traded (Yahoo rekonstruerad, Alpaca raw) med splitlista, featuretabell (T−1), kandidattabell med regelutfall och `setup_group_id`, kvalitetskontroll, kostnads- och riskmodell på pappret.
Saknas: breakoutnivå i as-traded-enheter per kandidatdag, tillståndsmaskin per setup, intradagsdata, utfallsberäkning, kostnads- och riskmotor i kod.
Byggs inte här: orderläggning mot mäklare, fullständig backtestmotor (walk-forward, rapportering enligt §11), ML.

## 1. Modulstruktur `[D]`

```
src/tradingbot/
├── scanner/
│   └── levels.py          breakout_level/stop-referens i as-traded-enheter för dag T   (steg 3a)
├── signals/
│   ├── state.py           SetupState: ARMED → TRIGGERED → CLOSED/EXPIRED, en per setup-grupp (3b)
│   ├── daily_trigger.py   daily_stop_entry: trigger, fyllning, stop, samma-dagsstopp (§5.2, §6.1) (3c)
│   └── orb.py             orb_entry: opening range, trigger, fyllning, LOD-stop (§5.1, §6)       (3e)
├── risk/
│   ├── sizing.py          position_size(), rena funktioner enligt RISK_MODEL §2.1              (3d)
│   ├── costs.py           CostModel: courtage, slippage, FX; per fyllning                     (3d)
│   └── limits.py          allow_entry(): portföljgränser §2.2 (bara det som behövs för en ticker i taget)
├── data/
│   └── providers/alpaca.py  fetch_minute() (3e) – samma adapter, timeframe=1Min
└── backtesting/
    └── trades.py          Trade-post: entry, stop, exits enligt §8, R-multipel, kostnader (3f)
```

Ordning: 3a → 3b → 3c → 3d → 3f ger ett komplett `daily_stop_entry`-flöde utan intradagsdata. 3e (ORB) kommer sist och återanvänder state, sizing, costs och trades. Varje delsteg är en egen PR under ~300 rader.

## 2. Breakoutnivå och prisbas (3a)

**Regel `[T]` §4:** `breakout_level` = högsta `high` i konsolideringsfönstret (toppdag, T−1].

**Design `[D]`:**
- `levels.py` beräknar per kandidatrad: `breakout_level_at` (as-traded), `low_prev_at` (= low[T−1], as-traded), `adr20` (kvot, redan i features). Indata är den lagrade as-traded-serien och kandidatraden (`prior_move_peak_date`, `asof_date`). Funktionen **vägrar** serier med `price_basis != as_traded` och as-traded-serier med NaN i `split_ratio` (samma spärr som features).
- **Split på dag T** (`split_ratio[T] ≠ 1`): alla nivåer som ska gälla under dag T divideras med `split_ratio[T]`, eftersom T:s priser handlas i nya aktier. Detta är det enda stället där en framtida stapel (T:s splitkvot) används, och det är legitimt: splitkvoten är känd i förväg (bolagsbeslut), inte ett pris. Dokumenteras och testas.
- **Diagrammets splitjusterade nivåer används aldrig.** Säkring i kod: `OrderLevels` är en frusen dataclass som bara kan skapas av `levels.compute_levels(series_as_traded, row)`; konstruktorn kontrollerar `price_basis` och sätter `units="as_traded"`. `plot.py` importerar inte `levels`. Test: en serie som rebasats till `split_adjusted` ger `FeatureError`.
- Nivåerna lagras i en ny tabell `data/levels/<source>/<TICKER>.parquet` (kandidatrad → nivåer) eller beräknas on-the-fly i signalsteget. `[D]` on-the-fly först (ingen ny lagring), eftersom de är billiga och deterministiska.

## 3. Setup-tillstånd och dedupe (3b)

**Regel `[T]` §4:** en kandidat triggas bara en gång per konsolidering; efter stopp krävs ny konsolidering.

**Design `[D]` – tillståndsmaskin per `setup_group_id`:**

| Tillstånd | Inträde | Utgång |
|---|---|---|
| `ARMED` | första kandidatdagen i gruppen | `TRIGGERED` (fylld), `LAPSED` (dag utan trigger – stannar ARMED om nästa dag också är kandidat i samma grupp), `EXPIRED` (gruppen tar slut utan fyllning, eller `max_armed_days` `[H]` passerat) |
| `TRIGGERED` | fyllning dag T | `CLOSED` när positionen stängts (stop, delvinst + trailing enligt §8) |
| `CLOSED` | position stängd | terminal; gruppen är förbrukad |
| `EXPIRED` | ingen fyllning | terminal |

- Nyckel: `setup_group_id` från `diagnostics.add_group_diagnostics` (konsekutiva kandidatdagar med samma topp). Det finns en lucka: om en kandidatdag saknas mitt i (t.ex. `above_sma20` fäller en dag, som NVDA 2017-06-29) delas gruppen i två id:n med samma topp. `[D]` dedupe-nyckeln blir därför `(ticker, prior_move_peak_date)` – "samma konsolidering" definieras av toppen, inte av gruppens id. En topp kan bara förbrukas en gång. Gruppen i tabellen förblir diagnostisk.
- **En position per ticker** `[T]` (RISK_MODEL §2.2 räknar väntande ordrar som positioner): medan en ticker har `TRIGGERED` ignoreras nya ARMED-grupper för samma ticker.
- **Efter stopp**: toppen är förbrukad; en ny trigger kräver en ny topp (vilket §3:s `consol_min_days` ≥ 12 garanterar tar minst 12 dagar). Efter en vinstaffär som trailas ut: samma regel; `[H]` variant "re-entry på samma topp efter trailing-exit" testas inte i v1.
- **Blockeringen är ändlig – en förbrukad topp släpps `[D]`:** (a) när toppen lämnar `prior_move_window` (63 handelsdagar) och därmed inte längre kan vara `prior_move_peak_date`, eller (b) när priset sätter en ny högsta stängning över toppen, vilket ger en ny toppdag och därmed en ny nyckel. En legitim ny formation under samma gamla topp kan alltså tidigast triggas 63 handelsdagar efter toppen; en formation som bryter över toppen får ny nyckel direkt. Detta är ett medvetet val som gör "en gång per konsolidering" (§4) mekaniskt entydigt. `[H]` variant att testa senare: släpp nyckeln `rearm_after_days` (t.ex. 20) handelsdagar efter stoppet. Test: tre formationer under samma topp inom 63 dagar → en affär; samma med ny topp emellan → två.
- Tillståndet skrivs till `data/signals/<source>/<TICKER>.parquet`: en rad per (grupp, dag) med tillstånd, nivåer, beslut och orsak (`no_trigger`, `gap_too_large`, `cost_to_risk`, `filled`, …). Det ger spårbarhet och är indata till `trades.py`.

## 4. Trigger och fyllning, dagsmodell (3c)

Exakt §5.2 + §6 + §6.1, inget nytt. Sammanfattat som algoritm för dag T, given `OrderLevels` och as-traded `open/high/low/close[T]`:

```
if state != ARMED: skip
if open_T > level × (1 + max_gap_entry): reason = gap_too_large; stay ARMED (nästa dag om kandidat)
elif high_T > level:
    fill = max(open_T, level) × (1 + slippage_bps/1e4)
    stop = max(low_prev_at, fill × (1 − adr20))
    if stop >= fill or (fill − stop)/fill < min_stop_pct: reason = stop_invalid; skip
    shares = position_size(...)   # 3d; 0 → reason = too_small; skip
    state = TRIGGERED
    if low_T <= stop: same-day stop (konservativt −1R), flagga ambiguous_same_day
else: reason = no_trigger
```

Känd svaghet `[T]` §6: `low_prev_at` som stopreferens är ett surrogat för LOD. Det mäts mot ORB när 3e finns.

### 4.1 Signal kontra simulerad fyllning – gemensam fyllningspolicy `[D]`

Två objekt, aldrig samma: `Signal` (vad som är känt när ordern läggs: nivå/trigger, tidpunkt, stopreferens, orsak) och `Fill` (vad simuleringen antar hände: pris, tid, flaggor). En signal utan fyllning är ett giltigt utfall (`no_trigger`, `lapsed`, `gap_too_large`, `orb_incomplete`). Fyllningspolicyn är konservativ och densamma för dags- och minutstaplar:

| Händelse | Regel | Flagga |
|---|---|---|
| Köp-stop vid `trigger`, första stapel med `high ≥ trigger` | `open ≥ trigger` → fyll vid `open × (1 + slip)` (gap igenom nivån); annars `trigger × (1 + slip)` | `gap_fill` om open-fallet |
| Gap vid öppning över `level × (1 + max_gap_entry)` | ingen order den dagen (§5.1.4) | `gap_too_large` |
| Sälj-stop vid `stop` | `open ≤ stop` → `open × (1 − slip)`; annars `low ≤ stop` → `stop × (1 − slip)` | `stop_gap` om open-fallet |
| Entry och stop i **samma stapel** (`high ≥ trigger` och `low ≤ stop`) | fyllning antas ske, därefter stopp i samma stapel: resultat −1R − kostnader. OHLC avslöjar inte ordningen; värsta fallet väljs | `ambiguous_same_bar` (dag: `ambiguous_same_day`) |
| Exit-order från föregående stängning (delvinst/trailing) och stop samma dag | exit-ordern fylls vid öppningen först (§6.1) | – |
| Stapel utan handel / saknad stapel vid trigger-tidpunkt | ingen fyllning förrän en faktisk stapel uppfyller villkoret | – |

Andelen `ambiguous_*` och `gap_fill` rapporteras alltid; är den stor är modellen för grov för aktien, och det ska synas i stället för att döljas av ett optimistiskt antagande.

## 5. ORB (3e)

**Regel `[Q]`/`[T]` §5.1:** opening range = första `orb_minutes` (5) minuterna; trigger = `max(orb_high, breakout_level)`; order läggs först när opening range är stängd; ingen entry efter `latest_entry_time`; stop = LOD fram till fyllning, max ADR.

**Design `[D]` (reviderad efter verifieringen i §9):**
- **Sessionsfilter först.** Alpacas SIP-minutstaplar innehåller pre- och post-market (halvdagen 2024-11-29 gav staplar t.o.m. 21:00Z trots stängning 13:00 ET). Varje dags staplar filtreras med XNYS-kalenderns `session_open`/`session_close` för just den sessionen (hanterar DST och halvdagar): behåll staplar med `open ≤ t < close`, där `t` är stapelns starttid. Antalet behållna staplar lagras (`session_bars`, förväntat 390 resp. 210 på halvdag) och avvikelser > 5 % flaggas `sparse_session`.
- **Opening range:** staplarna med `open ≤ t < open + orb_minutes`. `orb_high/orb_low` över dessa. Kräver att alla `orb_minutes` staplar finns, annars `orb_incomplete` → ingen entry (`[H]` mildare krav testas inte i v1).
- **Opening range är känd först när dess sista stapel är avslutad.** Tekniskt: iteratorn levererar staplar i tidsordning, och ORB-värdena beräknas i det ögonblick stapeln med `t = open + orb_minutes − 1 min` konsumerats; ordern är aktiv från och med nästa stapel (`t ≥ open + orb_minutes`). Inget beslut får läsa en stapel som inte konsumerats. Test: stapeln 09:35 får inte påverka `orb_high`; priset över trigger inom opening range ger ingen fyllning (§5.1.5).
- **Trigger:** `trigger = max(orb_high, breakout_level_at)`. Gapregeln §5.1.4 på sessionens första stapels `open` (kontrolleras mot dagsstapelns `open`; avvikelse > 0,1 % flaggas).
- **Fyllning:** enligt §4.1, på första stapel med `t ≥ open + orb_minutes` och `high ≥ trigger`, under förutsättning att `t < latest_entry_time`.
- **Initial stop – bara tidigare observerade priser.** `stop = max(min(low för alla sessionsstaplar med t < fyllningsstapelns t), fill × (1 − adr20))`. Fyllningsstapelns egen `low` används **inte**, eftersom ordningen mellan dess high och low är okänd. Om fyllningsstapeln är den första efter opening range används alltså ORB-staplarnas lägsta. Därefter: om fyllningsstapelns `low ≤ stop` → `ambiguous_same_bar` enligt §4.1 (stoppad, −1R). Invarianten `stop < fill` kontrolleras; `min_stop_pct` enligt §6.
- **Efter fyllning:** stop kontrolleras mot varje efterföljande stapels `low` (entydigt). Delvinst och trailing enligt §8 beslutas på dagsstängning och exekveras nästa öppning, oförändrat.
- **Upphörande:** `latest_entry_time = session_close − 30 min` `[T]` (15:30 ET normalt, 12:30 ET halvdag). Ofylld → `LAPSED`; ARMED kvarstår om T+1 är kandidatdag med samma topp.
- **Tidszon/DST:** UTC → `America/New_York` med `zoneinfo` före varje klockslagsjämförelse; sessionstider tas från kalendern, aldrig hårdkodade. Verifierat: 13:30Z i augusti, 14:30Z i januari.
- **Volymfilter** `[H]` §4 är av.

## 6. Risk och kostnader (3d)

Allt enligt RISK_MODEL v0.2; här bara vad som behövs för en ticker i taget (portföljgränser över flera tickers kommer med backtestmotorn).

- `equity_start = 10 000 SEK` (D-008), `fx_rate` parameter (konstant i v1, `[D]` 10,5 som placeholder tills Q-003 ger serie), `fx_mode` båda scenarierna (RISK_MODEL §2.8).
- `position_size(equity_usd, fill_est, stop, params)`: `risk_amount = equity × risk_per_trade` (0,5 % och 1 % som testvarianter, inte låst), `shares = floor(risk_amount / (fill_est − stop))`, tak `max_position_pct` 0,25, golv `min_position_value` 100 USD, hela aktier (Q-007 öppen). `fill_est` = `level` (dagsmodell) eller `trigger` (ORB), före fyllning; efter fyllning räknas `actual_risk` om, och vid överskridande > `risk_overshoot_tolerance` reduceras positionen vid dagens stängning (§2.1.7).
- `CostModel`: `commission = max(min_commission, per_share × shares)` per order (Tiered 0,35 USD / 0,0035 per aktie, delvis verifierat; Fixed 1 USD / 0,005 som variant), `slippage_bps` per sida är ett **scenarioantagande, inte en uppmätt kostnad** (P-004): inga kurser (bid/ask) finns i minutstaplarna, så spread och slippage kan inte mätas. Körningar görs alltid med tre scenarier, `[D]` 5/10/20 bps, och resultatet redovisas per scenario; ett resultat som bara håller vid 5 bps räknas inte som robust (§11.8). Mätning blir möjlig först med kursdata eller paper trading. FX: `usd_balance` → kostnad vid insättning (engång), `per_trade` → `fx_cost_bps` på varje köp och sälj.
- `max_cost_to_risk` (0,10/0,15/0,25 som varianter): förväntad kostnad för tre ordrar / `risk_amount`; överskrids den avstås affären (orsak `cost_to_risk`).
- Med 10 000 SEK och en typisk amerikansk aktie på 50–300 USD blir positionerna 1–10 aktier. Delvinst (§8, 1/3) kräver `min_shares_for_partial` = 3; annars `partial_skipped`. Detta kommer att vara vanligt och ska rapporteras (RISK_MODEL §2.4), vilket är ett verkligt fynd om kapitalets storlek, inte ett modellfel.

## 7. Utfall och affärspost (3f)

`trades.py` producerar en rad per affär: `candidate_id`, `setup_group_id`, `peak_date`, entry-modell, datum och pris för entry/stop/delvinst/exit, aktier, `initial_risk_usd`, resultat i USD och R efter kostnader, kostnader per post, `ambiguous_same_day`, `partial_skipped`, `risk_overshoot`, och versionerna (spec, rules, features, levels, signals). Exits enligt §8 på dagsdata (delvinst dag `entry + partial_days` vid nästa öppning; trailing SMA10, exit nästa öppning). Ingen portföljaggregering, ingen equity-kurva i detta steg – bara affärer. Rapport enligt §11 kommer i fas 6.

## 8. Look-ahead-skydd – mekanismer, inte löften `[D]`

1. **Gränssnitt som inte kan läcka:** signalmotorn körs per dag med `DayContext(daily_history_upto_Tminus1, levels_for_T, bar_T)`; för ORB ersätts `bar_T` av en *iterator* över minutstaplar som konsumeras i tidsordning – koden kan inte titta framåt eftersom framtida staplar inte finns i objektet. Ingen funktion får ta hela serien som argument utom `levels.compute_levels`, som skär vid `asof_date` innan beräkning.
2. **Mutationstester** (som i steg 1–2): för varje beslut dag T muteras alla dagsstaplar > T och alla minutstaplar efter fyllningsstapeln; alla fält i signal- och affärsposten som bestäms vid T ska vara identiska.
3. **Explicit undantag:** `split_ratio[T]` används för nivåjustering (§2) – dokumenterat, testat, och det enda.
4. **Delvinst/trailing** beslutas på `close[T]` och exekveras vid `open[T+1]` (§6.1), aldrig på samma stapel.

## 9. Intradagsdata (3e-förberedelse)

**Behov:** minutstaplar (1Min, SIP, raw) för dag T för varje (ticker, ARMED-dag). Med 57 grupper och några ARMED-dagar per grupp är det några hundra handelsdagar × ~390 staplar – trivialt i volym. Inget behov av minutdata för icke-kandidatdagar i v1.

**Verifierat 2026-10-27 (paper-nycklar, SIP, raw, direkta anrop):**

| Datum | Resultat | Tolkning |
|---|---|---|
| AAPL 2020-08-28 | 391 staplar, första 13:30Z, open 504,03 | as-traded före 4:1-splitten; EDT |
| AAPL 2020-08-31 | 391 staplar, första 13:30Z, open 127,62 | as-traded på splitdagen, `raw` ger korrekt bas |
| NVDA 2017-01-10 | 391 staplar, första 14:30Z | EST; DST-hanteringen måste vara kalenderstyrd |
| AAPL 2024-11-29 (halvdag) | 325 staplar, första 14:30Z, sista 21:00Z | staplar efter 13:00 ET-stängningen ingår → sessionsfilter krävs (§5) |
| Alla | `next_page_token = null` | en dag = ett anrop under `limit` |

391 staplar för en hel session = 390 minuter 09:30–15:59 plus en 16:00-stapel (stängningsauktion/sena prints); sessionsfiltret `t < close` tar bort den, och dess volym räknas inte i intradagsflödet. Dagsstapelns volym jämförs ändå med summan av minutvolymerna (med och utan 16:00-stapeln) i verifieringen av 3e.

**Sena korrigeringar `[D]`:** historiska, färdigaggregerade minutstaplar kan revideras efter första publiceringen (sena prints, korrigerade affärer, avbrutna affärer). Konsekvenser: (1) minutdata lagras append-only per hämtning som dagsdata (`data/intraday/alpaca/<TICKER>/<YYYY-MM-DD>_<hämtad>.parquet`) med `fetched_at`; (2) en omhämtning av samma dag jämförs mot tidigare fil och avvikelser i `orb_high`, `orb_low`, fyllningsstapel och sessionslägsta loggas – om de ändrar ett beslut flaggas affären `revised_bars`; (3) backtestresultat anger vilken hämtning som använts; (4) i drift är detta ett argument för att inte lita på en enskild minutstapel nära en nivå utan att ha marginal (`[H]`, utanför v1).

**Hämtning `[D]`:** `AlpacaProvider.fetch_minute(ticker, session_date)` med `timeframe=1Min`, `start/end` = sessionens `open`/`close` i UTC från XNYS-kalendern (plus 1 minut efter close för att fånga 16:00-stapeln till jämförelsen), `limit=10000`, samma retry/pagination som dagsstaplar. Normaliserat schema: `ts_utc`, `ts_ny`, `open/high/low/close/volume`, `n`, `vw`, `in_session` (bool från sessionsfiltret), `fetched_at`.

**Fortsatt öppet (verifieras innan 3e byggs, utfall till DATA_PILOT §8):**
- Historikstart för minutdata (dagsdata börjar 2016-01-04; minutdata ej kontrollerad).
- Rate limit: `X-RateLimit-*`-headrar ej avlästa.
- Avnoterade symboler: `SIVB 2023-03-09` med `asof` på minutnivå ej kört.
- Hur ofta sena korrigeringar förekommer: mäts genom att hämta samma 20 dagar två gånger med en veckas mellanrum.

Yahoo har ingen användbar minuthistorik (bara ~30 dagar) och ingår inte.

## 10. Öppna beslut (måste tas före första körning av 3f)

| # | Beslut | Spec/källa | Typ | Förslag |
|---|---|---|---|---|
| 1 | Acceptansnivåer §12 A och B | §12 | `[T]`, P-011 | Godkänn tabellen som D-012 **innan** steg 3f körs; annars finns ingen stoppregel mot parameterfiske |
| 2 | `risk_per_trade` | RISK_MODEL §3 | `[Q]`-intervall | testa 0,005 och 0,01, ingen låsning |
| 3 | `slippage_bps`, `commission_model` | §10, P-004, P-010, Q-003 | `[T]` | 10 bps + Tiered som placeholder, märkt antagande; känslighet ×1,5/×2 enligt §11.8 |
| 4 | `fx_mode`, `fx_rate` | RISK_MODEL §2.8, Q-008 | öppet | båda lägen, konstant kurs |
| 5 | `max_gap_entry` | §5, P-004 | `[T]` | 0,05 |
| 6 | `orb_minutes` | §5.1, P-009 | `[T]` 5 (källan: 1/5/60 diskretionärt) | 5 först, 1 och 60 som varianter |
| 7 | `latest_entry_time` | §5.1, P-004 | `[T]` | close − 30 min |
| 8 | Dedupe-nyckel `(ticker, peak_date)` i stället för `setup_group_id` | §4 | `[D]` | ja, enligt §3 ovan |
| 9 | En position per ticker | RISK_MODEL §2.2 | `[T]` | ja |
| 10 | `max_armed_days` – hur länge en topp får vara armerad utan trigger | – | `[H]` | ingen gräns i v1; gruppen tar slut när reglerna slutar gälla |
| 11 | Ofullständig opening range | – | `[D]` | ingen entry (konservativt) |
| 12 | Samma-dagsstopp i dagsmodellen | §6.1.4 | `[T]` | konservativt −1R, räknas och rapporteras |
| 13 | Fyllningspolicy §4.1 (gap igenom nivån → open; samma stapel entry+stop → stoppad) | §5–6 | `[D]` | lås som D-013 före 3c |
| 14 | Initial stop i ORB använder bara staplar före fyllningsstapeln | §6 | `[D]` | ja; ORB-staplarnas lägsta om fyllning sker direkt efter opening range |
| 15 | Sessionsfilter `open ≤ t < close` från XNYS-kalendern; 16:00-stapeln utesluts | – | `[D]` | ja; `sparse_session` vid > 5 % saknade staplar |
| 16 | Släppregel för förbrukad topp (63 dagar eller ny topp) | §4 | `[D]` | ja; `rearm_after_days` som `[H]`-variant |
| 17 | Slippage som scenario 5/10/20 bps, aldrig "uppmätt" | §10, P-004 | `[D]` | ja; rapport per scenario |
| 18 | Append-only minutdata med `fetched_at`; `revised_bars`-flagga vid omhämtning | – | `[D]` | ja |

**Måste låsas innan implementation börjar:** beslut 1 (D-012, §12-nivåer) före första körning av 3f; beslut 8, 13, 14, 15 och 16 före kod i 3b/3c/3e eftersom de styr datastrukturer och tester. Beslut 2–7, 9–12, 17–18 är parametrar eller rapporteringsval som kan ändras utan omdesign.

## 11. Testplan (offline, syntetiska serier som i steg 1–2)

**Nivåer (3a)**
- Breakoutnivå = max high i fönstret på as-traded; `low_prev_at` = low[T−1]; serie i `split_adjusted` → vägras; as-traded med NaN-split → vägras.
- Split på T (4:1): nivåer för T = ovanstående / 4; dagen före oförändrad.

**Tillstånd/dedupe (3b)**
- Fem kandidatdagar i rad, trigger dag 3 → en affär, dagarna 4–5 ignoreras (`TRIGGERED`).
- Kandidatdag, lucka (en dag fäller `above_sma20`), kandidatdag igen med samma topp → samma dedupe-nyckel; trigger på första tillfället förbrukar toppen.
- Stoppad ut → ingen ny trigger förrän `prior_move_peak_date` ändrats.
- Två tickers oberoende; samma ticker med två toppar i följd → två affärer tidigast efter 12 dagar.
- Grupp tar slut utan trigger → `EXPIRED`, inget registrerat som affär.

**Dagsmodell (3c)**
- Ingen trigger (`high ≤ level`) → `no_trigger`, ARMED kvar.
- Gap över `max_gap_entry` → `gap_too_large`, ingen affär även om high > level.
- Gap inom gränsen → fyllning vid open, inte vid level.
- Stop < fill-invarianten: konstruera fall där `low[T−1]`-stoppen skulle vara ≥ fill (går inte med definitionerna – testet bevisar att det inte går) och fall där `min_stop_pct` fäller.
- Samma-dagsstopp: `low[T] ≤ stop` → −1R, `ambiguous_same_day = True`.
- Stop-gap nästa dag: `open < stop` → fyllning vid open, förlust > 1R, flaggad.

**ORB (3e)**
- Opening range 09:30–09:34 med 5 staplar; trigger = max(orb_high, level) i båda fallen (orb_high över/under level).
- Fyllning: första stapel med high ≥ trigger efter 09:35; stapel som öppnar över trigger → fyllning vid stapelns open.
- Priset passerar trigger *inom* opening range och sedan aldrig igen → ingen fyllning (§5.1.5).
- Ingen stapel når trigger före `latest_entry_time` → `LAPSED`; stapel efter gränsen ignoreras.
- Ofullständig opening range (3 staplar) → `orb_incomplete`.
- LOD-stop: lägsta low från 09:30 t.o.m. fyllningsstapeln, begränsad av ADR; stop träffas senare samma dag → stängd samma dag, *inte* tvetydig.
- Pre-market-staplar (08:00 ET) i indata → exkluderas från opening range.
- DST: samma syntetiska dag i juli (13:30Z) och januari (14:30Z) ger identiska beslut; DST-skiftesdag (måndag efter) likaså.
- Halvdag: `latest_entry_time` = 12:30 ET; staplar efter 13:00 ET exkluderas av sessionsfiltret; 16:00-stapeln exkluderas på vanlig dag.
- Opening range beräknas först när 09:34-stapeln konsumerats; ett försök att läsa 09:35-stapeln innan dess ger fel, och `orb_high` är oberoende av 09:35-stapelns high.
- Initial stop: fyllning i 09:35-stapeln → stop = min(low 09:30–09:34), inte 09:35-stapelns low; fyllning 10:12 → min(low 09:30–10:11).
- `ambiguous_same_bar`: fyllningsstapel vars low ≤ stop → stoppad −1R, flaggad; stapel därefter med low ≤ stop → stoppad entydigt, ej flaggad.
- `gap_fill`: fyllningsstapel med open > trigger → fyll vid open, flaggad.
- Sena korrigeringar: två hämtningar av samma dag med en ändrad stapel → `revised_bars` när beslutet påverkas, annars bara loggat.
- Släppregel: tre formationer under samma topp inom 63 dagar → en affär; ny topp emellan → två; 64 dagar senare utan ny topp → ny affär tillåten.

**Risk/kostnader (3d)**
- `position_size`: handräknade fall (aktie 40 USD, stop 38, risk 1 % av 950 USD → 4 aktier); tak 25 %; golv 100 USD → 0 aktier; `cost_to_risk` 25 % fäller Fixed men inte Tiered (exemplet i chatten 2026-10-25).
- `CostModel`: minimicourtage slår in vid små ordrar; FX per affär vs engång ger olika totalkostnad; känslighet ×1,5/×2.
- Efter fyllning med gap: `actual_risk` > tolerans → reduktion registrerad.

**Look-ahead (alla)**
- Mutation av dagsstaplar > T och minutstaplar efter fyllning → identiska signal- och affärsposter för T.
- `DayContext`/iterator-gränssnittet: ett försök att läsa framåt ger `AttributeError`/`StopIteration`, inte data.

**Split (alla)**
- Syntetisk serie med 2:1-split tre dagar efter entry: stop och trailing uttrycks i nya aktier (antal × 2, nivåer / 2); R-multipel identisk med osplittad serie.

## 12. Vad som avgör om designen håller

Tre saker ska kunna visas efter 3a–3f, innan ORB: (1) `daily_stop_entry` ger samma affärer på Yahoo och Alpaca för de 19 aktierna; (2) mutationstesterna är gröna; (3) andelen `partial_skipped` och `too_small` vid 10 000 SEK är känd – det är den siffra som säger om kapitalet räcker (RISK_MODEL §4, sista raden). Därefter minutdata-verifieringen i §9, sedan ORB.

## Versionshistorik
- v0.2 (2026-10-27): efter intradagsverifiering och granskning: sessionsfilter, ORB-tillgänglighet, stop utan fyllningsstapelns low, fyllningspolicy §4.1, släppregel för dedupe, slippage som scenario, sena korrigeringar, öppna verifieringspunkter, beslut 13–18.
- v0.1 (2026-10-27): första utkast.
