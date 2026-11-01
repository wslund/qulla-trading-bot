# PORTFOLIO_DESIGN – gemensam portfölj-backtestmotor (fas 6)

**Version:** 0.2 (2026-11-01). Grundarkitekturen godkänd efter ChatGPT-granskning av v0.1; förtydliganden i §2, §4, §6, §7 och §14. Teknisk design, ingen implementation. Bygger på `STRATEGY_SPEC.md` v0.2.1 (§6.1, §8, §11–12), `RISK_MODEL.md` v0.2 (§2.1–2.8, §3) och `STEP3_DESIGN.md` v0.3 samt de mergade modulerna 3a–3f/3e. Taggar: `[D]` designval att fastställa, `[H]` hypotes/testvariant, `[T]` vår regel.

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
| 1 `open` | **Först** splitjustering (§7.1) av alla öppna positioner och alla väntande ordrar/reservationer för tickers med `split_ratio[T] ≠ 1`. **Därefter** exit-ordrar vid `open[T]` (−slip) och stop-gap `open ≤ stop` → fylls vid open. | `open[T]`, `split_ratio[T]` |
| 2 `intraday` | Befintliga positioner: `low ≤ stop` → stoppad. Nya entries: dagsmodell `evaluate_day` på T:s open/high/low; ORB-modell `simulate_orb_day` på minutströmmen (fyllning, same-bar, intradagsstopp). Godkännande via `assess_fill` **inom reservationen**. | `high/low[T]`, minutstaplar för T i ordning |
| 3 `close` | Mark-to-market på `close[T]`; equity, orealiserat, exponering, öppen risk; delvinst-/trailing-beslut för T+1 (`step_position` steg 3); förlustgränser och drawdown (HWM) → läge för T+1; frigivning av ofyllda reservationer; dagsrad i ledgern. | `close[T]` |

Två tickers kan inte påverka varandra inom en fas annat än genom kontot, och kontot ändras i fas 1–2 bara genom fyllningar vars kapital redan var reserverat i fas 0. Därmed spelar fyllningsordningen inom dagen ingen roll för om en order är finansierad – det är det som gör dagsmodellen väldefinierad utan intradagstider.

**Ordning mellan tickers i dagsmodellen är okänd – och får inte spela roll.** Kravet formuleras som en invariant, inte som en konvention: i fas 2 får ingen storhet som en tickers fyllning läser (reservation, antal, godkännande) bero på en annan tickers fyllning samma dag. Konkret: (a) alla beslut som kräver kapacitet tas i fas 0 på T−1-data; (b) `assess_fill` i fas 2 ser bara sin egen reservation; (c) ingen "först till kvarn"-logik finns i fas 2. Test §10.1b: permutation av tickerordningen ger identiska tabeller, och ett sabotagetest där fas 2 medvetet får läsa kontot ska fälla testet. Tickerordning används **bara** som deterministisk iterationsordning och, i baslinjen, som prioritering i fas 0 (§6) – där den är känd T−1 och därför tillåten.

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

Vid fyllning (fas 2): `assess_fill` körs på den antagna fyllningen med `cash_available = reservation.reserved_usd` (inte hela kontot).

**Två skilda orsaker, aldrig hopslagna:**
- `insufficient_capital` – kapitalbehovet överstiger kontots *tillgängliga köpkraft* `cash − reserved_others` vid tidpunkten (dvs. affären hade inte rymts även utan reservationsregeln).
- `reservation_exceeded` – kapitalbehovet överstiger den egna reservationen men **ryms** inom `cash − reserved_others`. Det är reservationsregelns egen kostnad, inte kapitalbrist. Räknas i ledgern (`reservation_blocked_fills`, `reservation_blocked_usd`) och i rapporten som egen rad; blir andelen stor är det ett argument för `[H]`-scenariot "reservation med marginal", inte för att tyst reducera antalet.
Kontrafaktisk reduktion enligt D-013 (`shares_reduced_at_fill`) gäller fortfarande riskgränsen, inte kapitalet. Vid `no_trigger`/`lapsed`/avslag frigörs reservationen i fas 3.

**ORB – explicita tidpunkter:**

| Tidpunkt | Känt | Görs |
|---|---|---|
| fas 0 (före 09:30) | nivå, `low[T−1]`, ADR, konto T−1 | kapacitetskontroll (§5), prioritering, reservation på **dagsplanen** `OrderPlan(nivå × (1+slip), max(low[T−1], ADR-tak))` |
| 09:34-stapeln konsumerad (`OpeningRange.complete`) | `orb_high`, `orb_low` → trigger | ordern dimensioneras på `OrderPlan(trigger × (1+slip), max(orb_low, ADR-tak))` (3d steg 1); kapitalbehov jämförs med reservationen → vid överskridande `reservation_exceeded` (eller `insufficient_capital` om det inte heller ryms i köpkraften); ingen reduktion |
| fyllningsstapeln | antagen fyllning, initial stop | `assess_fill` steg 2 (faktisk risk, kontrafaktisk reduktion per D-013, `cost_to_risk`) – samma som i dag |
| senare staplar / fas 3 | stop, stängning | intradagsstopp, mark-to-market |

Reservationen görs alltså en gång och aldrig om intradag; ORB-planen får vara billigare men inte dyrare än reservationen.

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

Problemet: fas 0 kan ha fler finansierbara kandidater än kapacitet (positioner, risk, kontanter). Valet måste göras på data ≤ T−1 och vara deterministiskt.

**Baslinje (huvudscenario):** ticker alfabetiskt. Neutral i den meningen att den inte bär någon strategiinformation; den är känd T−1 och ger reproducerbara körningar. Den är inte "rättvis" – en ticker tidigt i alfabetet får systematiskt företräde – men det är en känd, rapporterad snedvridning, inte en dold. Som komplement körs `[H]` slumpad ordning med fast frö (flera frön) för att visa spridningen som prioriteringen i sig orsakar.

**Forskningshypotes (separat scenario, förhandsbestämd, inte validerad):** `consol_pct_below_high` stigande, därefter `consol_tightness` stigande, därefter ticker. Hypotesen är att kandidater närmast nivån med tätast konsolidering oftare fyller och sträcks mindre vid fyllning. Den registreras i DECISIONS som P-002-hypotes **innan** den körs, jämförs mot baslinjen och slumpen på samma körning, och får inte presenteras som en fördel förrän den slagit båda på point-in-time-data walk-forward. Andra features (`prior_move_pct`, `dollar_volume_20d`) är tillåtna som ytterligare förhandsregistrerade hypoteser, aldrig som efterhandsval.

Krav på alla prioriteringsfeatures: de ska finnas i kandidatraden för dag T (dvs. beräknade på staplar ≤ T−1, `asof_date`). Ingen fyllningsinformation, inget intradagspris, ingen portföljhändelse från dag T får ingå. Testet §10.1 kör baslinjen och hypotesen på samma syntetiska dag och kontrollerar att ordningen bara beror på T−1-kolumner.

Kandidater som inte ryms får `not_prioritized` (ingen key-förbrukning) och tävlar igen nästa kandidatdag. `priority_binding_days` loggas; vid 10 000 SEK väntas gränsen sällan binda, och då är hela frågan sekundär.

## 7. Exits, splits och ordningen exits-före-entries

- Exits (stop-gap, stop, delvinst, trailing, breakeven) är exakt 3f:s `step_position`, nu fasdelad (§2). Delvinstens säljben, `partial_skipped`, `stop_intraday` (ORB) och `ambiguous_same_*` är oförändrade.
- **Splitdagar (§7.1), ordning inom fas 1 för en ticker med `split_ratio[T] = r`:** (1) öppen position: antal × r, stop / r, entryreferens / r, bråkdel → cash-in-lieu krediteras `cash` till `open[T]` (i dag bokförs den bara i affären); (2) väntande exit-ordrar från T−1: antal × r; (3) väntande entry-order: nivåer kommer redan i T-enheter från `compute_levels` (3a delar med `split_ratio[T]`), så `shares_ordered`, `fill_est` och reservationen beräknades i fas 0 i T-enheter och ändras inte – men det **kontrolleras** (assert) att planens enheter är T:s; (4) först därefter exit-fyllningar vid `open[T]`, stop-gap och fas 2:s prisjämförelser. ORB-dagar: minutstaplarna är redan i T-enheter (`raw`), så trigger/orb_low behöver ingen justering; `split_on_entry_day` följer med i affären. Test: equity före och efter split identisk vid samma pris, stop/entry i nya enheter, exit-order med rätt antal.
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

1. **Samtidiga signaler:** tre tickers kandidat samma dag, kapacitet två → (a) baslinje: de två första alfabetiskt får ordrar, tredje `not_prioritized`; hypotes-scenariot väljer efter `consol_pct_below_high` på samma dag; båda beror bara på T−1-kolumner (byt T:s priser → samma val). (b) **Ordningsinvarians i fas 2:** permutation av tickerordningen ger bitidentiska tabeller; ett sabotagetest där fas 2 får läsa kontot fäller testet.
2. **Kapitalbrist och reservation:** cash räcker till exakt en reservation → andra `insufficient_cash`; `cash − 0,01` → ingen; frigivning i fas 3 gör nästa dags order möjlig. Gap-fyllning som överstiger den egna reservationen men ryms i köpkraften → `reservation_exceeded` (inte `insufficient_capital`) och räknas i `reservation_blocked_fills`; samma fall utan köpkraft → `insufficient_capital`. ORB-plan dyrare än dagsplanen → `reservation_exceeded`.
3. **Exits före entries:** position A stoppas vid open på T och B signalerar T → B läggs bara om den var finansierad före A:s exit (ingen samma-dags-återanvändning); nästa dag räcker kapitalet.
4. **Split med cash-in-lieu:** 3:2-split på öppen position → `cash` ökar med bråkdel × open, equity oförändrad vid samma pris, stop/entryreferens /1,5, R identisk med osplittad; väntande delvinstorder × 1,5; splitjusteringen sker före exit-fyllningen samma dag (en stop som bara träffas i gamla enheter får inte lösa ut).
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

## 14. Beslut efter granskningen av v0.1 (2026-11-01)

Grundarkitekturen (sessionsklocka, faser, ett konto, reservationer, återanvändning av 3a–3f/3e, ekvivalenstester) är godkänd. Status per punkt – det som är **beslut** förs in i `DECISIONS.md` som D-015–D-017 innan P3/P4; resten är dokumenterade pilotantaganden `[D]` i denna fil.

| # | Punkt | Status | Innehåll |
|---|---|---|---|
| 1 | Prioriteringsregel | **D-015** | baslinje ticker alfabetiskt; slump med frö som spridningsmått; `consol_pct_below_high`/`consol_tightness` som förhandsregistrerad P-002-hypotes, separat scenario; alla features kända T−1 |
| 2 | Samma-dags-återanvändning av exitkapital | pilotantagande | nej; `[H]`-scenario |
| 3 | Reservationer | **D-016** | konservativ förhandsreservation; `reservation_exceeded` skild från `insufficient_capital`; `reservation_blocked_fills/usd` rapporteras; ORB: reservation på dagsplanen i fas 0, dimensionering vid komplett opening range, riskbedömning vid fyllningsstapeln (§4) |
| 4 | Förlustgränser vid stängning | pilotantagande | begränsning: ingen intradagsequity på dagsdata |
| 5 | Sektorgräns | pilotantagande | av tills sektordata (Q-002) |
| 6 | ISK-skatt | pilotantagande | ledgerrad vid årsskifte, av tills parametrar satta |
| 7 | Risktrappa | **D-017** | testas bara som förhandsregistrerat scenario enligt §12; stegen skrivs in i DECISIONS innan första körning; utvärderas walk-forward på point-in-time, aldrig mot pilotens affärer |
| 8 | `max_open_positions = 4` | pilotantagande | rapportera bindningsfrekvens |
| 9 | Ingen delfyllning i simulering | pilotantagande | §2.3 gäller drift |
| 10 | Splitordning §7.1 | designregel `[D]` | justering av positioner, stoppar, antal och ordernivåer före dagens fyllningar; ingår i D-016:s text som invariant |
| 11 | Ordningsinvarians mellan tickers i fas 2 | designregel `[D]` | invariant + permutations- och sabotagetest (§2, §10.1b) |
| 12 | Inga trösklar eller riskparametrar optimeras mot pilotens 6 ORB-/9 dagsaffärer | bekräftat | gäller också prioriteringshypotesen och risktrappan |

## 15. Implementationsstatus

**P1 (gren `feat/portfolio-p1-account`, 2026-11-01):** `backtesting/clock.py` (`SessionClock`, `Session`, `Phase`, `steps()`), `backtesting/account.py` (`Account.from_sek`, `reserve/release`, `buy` endast mot reservation med `reservation_exceeded`/`would_fit`, `sell`, `apply_split` enligt §7.1 inkl. cash-in-lieu till `cash` och omskrivning av väntande reservation, `mark`, `leg_costs`), `backtesting/ledger.py` (`LedgerEvent` per kapitalrörelse, `LedgerRow` per session med diagnosfält för P3 förberedda, `check_identities`). Identiteter som kontrolleras vid varje sessionsrad: `cash = start + realized − Σ kostnadsbas`, `equity = cash + Σ antal × mark = start + realized + unrealized`, `0 ≤ reserved = Σ reservationer ≤ cash`. `realized` absorberar courtage och FX när de betalas (entrykostnader direkt), säljresultat mot kostnadsbasen och cash-in-lieu; slippage ligger i priserna och spåras bara som information. `state` är en platshållare för P2. 14 tester. **D-015–D-017 saknas fortfarande i DECISIONS.md och måste föras in före P3.**

**P2 (gren `feat/portfolio-p2-limits`, 2026-11-01):** `risk/limits.py` (lagd i `risk/` enligt STEP3_DESIGN §1 och uppgiften, inte i `backtesting/` som §1 här skrev – `risk` importerar inte `backtesting`; adaptern `snapshot_from_account` är duck-typad). `LimitParams` med RISK_MODEL §3-startvärden (`None` = kontroll av), `PortfolioSnapshot` (konto vid T−1 stängning + planerad stoprisk för öppna positioner och väntande ordrar, `with_order` för kedjning samma dag), `EntryRequest`, `allow_entry` i §5-ordningen med alla kontroller registrerade (`LimitDecision.to_row`), `LossState.update_at_close` (DAILY/WEEKLY/HALTED, bara föregående tillstånd + equity vid stängning; HALTED permanent). Tolkningar: (a) `cost_to_risk` kontrolleras inte här (3d gör det på plan och fyllning); (b) med `max_gross_exposure = 1,0` och `equity = cash + marknadsvärde` är kontantkontrollen implicerad av bruttoexponeringen – båda finns kvar enligt RISK_MODEL, kontanter binder bara om bruttokontrollen är av; (c) HALTED > WEEKLY > DAILY vid samma stängning; veckogränsens ankare = equity vid sista stängning före ISO-veckan; (d) öppen risk räknas på marknadsvärde − aktuell stop (positioner) och `fill_est − stop_plan` (väntande), aldrig negativ; (e) en reservation räknas som position i `max_open_positions`. 14 tester. **D-015–D-017 saknas fortfarande i DECISIONS.md – krävs före P3.**

**P3 (gren `feat/portfolio-p3-reservations`, 2026-11-01):** D-015–D-017 införda i `DECISIONS.md`. `backtesting/priority.py` (`Candidate`, `PriorityParams` med `baseline` default, `seeded_random` med obligatoriskt frö, `research_hypothesis` på T−1-features som vägrar NaN; alla utgår från kanonisk ordning `(ticker, peak_date)`), `backtesting/reservations.py` (`plan_reservation` = 3d `position_size` + `assumed_costs` på dagsplanen inkl. `cost_to_risk` före order, `reserve_day` = snapshot → plan → `allow_entry` → `Account.reserve` med `with_order`-kedjning, `classify_overrun` för `reservation_exceeded`/`insufficient_capital`, `release_unfilled` för fas 3, `DayReservations.priority_binding`). **P1-justering:** `Account.apply_split` skriver inte längre om reservationer – de lever en session och entry-planen är redan i T-enheter (§7.1 punkt 3); den gamla grenen hade dubbeljusterat en split-dags-order. 11 tester (267). Återstår för P4: fasloopen, fyllningar mot reservation, exits, ekvivalenstest mot `run_ticker`.

**P4 (gren `feat/portfolio-p4-engine`, 2026-11-01):** `backtesting/portfolio.py` – `run_portfolio(series, cands, PortfolioParams)` över `SessionClock`, faserna exakt som §2: `pre_open` (registerbeslut, nivåer, `Signal`, P3-prioritering, `reserve_day` mot `LossState` från T−1; avslag → `record_no_fill`), `open` (§7.1 split på position **och** konto först, därefter `step_position(phase="open")`: väntande exits/delvinst vid open, stop-gap; sälj speglas i kontot; stängning → `record_close`), `intraday` (`step_position(phase="intraday")` för befintliga; nya entries bara för reserverade ordrar: `evaluate_day` → `assess_fill` med `cash = egen reservation` och equity T−1 → `classify_overrun` → `approve_and_record` → `Account.buy`; samma-dagsstopp stängs direkt), `close` (`step_position(phase="close")`, `release_unfilled`, mark, `LossState.update_at_close`, ledgerrad med identiteter). 3f:s `step_position` fick ett valfritt `phase`-argument (samma regler, tre anrop) – ingen andra exituppsättning. `assess_fill` fick tolerans 1e-9 i kapitalkontrollen (reservation exakt lika med behovet ska passera). **Ekvivalens:** en ticker, icke-bindande gränser → `Trade` fält för fält lika `run_ticker` (entry, antal, stop, delvinst, exit, kostnader, R, legs) och samma avslagsmängd; dokumenterat undantag: gap-fyllning över reservationen ger `reservation_exceeded` (ingen affär, key ARMED) där `run_ticker` med konstant konto fyller (D-016). Observation: syntetiska serier på `bdate_range` innehåller börshelgdagar som klockan hoppar över – testerna lägger därför staplarna på XNYS-sessioner. 11 tester (278). Inte med: ORB (P5), rapport/script (P6), risktrappa (P7), universum (P8).

**P6 (gren `feat/portfolio-p6-report`, 2026-11-01):** `backtesting/report.py` (`build_report`: kapital med insatt SEK/USD, deposit-FX, handels-P&L, slut-equity och identitetsgap; kostnader; prestanda för alla/realistiska/kontrafaktiska; utnyttjande; avslag delade i **strategi** (signal/risk-kedjan hade nekat även i en-tickerkörning) och **portfölj** (bara det delade kontot/gränserna) med `other` som aldrig tappas; `reservation_blocked_fills/usd` från 3d-besluten, `priority_binding_days`, förlustlägen; `stale_marks` med tickers och datum; `write_run` med trades/rejections/reservations/decisions/ledger/events/equity_curve + `summary.json` + `config.json` (parametrar, args, commit, konfig-hash utan run_id); `render_text`). `scripts/run_portfolio.py` i `run_trades.py`-stil (`--source --splits-from --tickers --equity-sek --risk --slippage --commission --fx-mode --fx-cost-bps --max-cost-to-risk --max-open-positions --max-open-risk-pct --priority --seed --run-id`). Observation: reservationer syns inte i ledgerraden (frigörs vid stängning), så utnyttjandet mäts på reservationsraderna per kandidatdag. 8 tester (286). Ingen ändring i P4-semantik.

**P5 (gren `feat/portfolio-p5-orb`, 2026-11-01):** `PortfolioParams.entry_model = "daily"|"orb"` (+ `OrbParams`), `run_portfolio(..., minute_bars=loader)`. ORB i fasloopen exakt enligt D-016: reservation i `pre_open` på **dagsplanen** (nivå × (1+slip), stop `max(low[T−1], ADR-tak)`) – samma P2-gränser, P3-prioritering och `reserve_day` som dagsmodellen; i `intraday` körs 3e:s `simulate_orb_day` på sessionens minutstaplar (opening range 09:30–09:34 komplett → trigger = max(orb_high, level), första möjliga fyllning 09:35), `assess_fill` med **ORB-planen** (trigger, orb_low) och `cash = egen reservation` → kapitalbehov över reservationen ger `reservation_exceeded` (eller `insufficient_capital` om det inte ryms i köpkraften), ingen reduktion; `ambiguous_same_bar` via 3c:s stängning, entydig intradagsstopp → sälj + `record_close` samma dag; därefter 3f:s dagsexits från T+1. Saknade minutdata → `no_minute_bars`, ofullständig opening range → `orb_incomplete` (aldrig syntetiskt ifyllda staplar); `result.orb_attempts` och `summary()["orb"]` ger kvot per ticker och år. Split på T: `compute_levels` ger T-enheter, minutstaplarna är redan i T-enheter, reservationen görs en gång. Dagsmodellen oförändrad: alla P4/P6-tester gröna och ett test att `entry_model="daily"` med minutladdare ger identiskt resultat som default. Ekvivalens mot `run_ticker(minute_bars=…)`: fält för fält lika inkl. `intraday`-fälten; dokumenterat undantag `reservation_exceeded` när ORB-planen är dyrare än dagsreservationen. Antagna exekveringsbegränsningar: `open_risk`-kontrollen i `pre_open` använder dagsplanens risk (ORB-stoppen är okänd då); minutstaplar visar inte om vår order hade fått fyllning; sena korrigeringar av minutdata loggas av `fetch_minute.py --refetch`, inte här. Rapporten fick `candidates` (rader, distinkta sessioner, dagar med reserverbar kandidat, rader utanför kalendern) och `orb`. 8 tester (294).

**Säkerhetsgranskning faktisk risk (gren `fix/portfolio-open-risk-at-fill`, 2026-11-01, D-018):** `run_portfolio` kunde fylla ORB-order vars summerade *faktiska* stoprisk översteg `max_open_risk_pct` när varje `pre_open`-kontroll på *planerad* risk passerade (gap i fyllningsstapeln; ORB-plan ≠ dagsplan). Reproducerat på `main`: tre order, planerat 279,88 ≤ budget 295,24, faktiskt 300,19 (1,7 % över). Rättning: per order ett tak `planerad_i + slack/n` beräknat i `pre_open`; vid fyllning avslag `open_risk_at_fill` (steg `risk`) om `actual_risk` > tak, före `approve_and_record`/`Account.buy`. Efter: AAA fylld (95,00 ≤ 98,41), BBB/CCC avvisade (102,60 > 98,41), Σ 95,00 ≤ 295,24. `result.risk_audit` per session och rapportens `limits.risk_audit` (`max_actual_over_budget`, `days_over_budget`, `days_prevented`). Ordningsinvarians bevarad (permutationstest). Dagsmodellen oförändrad där ingen överträdelse finns (ekvivalenstest mot `run_ticker` grönt). `PORTFOLIO_VERSION = "3"`. 6 tester (300).

## Versionshistorik
- 0.2 (2026-11-01): efter ChatGPT-granskning av v0.1. §6 baslinje ticker alfabetiskt, featureprioritering som förhandsregistrerad hypotes; §4 `reservation_exceeded` skild från `insufficient_capital` med diagnostik, ORB-tidpunkter explicita; §7.1 splitordning före fyllningar; §2 ordningsinvarians mellan tickers i fas 2 som invariant med test; §14 omgjord till beslutsstatus (D-015–D-017 att föra in).
- 0.1 (2026-11-01): första utkast efter 3a–3f/3e och de två pilotkörningarna (dagsmodell: 9 affärer; ORB: 6 affärer, 15 `orb_incomplete` med 31 av 75 öppningsminuter saknade – regeln om fem kompletta minuter behålls).
