# RISK_MODEL – money management

**Version:** 0.2 (utkast, 2026-10-25). v0.1 granskad av ChatGPT efter merge; korrigeringar införda, ej slutgodkänd. Kompletterar `STRATEGY_SPEC.md` §6–8 och ersätter dess §7 som normativ källa för positionsstorlek och portföljgränser.

Taggar som i STRATEGY_SPEC: `[Q]` dokumenterad Qullamaggie-regel, `[T]` vår regel, `[H]` otestad hypotes. Parametrar är namn i `configs/risk.yaml`. Alla värden är **preliminära och testbara**, inte beslut, om inte ett D-nummer anges.

Grundprincip: riskmotorn är en uppsättning rena funktioner (`position_size`, `allow_entry`, `portfolio_limits`) som är oberoende av signalgenerering och ML. ML får aldrig ändra riskgränser (PROJECT_MASTER §3).

## 1. Qullamaggies dokumenterade principer `[Q]`

Källa: <https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/>.

| Princip | Källans formulering |
|---|---|
| Risk per affär | "usually 0.25-1%", sällan över 1 %; för konton under några miljoner "0.5-1.5%" |
| Positionsstorlek | max 30 % av kontot i en aktie över natten; typiskt 10–20 % |
| Stop-loss | dagens lägsta, aldrig vidare än aktiens ADR/ATR |
| Delvinst | efter 3–5 dagar säljs 1/3–1/2, stop flyttas till breakeven |
| Trailing stop | 10- eller 20-dagars MA, exit vid första stängning under |
| Marknadsläge | "fewer or no trades" i svaga marknader |

Källan säger inget om: total öppen risk, antal positioner, korrelation, dagliga förlustgränser, drawdown-stopp, avgifter, valuta, rapportdatum eller handelsstopp. Allt i §2 är därför vårt.

## 2. Våra kompletterande riskregler `[T]`

### 2.1 Positionsstorlek
1. `risk_amount = equity × risk_per_trade`, där `equity` = kontovärde vid stängning T−1 inklusive orealiserat resultat. Återinvestering sker automatiskt via procentregeln.
2. `shares = floor(risk_amount / (fill_estimate − stop))`. Avrundas nedåt till hela aktier tills fraktionshandel verifierats (Q-007). `shares = 0` → affären avstås.
3. `shares × fill_estimate ≤ equity × max_position_pct`, annars reduceras `shares`.
4. `shares × fill_estimate ≥ min_position_value`, annars avstås affären.
5. Vid `daily_stop_entry` är `fill_estimate = breakout_level × (1 + slippage_bps/10 000)`. Vid `orb_entry` är det `trigger` enligt spec §5.1.
6. **Före order:** alla kontroller i 2.1–2.2 görs på `fill_estimate` och planerad stop. En order som inte klarar dem läggs inte.
7. **Efter fyllning:** `actual_risk = shares_filled × (fill_actual − stop)`. Stoppen flyttas aldrig för att rädda risken. Om `actual_risk > risk_amount × (1 + risk_overshoot_tolerance)` (förslag 0,25) – i praktiken vid gap uppåt inom `max_gap_entry` – reduceras positionen samma dag till det antal som ger `actual_risk ≤ risk_amount` (säljs vid nästa tillgängliga pris; i dagsdatasimulering vid `close[T]`). Om portföljgränsen för öppen risk (2.2) överskrids efter fyllning gäller samma reduktion. Händelsen rapporteras som `risk_overshoot`.

### 2.2 Portföljgränser (kontrolleras före varje ny order)
| Kontroll | Regel |
|---|---|
| Antal positioner | öppna + väntande entry-ordrar ≤ `max_open_positions` |
| Öppen risk | Σ(aktuell stop-distans × antal) över öppna positioner **och** väntande ordrar ≤ `equity × max_open_risk_pct` |
| Bruttoexponering | Σ marknadsvärde + reserverat för väntande ordrar ≤ `equity × max_gross_exposure` |
| Tillgängligt kapital | kontant saldo − reserverat för väntande ordrar ≥ orderns värde + beräknade avgifter; ingen marginal |
| Korrelation | max `max_per_sector` positioner i samma sektor (GICS-sektor eller motsvarande från datalagret); `[H]` – kräver sektordata, testas på/av |
| Avgiftsandel | förväntad total avgift (entry + delvinst + exit, courtage + spread + slippage) / `risk_amount` ≤ `max_cost_to_risk`, annars avstås |

Väntande ordrar räknas som om de fyllts; när en order inte fylls (ingen breakout) frigörs reservationen vid dagens slut.

### 2.3 Orderfyllning och avstämning
- Riskberäkning uppdateras efter **faktisk** fyllning: verkligt pris, verkligt antal. Delfyllning: stoppen gäller för fyllt antal; resten av ordern avbryts vid dagens slut.
- Positioner stäms av mot mäklarens saldo varje dag före öppning; avvikelse stoppar all ny orderläggning tills den är förklarad (livehandel, fas 7+).
- Dubblettskydd: en kandidat får en `candidate_id`; en order per `candidate_id` och dag.

### 2.4 Delvinst och avrundning
Delvinst enligt spec §8 kräver minst `min_shares_for_partial` (förslag 3) aktier; annars behålls hela positionen med trailing stop och stoppen flyttas till breakeven vid samma tidpunkt som delvinsten skulle ha tagits. Detta är en **annan strategi** än spec §8 (ingen delvinst → annan R-fördelning). Sådana affärer märks `partial_skipped` och rapporteras som egen grupp med eget EV, utöver totalen; en körning där andelen överstiger `max_partial_skipped_share` (förslag 25 %) flaggas som icke-representativ för specen.

### 2.5 Förlustgränser
| Gräns | Verkan |
|---|---|
| `daily_loss_limit_pct` | realiserad + orealiserad förlust under dagen ≥ gräns → inga nya entries resten av dagen; befintliga stoppar gäller |
| `weekly_loss_limit_pct` | motsvarande, resten av kalenderveckan |
| `max_drawdown_halt_pct` | se 2.6 |

### 2.6 Drawdown-stopp
Trigger: `equity` ≤ (1 − `max_drawdown_halt_pct`) × högsta `equity` hittills (high-water mark), mätt vid stängning.

Verkan:
1. Alla väntande entry-ordrar avbryts. Inga nya entries.
2. Befintliga positioner **behålls** med sina stoppar och trailing-regler; de stängs inte tvångsmässigt (en tvångsstängning låser förlusten vid ett godtyckligt tillfälle och är inte en Qullamaggie-regel). `[H]`-variant: stäng allt; testas.
3. Status `HALTED` loggas med datum, equity och orsak.

`HALTED` är ett konsekvent läge, lika i backtest och drift:
- Inga nya entries, inga nya entry-ordrar. Befintliga positioner avvecklas enbart genom sina stoppar, delvinst- och trailing-regler (§2.4, spec §8); dessa fortsätter att gälla oförändrade.
- Läget lämnas bara genom ett explicit `RESUME`-beslut. I drift: manuellt av projektägaren, dokumenterat i `DECISIONS.md` med orsak och eventuella ändrade parametrar. Botten får inte återuppta sig själv.
- I backtest finns inget `RESUME` i grundmodellen: simuleringsfönstret avslutas i `HALTED` när sista positionen är stängd, och utfallet rapporteras som `halted` med datum och equity. Att strategin skulle ha stoppats är ett resultat, inte något som ska maskeras.
- `[H]` Testvariant `auto_resume`: återstart efter `halt_cooldown_days` (förslag 10) med `risk_per_trade` halverad tills equity passerat nivån där stoppet utlöstes. Körs bara som jämförelse mot grundmodellen; high-water mark nollställs inte.

### 2.7 Kursgap, rapporter och handelsstopp
- Entry-gap: spec §5 (`max_gap_entry`).
- Stop-gap: stoppen fylls vid öppning om öppning < stop (spec §6). Förlust > 1 R är verklighet, inte modellfel; andelen affärer med förlust > 1 R och > 2 R rapporteras.
- `max_open_risk_pct` begränsar den **planerade** stop-risken (summan av avstånd till stop). Den begränsar inte den verkliga förlusten en dag då flera positioner gappar under sina stoppar; den förlusten begränsas bara av `max_gross_exposure` (i värsta fall hela exponeringen). Därför rapporteras sämsta dagsresultat och antalet dagar med förlust > `max_open_risk_pct` separat.
- Rapportblackout: **separat testvariant**, inte obligatorisk. Variant på: ingen entry om rapport inom `earnings_blackout_days` handelsdagar; befintlig position stängs vid stängning dagen före rapport. Kräver rapportdatum i datalagret (Q-002).
- Handelsstopp/avnotering i simulering: position värderas till 0 tills datalagret kan visa faktisk slutkurs (konservativt).

### 2.8 Valuta och skatt
Valuta – två scenarier, båda modelleras, inget antas gälla för IBKR svenska ISK förrän Q-008 är löst (`fx_mode`):
- `usd_balance`: kapitalet växlas en gång; USD-saldo hålls mellan affärer. FX-kostnad (`fx_cost_bps` + eventuell minimiavgift) bokförs vid insättning och uttag. Valutarisk på USD-saldot redovisas som egen post.
- `per_trade`: varje köp växlar SEK→USD och varje försäljning USD→SEK. FX-kostnaden ingår i varje affärs avgifter och därmed i `max_cost_to_risk`. Vid litet kapital kan detta ensamt göra många affärer ogenomförbara; andelen rapporteras.
- `fx_rate` är en simuleringsparameter (konstant eller historisk serie).

ISK-schablonskatt (verifiera mot Skatteverket, gäller svensk skatt 2026):
- Kapitalunderlag = (summan av kontots värde vid ingången av varje kvartal + insättningar under året) / 4. Insättningar räknas med; uttag dras inte av.
- Schablonintäkt = (kapitalunderlag − `isk_tax_free_allowance`) × `isk_schablon_rate`, lägst 0. Grundnivån (300 000 SEK år 2026) gäller användarens **sammanlagda** ISK/KF/PEPP-sparande och fördelas mellan konton; `isk_tax_free_allowance` är därför den del av grundnivån som är tillgänglig för detta konto – en användarparameter, inte en konstant.
- Skatt = schablonintäkt × 30 %, betalas utanför kontot men dras i simuleringen från equity vid årsskiftet för att visa nettoavkastning. `isk_schablon_rate` följer statslåneräntan + 1 procentenhet med golv; värdet per år är en parameter.

## 3. Preliminära parametrar att testa

Utgångsvärden för `configs/risk.yaml`. Kolumnen "Testa" anger vilka alternativ som ska köras och rapporteras; ingen kolumn är ett beslut.

| Parameter | Utgångsvärde | Testa | Kommentar |
|---|---|---|---|
| `equity_start` | 10 000 SEK | 10 000 / 50 000 / 200 000 | D-008. Skiljer kapitalproblem från strategiproblem |
| `risk_per_trade` | 0,005 | 0,005 / 0,01 | båda inom `[Q]`-intervallet; högre risk löser inte avgiftsproblemet |
| `max_position_pct` | 0,25 | 0,20 / 0,30 | `[Q]` max 30 % |
| `max_open_positions` | 4 | 3 / 4 / 6 | |
| `max_open_risk_pct` | 0,04 | 0,03 / 0,05 | |
| `max_gross_exposure` | 1,0 | – | ingen hävstång; ej testvariabel |
| `min_position_value` | 100 USD | 50 / 100 / 200 | |
| `max_cost_to_risk` | 0,25 | 0,10 / 0,15 / 0,25 | rapportera antal möjliga affärer **och** netto-EV per nivå |
| `min_shares_for_partial` | 3 | – | affärer utan delvinst rapporteras separat (§2.4) |
| `max_partial_skipped_share` | 0,25 | – | flaggar körning som icke-representativ |
| `risk_overshoot_tolerance` | 0,25 | – | reduktion efter fyllning (§2.1.7) |
| `daily_loss_limit_pct` | 0,03 | på / av | |
| `weekly_loss_limit_pct` | 0,06 | på / av | |
| `max_drawdown_halt_pct` | 0,15 | 0,10 / 0,15 / 0,20 | procent, inte belopp |
| `halt_cooldown_days` | 10 | endast i `[H]`-varianten `auto_resume` | grundmodellen har ingen automatisk återstart |
| `earnings_blackout_days` | 5 | av / 5 | variant, ej default |
| `max_per_sector` | 2 | av / 2 | kräver sektordata |
| `commission_model` | IBKR Tiered | Tiered / Fixed | delvis verifierat: min 0,35 USD resp. 1 USD per order (ordinarie prislista); övrigt Q-003 |
| `slippage_bps`, `spread_bps` | – | – | Q-003 |
| `fx_mode` | – | `usd_balance` / `per_trade` | båda körs tills Q-008 avgör |
| `fx_rate`, `fx_cost_bps` | – | – | Q-003, Q-008 |
| `isk_schablon_rate`, `isk_tax_free_allowance` | – | – | användarparametrar per år; verifiera hos Skatteverket |

Rapportering per körning: antal affärer, andel avstådda per orsak (`shares = 0`, `min_position_value`, `max_cost_to_risk`, portföljgräns), antal `risk_overshoot` och `partial_skipped`, EV efter kostnader totalt och för `partial_skipped` separat, avgifter som andel av bruttoresultat, max drawdown, sämsta dagsresultat, `halted` (ja/nej, datum).

## 4. Kvarstående frågor som blockerar livehandel

| Fråga | Blockerar |
|---|---|
| Q-001 IBKR API-orderläggning på ISK | all automatisk exekvering |
| Q-003 kontospecifika avgifter, växling, datafeed-kostnader | korrekt kostnadsmodell |
| Q-007 fraktionshandel på svenskt ISK hos IBKR | positionsstorlek vid litet kapital |
| Q-008 USD-saldo på svenskt ISK hos IBKR | FX-kostnad per affär vs. engång |
| Orderavstämning och dubblettskydd (§2.3) implementerade och testade mot paper trading | livehandel |
| Manuell återstart efter drawdown-stopp (§2.6) i drift | livehandel |
| Verifierat att 10 000 SEK ger tillräckligt många genomförbara affärer efter §2.2 | ekonomisk genomförbarhet; annars krävs mer kapital före livehandel |

## Versionshistorik
- 0.2 (2026-10-25): efter granskning av v0.1. §2.1 kontroll före order och definierad hantering vid överskridande efter fyllning. §2.4 affärer utan delvinst rapporteras separat. §2.6 `HALTED` konsekvent utan automatisk återstart; `auto_resume` som `[H]`-variant. §2.7 korrigerat: `max_open_risk_pct` begränsar planerad risk, inte verklig gap-förlust. §2.8 två valutascenarier (`fx_mode`), ISK-skatt på kapitalunderlag med kvartalsvärden och insättningar.
- 0.1 (2026-10-25): första utkast efter projektägarens och ChatGPT:s granskning av förslaget i chatt.
