# PORTFOLIO_DESIGN – gemensam portfölj-backtestmotor (fas 6)

**Version:** 0.1 (utkast, 2026-11-01). Teknisk design, ingen implementation. Bygger på `STRATEGY_SPEC.md` v0.2.1 (§6.1, §8, §11–12), `RISK_MODEL.md` v0.2 (§2.1–2.8, §3) och `STEP3_DESIGN.md` v0.3 samt de mergade modulerna 3a–3f/3e. Taggar: `[D]` designval att fastställa, `[H]` hypotes/testvariant, `[T]` vår regel.

## 0. Vad som finns och vad som saknas

Finns (per ticker, konstant konto): `SetupRegistry` (3b), `evaluate_day`/`simulate_orb_day` (3c/3e), `assess_fill`/`RiskApprover` med `OrderPlan` (3d), `step_position`/`run_ticker`/`Trade`/`Rejection`/`summarize` (3f). Allt är rena funktioner per dag eller per stapel.

Saknas: ett konto som ändras av fyllningar, reservationer för väntande ordrar, portföljgränser över tickers (RISK_MODEL §2.2), prioritering när flera tickers signalerar samma dag, förlustgränser och drawdown-stopp (§2.5–2.6), equity-kurva och §11-rapporten. Designprincip: **motorn äger tiden och kontot; allt annat återanvänds oförändrat.** `run_ticker` blir ett specialfall (ett konto, en ticker, inga gränser) och behålls som regressionsreferens: samma enskilda affär måste ge samma `Trade` i portföljmotorn när gränserna inte binder.

## 1. Modulstruktur `[D]`

```
src/tradingbot/backtesting/
├── trades.py        oförändrad: step_position, Trade, close_trade, Rejection (3f)
├── clock.py         sessionsklocka: XNYS-sessioner, faser per dag, deterministisk ordning       (P1)
├── account.py       Account: cash, reserverat, positioner, realiserat/orealiserat, equity, HWM   (P1)
├── ledger.py        dagsrad per session + händelselogg; konserveringskontroll                     (P1)
├── limits.py        allow_entry(): §2.2 portföljgränser + §2.5–2.6 förlust-/drawdownlägen        (P2)
├── priority.py      rangordning av samma dags signaler på data ≤ T−1                              (P3)
├── reservations.py  reservation per väntande order; frigivning vid dagens slut                    (P3)
├── portfolio.py     run_portfolio(): dagsloopen som kopplar ihop allt                              (P4–P5)
├── universe.py      Universe-gränssnitt: members(date); PilotUniverse / PointInTimeUniverse       (P8)
├── policies.py      RiskPolicy: FixedRisk, RiskLadder (scenario), kostnadsmultiplikator          (P7)
└── report.py        §11-rapport: EV, PF, drawdown, per år/regim, blockbootstrap, per scenario     (P6)
```

Alla moduler utom `portfolio.py` är rena funktioner eller frusna dataklasser med explicit tillstånd in/ut. `portfolio.py` är den enda platsen med en loop över tid.

## 2. Händelsekö: faser per session, inte generisk diskret simulering `[D]`

Dagsdata har ingen tidsordning inom dagen; minutdata har det bara för ORB-dagar. En generell händelsekö med tidsstämplar skulle tvinga fram påhittade tider för dagsstaplar. I stället: **en gemensam kronologisk sessionsklocka** (XNYS-kalendern) och **fasta faser inom varje session** i spec §6.1-ordning. Varje fas körs för alla tickers innan nästa fas börjar, och inom en fas i deterministisk ordning (prioritetslista, annars ticker alfabetiskt).

| Fas | Vad | Får läsa |
|---|---|---|
| 0 `pre_open` | Exit-ordrar beslutade vid stängning T−1 (delvinst, trailing, breakeven-flytt). Kandidater för T: `SetupRegistry.decide`, nivåer (3a), `OrderPlan` på dagsformlerna, prioritering, reservationer. Förlust-/drawdownläge från T−1 avgör om nya ordrar får läggas alls. | staplar ≤ T−1, kontot vid T−1 stängning |
| 1 `open` | Exit-ordrar fylls vid `open[T]` (−slip). Stop-gap: `open ≤ stop` → fylls vid open. Splits appliceras på positioner innan prisjämförelser (`apply_split`, cash-in-lieu till cash). | `open[T]`, `split_ratio[T]` |
| 2 `intraday` | Befintliga positioner: `low ≤ stop` → stoppad. Nya entries: dagsmodell `evaluate_day` på T:s open/high/low; ORB-modell `simulate_orb_day` på minutströmmen (fyllning, same-bar, intradagsstopp). Godkännande via `assess_fill` **inom reservationen**. | `high/low[T]`, minutstaplar för T i ordning |
| 3 `close` | Mark-to-market på `close[T]`; equity, orealiserat, exponering, öppen risk; delvinst-/trailing-beslut för T+1 (`step_position` steg 3); förlustgränser och drawdown (HWM) → läge för T+1; frigivning av ofyllda reservationer; dagsrad i ledgern. | `close[T]` |

Två tickers kan inte påverka varandra inom en fas annat än genom kontot, och kontot ändras i fas 1–2 bara genom fyllningar vars kapital redan var reserverat i fas 0. Därmed spelar fyllningsordningen inom dagen ingen roll för om en order är finansierad – det är det som gör dagsmodellen väldefinierad utan intradagstider.

`step_position` används oförändrad men delas upp i anrop per fas: fas 1 = dess steg 1–2 med `open`, fas 2 = steg 2 med `low`, fas 3 = steg 3. Praktiskt: en tunn `PositionDay`-adapter som anropar samma kod i tre steg, med test att resultatet per position är identiskt med dagens `step_position` när kontot inte binder.

## 3. Ett konto `[D]`

```
Account (muterbar, bara portfolio.py muterar den, via metoder som returnerar Ledger-händelser)
  cash_usd            tillgängliga medel efter alla fyllningar och kostnader
  reserved_usd        summan av öppna reservationer (fas 0 → frigivning fas 3)
  positions           ticker -> Position (3f) + entry-data
  realized_usd        ackumulerat realiserat netto (efter courtage/FX)
  unrealized_usd      Σ shares × (close − entry_fill) vid senaste mark
  equity_usd          cash + Σ shares × close           (RISK_MODEL §2.1.1: inkl. orealiserat)
  hwm_usd             högsta equity vid stängning hittills
  fx                  fx_mode, fx_rate (konstant pilotparameter), deposit_fx_cost_usd
  state               NORMAL | DAILY_LIMIT | WEEKLY_LIMIT | HALTED (+ datum, equity, orsak)
```

Regler:
- `equity` beräknas bara i fas 3 (stängning T) och används för `risk_amount` och `max_position_pct` i fas 0 nästa dag – aldrig intradag.
- `available_for_orders = cash − reserved`. Kapital som frigörs av exits i fas 1 på dag T **används inte** för entries på dag T (`[D]`, konservativt: ordern lades före öppning när kapitalet var låst). Alternativ `[H]` "samma-dags-återanvändning" som scenario.
- Konservering (testas varje dag): `cash + Σ positionsvärde(entry) + reserved − reserved == start_cash + realized + cash_in_lieu − deposit_fx` och `equity == cash + Σ shares × close`.
- `usd_balance`: hela startkapitalet växlas dag 0, `deposit_fx_cost` bokförs; `per_trade`: FX per ben in i `realized`. Equity rapporteras i USD och i SEK via `fx_rate` (konstant; valutarisken på USD-saldot redovisas som egen rad, RISK_MODEL §2.8).
- ISK-schablonskatt: egen ledgerrad vid årsskifte, **av som default** tills parametrarna är satta (användarparametrar); påverkar inte entrybeslut.

## 4. Reservationer och kapitalkrav `[D]`

Varje väntande entry-order får en `Reservation(ticker, key, shares_ordered, fill_est, reserved_usd)` i fas 0 med `reserved_usd = shares_ordered × fill_est + entry-courtage + entry-FX` (3d:s `capital_required` på planen). En order läggs **bara om den är fullt finansierad**; delvis finansiering reduceras inte (RISK_MODEL §2.1.6: en order som inte klarar kontrollerna läggs inte).

Vid fyllning (fas 2): `assess_fill` körs på den antagna fyllningen med `cash_available = reservation.reserved_usd` (inte hela kontot). Gap-fyllning över reservationen → `insufficient_capital` på portföljnivå (ny orsak `reservation_exceeded`) eller kontrafaktisk reduktion enligt D-013 – samma flaggor som i dag. Vid `no_trigger`/`lapsed`/avslag frigörs reservationen i fas 3.

ORB-ordrar: reservationen i fas 0 görs på **dagsplanen** (nivå × (1+slip), stop `low[T−1]`) eftersom trigger och ORB-low inte finns före 09:35. Vid 09:35 dimensioneras ordern på `OrderPlan(trigger, orb_low)` (som i dag) men får högst använda reservationen; blir ORB-planen dyrare än reservationen reduceras **inte** – ordern avstås med `reservation_exceeded` `[D]`. Alternativ `[H]`: reservation med marginal `max_gap_entry`.

## 5. Portföljgränser och lägen (`limits.py`)

`allow_entry(account, open_positions, reservations, order, sector_of) -> (ok, reason)` i fas 0, i denna ordning (första fallerande rapporteras):

| # | Kontroll | Källa | Orsak |
|---|---|---|---|
| 1 | `state == HALTED` eller dags-/veckogräns aktiv | §2.5–2.6 | `halted` / `loss_limit_daily` / `loss_limit_weekly` |
| 2 | öppna + väntande ≤ `max_open_positions` | §2.2 | `max_positions` |
| 3 | Σ planerad stoprisk (öppna med aktuell stop + väntande med plan) + orderns risk ≤ `equity × max_open_risk_pct` | §2.2, §2.7 | `open_risk` |
| 4 | Σ marknadsvärde + Σ reserverat + orderns reservation ≤ `equity × max_gross_exposure` (1,0) | §2.2 | `gross_exposure` |
| 5 | `cash − reserved ≥ reservation` | §2.2 | `insufficient_cash` |
| 6 | `[H]` antal i samma sektor < `max_per_sector`; kräver sektordata, annars av | §2.2 | `sector_limit` |
| 7 | `cost_to_risk` på planen (3d) | §2.2 | `cost_to_risk` |

Förlustgränser mäts på **stängning** (`[D]`: dagsdata saknar intradagsequity; "under dagen" i §2.5 blir "vid stängning T → inga nya ordrar T+1"). Drawdown-stopp enligt §2.6: `equity ≤ (1 − max_drawdown_halt_pct) × HWM` → `HALTED` från nästa fas 0, inga nya ordrar, befintliga positioner följer sina regler; simuleringen avslutas i `HALTED` när sista positionen stängts och rapporteras så. `auto_resume` bara som `[H]`-scenario.

Avslag i fas 0 går via `record_no_fill(key, T, reason)` precis som i dag: **keyn förbrukas aldrig av en portföljgräns.**

## 6. Prioritering vid flera signaler samma dag (`priority.py`) `[D]`

Problemet: fas 0 kan ha fler finansierbara kandidater än kapacitet (positioner, risk, kontanter). Valet måste göras på data ≤ T−1 och vara deterministiskt och förhandsregistrerat.

Förslag `[T]` (default, ska fastställas som beslut innan första körning):
1. `consol_pct_below_high` stigande (närmast nivån först – lägst avstånd till trigger, minst "sträckning" vid fyllning),
2. `consol_tightness` stigande,
3. ticker alfabetiskt (ren tiebreak, ingen informationsbärare).

Alternativ som **scenarier**, inte parametersök: `[H]` `prior_move_pct` fallande (starkast momentum först); `[H]` `dollar_volume_20d` fallande (mest likvid först); `[H]` slumpad ordning med fast frö (nollhypotes: om prioriteringsregeln inte slår slumpen bär den ingen information). Regeln rapporteras alltid i resultatet, och antalet dagar där den faktiskt avgjorde något (`priority_binding_days`) loggas – är det få dagar är frågan ointressant vid 10 000 SEK.

Kandidater som inte ryms får `not_prioritized` (ingen key-förbrukning). Nästa dag tävlar de igen om de fortfarande är kandidater.

## 7. Exits, splits och ordningen exits-före-entries

- Exits (stop-gap, stop, delvinst, trailing, breakeven) är exakt 3f:s `step_position`, nu fasdelad (§2). Delvinstens säljben, `partial_skipped`, `stop_intraday` (ORB) och `ambiguous_same_*` är oförändrade.
- Splits: `apply_split` på varje öppen position i fas 1 innan prisjämförelser; bråkdel → cash-in-lieu **krediteras `cash`** (i dag bokförs den bara i affären). Test: equity före och efter split identisk på samma pris.
- Exits före entries gäller dubbelt: fas 1–2 stänger innan fas 2:s entries körs för samma ticker (registret kräver det), och kontot hinner registrera realiserat innan stängningens mark. Kapitalet från exits blir dock tillgängligt först nästa fas 0 (§3).
- Avnoterad/handelsstoppad aktie med öppen position: värderas till 0 tills datalagret visar slutkurs (RISK_MODEL §2.7); affären får `exit_reason = delisted_zero` och flaggas. Gäller bara point-in-time-körningen.

## 8. Kostnader och FX på portföljnivå

Som 3d/3f per ben, men nu mot ett konto: courtage och FX dras från `cash` vid varje ben; slippage ligger i priserna. Kostnadsmultiplikator ×1,0/×1,5/×2,0 (spec §11.8) läggs i `policies.py` som **tre separata körningar** – multiplikatorn skalar `slippage_bps` i `TriggerParams`, `per_share`/`min_commission` och `fx_cost_bps` samtidigt, så att priser och kostnadsposter inte glider isär (samma skäl som i 3d). `cost_actual_over_risk` aggregeras per körning.

## 9. Loggning och rapport (`ledger.py`, `report.py`)

Tre tabeller per körning, alla med `run_id`, commit-hash, scenario och versioner (spec, features, rules, levels, trigger, state, sizing, cost, trades, portfolio):
- **trades** – 3f:s `Trade` + `reservation_usd`, `priority_rank`, `n_open_at_entry`, `open_risk_at_entry`, `equity_at_entry`.
- **rejections** – 3f:s `Rejection` + portföljorsakerna i §5–6 och `stage = "portfolio"`.
- **ledger** (en rad per session) – cash, reserved, n_positions, n_pending, gross_exposure, open_risk_planned, equity, realized, unrealized, drawdown, hwm, state, n_signals, n_orders_placed, n_fills, priority_binding.

Rapporten (§11.6–11.8, D-012): antal affärer, EV i R, PF, träffprocent, snittvinnare/-förlorare, max drawdown (procent av equity), sämsta dag, antal dagar med förlust > `max_open_risk_pct`, CAGR, per år, per regim, `halted` (datum), andelar `ambiguous_*`, `partial_skipped`, `shares_reduced_at_fill` (egen grupp), `open_at_end`, avslag per orsak, blockbootstrap per månad för EV-intervall. Funktionstestet på piloten skriver **alltid** "funktionstest, ej evidens" i rapporthuvudet (D-012).

## 10. Deterministiska tester (offline, syntetiska serier)

1. **Samtidiga signaler:** tre tickers kandidat samma dag, kapacitet två → de två med lägst `consol_pct_below_high` får ordrar, tredje `not_prioritized`; resultatet oberoende av tickrarnas inmatningsordning; tiebreak alfabetisk testad separat.
2. **Kapitalbrist:** cash räcker till exakt en reservation → andra `insufficient_cash`; `cash − 0,01` → ingen; frigivning i fas 3 gör nästa dags order möjlig.
3. **Exits före entries:** position A stoppas vid open på T och B signalerar T → B läggs bara om den var finansierad före A:s exit (ingen samma-dags-återanvändning); nästa dag räcker kapitalet.
4. **Split med cash-in-lieu:** 3:2-split på öppen position → `cash` ökar med bråkdel × open, equity oförändrad vid samma pris, stop/entryreferens /1,5, R identisk med osplittad.
5. **Konservering:** varje dag `equity == cash + Σ shares × close` och realiserat = Σ affärers `pnl_net` (+ cash-in-lieu − deposit-FX).
6. **Look-ahead:** mutera alla staplar > T för alla tickers (och lägg en split på T+1) → ledgerrad T, ordrar, reservationer, prioritering och fyllningar för T identiska; raderna efter T ändrade (testet kräver båda).
7. **Gränser:** `max_open_positions`, `open_risk`, `gross_exposure` var för sig bindande med handräknade tal; förlustgräns vid stängning blockerar T+1 och släpper enligt regel; drawdown → `HALTED`, inga nya ordrar, befintlig position stängs av sin stop, körningen slutar `halted`.
8. **Ekvivalens:** en ticker, kapital som aldrig binder → portföljmotorns `Trade` == `run_ticker`:s `Trade` fält för fält (båda modellerna, inkl. ORB med intradagsstopp).
9. **Determinism:** två körningar av samma konfiguration ger bitidentiska tabeller; slumpad prioritering med frö likaså.

## 11. Pilot (19 aktier) kontra point-in-time-backtest

| | Funktionstest på piloten | Point-in-time (fas 6, Q-002) |
|---|---|---|
| Universum | 19 överlevande tickers, kända i dag | `Universe.members(T−1)`: noterade bolag som uppfyllde spec §1 den dagen, inkl. senare avnoterade |
| Vad resultatet säger | att motorn, kontot och gränserna fungerar; andelar `too_small`/`cost_to_risk`/`partial_skipped` vid 10 000 SEK | strategins EV, PF, drawdown enligt D-012 |
| Survivorship | ja, grov (DATA_PILOT §6) | hanterad om leverantören täcker avnoteringar |
| Avnotering/handelsstopp | förekommer inte | position värderas till 0 (§7), `delisted_zero` |
| Tickeromdöpningar | ej hanterade | kräver symbolhistorik (Alpaca `asof` räcker inte för universumet) |
| Sektordata | saknas → `max_per_sector` av | krävs för §2.2-korrelationsgränsen |
| Rapportdatum | saknas → blackout-variant av | krävs för `earnings_blackout_days`-varianten |
| Rubrik | "funktionstest, ej evidens" tvingande | §11-rapport med walk-forward och bootstrap |

Samma motor körs i båda fallen; skillnaden ligger helt i `Universe` och datalagret. Universumgränssnittet byggs därför nu (P8) med `PilotUniverse` (konstant lista) och ett tomt `PointInTimeUniverse` som kräver noterings-/avnoteringsdatum – så att ingen kod i motorn antar att universumet är konstant.

## 12. Dynamisk risktrappa som separat scenario `[H]`

Idén (från chatten, inte i något dokument): `risk_per_trade` trappas upp när kontot växer och ned vid drawdown, i stället för fast 0,5 % eller 1 %. Den hör hemma i `policies.py` som en `RiskPolicy` med signaturen `risk_per_trade(account_at_close_Tminus1) -> float`, så att 3d (`RiskParams.risk_per_trade`) tar värdet per dag utan andra ändringar.

Regler för att testa den **utan att optimera mot piloten**:
1. **Förhandsregistrering.** Trappans steg skrivs in i `DECISIONS.md` innan den körs första gången, med motivering i riskmodellens termer (t.ex. steg vid equity ≥ 1,25× resp. 1,5× start: 0,5 % → 0,75 % → 1,0 %; nedsteg till 0,5 % vid drawdown ≥ 10 % från HWM; högst 1,0 % enligt `[Q]`-intervallet). Högst två varianter, ingen rutnätssökning.
2. **Ingen titt på pilotens affärer** när stegen sätts. Pilotkörningen är ett funktionstest; trappan utvärderas först på point-in-time-data med walk-forward (§11.4), där stegen sätts på träningsfönstret och utvärderas på nästa fönster.
3. **Jämförelsegrund:** samma signaler och samma fyllningar som `FixedRisk(0,005)` och `FixedRisk(0,01)`; skillnaden ligger bara i antal aktier. Rapportera max drawdown, sämsta dag, antal `HALTED` och EV i R **per fönster**, inte slut-equity (slut-equity belönar hävstång i en uppåtperiod).
4. **Nollhypotes:** en trappa som bara följer equity är ekvivalent med en högre fast risk i bra perioder; den måste visa lägre drawdown vid likvärdig EV för att räknas som bättre, annars avfärdas den.
5. Vid 10 000 SEK gäller dessutom heltalsproblemet: trappan kan inte ändra `shares` förrän `risk_amount` passerar nästa heltal – det rapporteras som `ladder_binding_days`.

## 13. Prioriterad arbetsordning (varje steg egen PR < 300 rader, tester enligt §10)

| P | Innehåll | Beroende |
|---|---|---|
| P1 | `clock.py`, `account.py`, `ledger.py`: konto med konservering, faser, dagsrad | – |
| P2 | `limits.py`: §5 utan sektor, förlust-/drawdownlägen | P1 |
| P3 | `priority.py` + `reservations.py` | P1 |
| P4 | `portfolio.py` dagsmodell; ekvivalenstest mot `run_ticker` | P1–P3 |
| P5 | ORB i portföljloopen (reservation på dagsplanen, `reservation_exceeded`) | P4 |
| P6 | `report.py`: §11-rapport, blockbootstrap, scenario-sammanställning; `scripts/run_portfolio.py` | P4 |
| P7 | `policies.py`: kostnadsmultiplikator, `FixedRisk`, `RiskLadder` (förhandsregistrerad) | P4 |
| P8 | `universe.py`: `PilotUniverse`, `PointInTimeUniverse`-gränssnitt, `delisted_zero` | P4, Q-002 |

Funktionstest på piloten efter P6; inget resultat tolkas som edge före P8 och Q-002.

## 14. Öppna beslut (måste tas före P3/P4)

| # | Beslut | Förslag |
|---|---|---|
| 1 | Prioriteringsregel §6 | `consol_pct_below_high` ↑, `consol_tightness` ↑, ticker; slump med frö som nollhypotes |
| 2 | Samma-dags-återanvändning av kapital från exits | nej i grundmodellen; `[H]`-scenario |
| 3 | ORB-reservation | dagsplanen; `reservation_exceeded` i stället för reduktion |
| 4 | Förlustgränser mäts vid stängning | ja (dagsdata); intradagsvariant kräver minutdata för alla positioner |
| 5 | Sektorkälla för `max_per_sector` | av tills Q-002 ger GICS eller motsvarande |
| 6 | ISK-skatt i ledgern | rad vid årsskifte, av tills parametrar satta |
| 7 | Risktrappans steg | förhandsregistreras i DECISIONS innan första körning (§12) |
| 8 | `max_open_positions` vid 10 000 SEK | 4 enligt RISK_MODEL §3; rapportera hur ofta gränsen binder |
| 9 | Delfyllning | ingen i simulering (dagsdata); §2.3 gäller drift |

## Versionshistorik
- 0.1 (2026-11-01): första utkast efter 3a–3f/3e och de två pilotkörningarna (dagsmodell: 9 affärer; ORB: 6 affärer, 15 `orb_incomplete` med 31 av 75 öppningsminuter saknade – regeln om fem kompletta minuter behålls).
