# STRATEGY_SPEC – Qullamaggie long breakout

**Version:** 0.2.1 (utkast, 2026-10-25). Granskad en gång (ChatGPT, v0.1); åtgärder införda, ej slutgodkänd. Backtestresultat refererar alltid till specversion och entry-modell.

Primärkälla för alla `[Q]`-regler: Kullamägi, *3 TIMELESS setups that have made me TENS OF MILLIONS*, <https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/> (läst 2026-10-25). Endast breakout-setupen; Episodic Pivot och Parabolic Short ingår inte. Videon (<https://www.youtube.com/watch?v=xx8GvtAxilk>) är inte genomgången och åberopas inte.

## Taggar och konventioner

- `[Q]` dokumenterad Qullamaggie-regel, med citat eller referat från källan.
- `[T]` vår mekaniska tolkning av något han bedömer visuellt eller lämnar ospecificerat.
- `[H]` otestad hypotes utan stöd i källan.
- **Datakrav:** `daily` = testbar med dagsdata (OHLCV). `intraday` = kräver intradagsdata. Regler märkta `intraday` definieras här men testas inte i fas 6.
- **Parametrar** skrivs `snake_case` och är de namn som används i `configs/`. Defaultvärden i detta dokument är förslag, inte beslut. Parametrar utan stöd i källan listas i §13.
- **Tidskonvention:** alla features och filter beräknas på data t.o.m. stängning dag T−1. Beslut tas före öppning dag T. Fyllning sker dag T. Ingen regel får läsa data från dag T eller senare vid beslut.

## 1. Universum `daily`

- `[H]` Marknad: USA (NYSE, Nasdaq, NYSE American). Bekräftas av Q-001–Q-004.
- `[T]` Instrumenttyp: endast stamaktier. ETF:er, ADR:er, SPAC:er, warrants, preferensaktier och fonder exkluderas. Källan nämner inget instrumentfilter.
- `[T]` Likviditet: median dollaromsättning över 20 handelsdagar ≥ `min_dollar_volume`. Pris ≥ `min_price`. Källan anger inga likviditetskrav; detta filter finns för att kostnadsmodellen och fyllnadsantagandena ska hålla.
- Point-in-time: universumet för dag T består av bolag som var noterade dag T−1, inklusive senare avnoterade. Listor med endast nu noterade bolag får inte användas.

## 2. Momentum och relativ styrka `daily`

- `[Q]` Föregående uppgång: "This move can be anywhere from 30-100%+ and usually lasts a few days to a few weeks", inom de senaste 1–3 månaderna. Inga formler, fönster eller gränsvärden anges i källan.
- `[T]` Mekanisering av föregående uppgång: `max(close[T-1-prior_move_window : T-1]) / min(close i samma fönster, före maxet) − 1 ≥ min_prior_move`. Default `min_prior_move = 0.30` (källans nedre gräns), `prior_move_window = 63` handelsdagar (≈ 3 månader, källans övre gräns). Primär regel i v0.2.
- `[Q]` Ledarurval: källan scannar "top 1-2% of stocks by performance over 1-, 3-, and 6-month windows".
- `[T]` Mekanisering av ledarurval: percentilrankning av avkastning över 21, 63 och 126 handelsdagar inom dagens point-in-time-universum, krav topp `top_pct`. **Variant, ej primär:** kräver hela universumet dag för dag; testas efter den absoluta regeln.
- `[Q]` Priset "surfar" de stigande 10- och 20-dagars MA och rör ibland 50-dagars under konsolideringen. Inget MA-krav formuleras som regel i källan.
- `[T]` Trendfilter: `close[T-1] > SMA50[T-1]` och `SMA50[T-1] > SMA50[T-11]`. Strängare krav som `SMA10 > SMA20 > SMA50` är `[H]`.
- Källan nämner inte relativ styrka som begrepp; 1/3/6-månadersrankningen är närmaste motsvarighet.

## 3. Konsolidering `daily`

- `[Q]` "An orderly pullback and consolidation with higher lows and tightening range in the consolidation phase." Längd "usually 2 weeks to 2 months". Formen kan vara flat kanal, symmetrisk eller fallande triangel; han kallar den ibland high-tight flag. Pris "surfar" de stigande 10- och 20-dagars MA.
- `[T]` Konsolideringsfönstret börjar dagen efter `prior_move`-toppen och slutar dag T−1. Längd `consol_days` ∈ [`consol_min_days`, `consol_max_days`], default [12, 45] (källan: 2 veckor–2 månader).
- `[T]` Avsmalnande intervall: dela fönstret i tre lika tredjedelar. För varje dag beräknas `range5 = (max(high, 5 d) − min(low, 5 d)) / close`. Krav: `median(range5, sista tredjedelen) ≤ tightness_ratio × median(range5, första tredjedelen)`. Default `tightness_ratio = 0.6`. Medianen av korta rullande intervall valdes i stället för hela halvans range (v0.1) för att en enstaka extrem stapel inte ska avgöra. Källan ger inget kvantitativt mått; detta är helt vår konstruktion.
- `[T]` Higher lows: `min(low, sista tredjedelen) ≥ min(low, första tredjedelen)`. Ingen ny parameter. Fångar källans "higher lows" grovt; finare struktur (t.ex. serie av stigande svinglägsta) är `[H]`.
- `[T]` Djup: lägsta stängning i fönstret ≥ (1 − `max_consol_depth`) × toppen. Default `max_consol_depth = 0.25`.
- `[T]` Läge: `close[T-1]` inom `max_pct_below_high` av fönstrets högsta. Default `0.10`.
- `[T]` Pris över SMA20 vid T−1 (surf-regeln). Hur ofta priset får stänga under SMA20 inom fönstret: `max_closes_below_sma20`, default 3.
- Fönster kortare än 12 dagar ger tredjedelar på under 4 dagar, där 5-dagarsintervall blir meningslösa; `consol_min_days` får därför inte sättas under 12 om tightness-måttet används.

## 4. Breakout-trigger `daily`

- `[Q]` "Range expansion" ur konsolideringen. Nivå: konsolideringsfönstrets högsta (`breakout_level`).
- `[T]` Trigger dag T: `high[T] > breakout_level`. Med dagsdata vet vi först i efterhand att nivån bröts; därför modelleras entry som stop-order (§5).
- `[H]` Volymkrav vid breakout: `volume[T] ≥ vol_mult × SMA(volume, 20)`. Källan anger inget volymkrav. Default av; testas som variant.
- `[T]` En kandidat får bara triggas en gång per konsolidering. Efter misslyckad breakout (stoppad ut) krävs ny konsolidering enligt §3.

## 5. Entry

### 5.1 `orb_entry` `intraday`
- `[Q]` Entry vid brott av opening range high: "the high of the first 1-minute, 5-minute, or 60-minute candle" (60-minutersstapeln 9:30–10:00). "You can use whatever timeframe or a combination of them." Mestadels marknadsorder. Tidsramvalet är uttryckligen diskretionärt.
- `[T]` v0.2 låser `orb_minutes = 5` som primär variant; 1 och 60 testas när intradagsdata finns.
- `[T]` Samverkan mellan opening range och breakoutnivå (källan beskriver inte fallet där de skiljer sig):
  1. Kandidaten är känd före öppning dag T (§1–3 på data t.o.m. T−1), med `breakout_level` känd.
  2. Vid `09:30 + orb_minutes` är `orb_high` känd. Först då får en order placeras.
  3. Köp-stop placeras vid `trigger = max(orb_high, breakout_level)`. Fylls när priset handlas genom `trigger`, till `trigger × (1 + slippage_bps/10 000)`.
  4. Om `open[T] > breakout_level × (1 + max_gap_entry)` avstås affären. Default `max_gap_entry = 0.05`.
  5. Ingen entry efter `latest_entry_time` (default 15:30). Om priset passerat `trigger` under opening range-fönstret, före orderläggning, används ändå `trigger` som nivå; fyllning sker först om priset handlas genom nivån efter steg 2.
  Fallet `orb_high < breakout_level` innebär att entry sker vid breakoutnivån senare under dagen; fallet `orb_high > breakout_level` innebär att entry sker vid ORB-high (källans primära regel).
- `[Q]` Anticipatory entry före breakout förekommer men beskrivs som mindre effektiv och krävande; ingår inte.
- `[Q]` "Sometimes I buy the retrace" vid missad breakout; ingår inte i v0.2 (`[H]` framtida variant).

### 5.2 `daily_stop_entry` `daily` – surrogat
- `[T]` Stop-order vid `breakout_level` dag T, placerad före öppning. Fyllning: `fill = max(open[T], breakout_level) × (1 + slippage_bps/10 000)` om `high[T] > breakout_level`, annars ingen affär. Om `open[T] > breakout_level × (1 + max_gap_entry)` avstås affären.
- **Detta är en förenklad strategi, inte ett backtest av Qullamaggies opening-range-metod.** Fyllnadspris och stop skiljer sig från ORB, i okänd riktning (se §6). Alla rapporter ska ange `entry_model = daily_stop_entry`.

## 6. Stop-loss

- `[Q]` "Place the stop at the low of the day." Max avstånd: stoppen "should be no wider than" aktiens ADR/ATR (exempel i källan: 5 % ADR → max 5 % stop).
- `[T]` `intraday`: `stop = max(min(low på dag T fram till entry), fill × (1 − adr20))`. Om LOD-stoppen är vidare än ADR används ADR-gränsen.
- `[T]` `daily` surrogat: `stop = max(low[T-1], fill × (1 − adr20))`. Motivering: `low[T]` är okänd vid orderläggning; föregående dags lägsta är den närmaste kända strukturnivån. Om surrogatet ger vidare eller snävare stop än LOD-regeln är **okänt** och beror på gap och intradagsförlopp; det mäts när intradagsdata finns (jämförelse `daily_stop_entry` mot `orb_entry` på samma kandidater).
- `[T]` Invariant: `stop < fill` krävs alltid, annars avstås affären. Med definitionerna ovan gäller det automatiskt (`fill ≥ breakout_level ≥ high[T-1] > low[T-1]` och `fill × (1 − adr20) < fill`), men kravet kontrolleras i kod och test.
- `[T]` ADR20 = medelvärde över 20 dagar av `high/low − 1`.
- `[T]` Minsta stop-avstånd `min_stop_pct` (default 0.01); snävare stop → affären avstås (inte stoppen vidgad).
- `[T]` Stop-fyllning: om `open ≤ stop` fylls vid `open × (1 − slippage_bps/10 000)`, annars om `low ≤ stop` vid `stop × (1 − slippage_bps/10 000)`.

### 6.1 Exekveringsordning per dag `daily`
Gäller `daily_stop_entry`. Varje handelsdag T behandlas i denna ordning, och ett steg får bara läsa data som är känd när steget inträffar:

1. **Före öppning:** exits beslutade vid stängning T−1 (delvinst, trailing) läggs som marknadsorder; nya kandidater (§1–4 på data t.o.m. T−1) får köp-stop vid `breakout_level`; positionsstorlek enligt §7 med `equity[T-1]`.
2. **Öppning:** exit-order fylls vid `open[T]` minus slippage. Stop-order på befintliga positioner: om `open[T] ≤ stop` fylls vid `open[T]`.
3. **Under dagen, befintliga positioner:** om `low[T] ≤ stop` → stoppad vid `stop`. Entry och stop samma dag kan inte båda vara aktiva för en befintlig position.
4. **Under dagen, nya entries:** om `high[T] > breakout_level` (och gapregeln tillåter) → fylld. Därefter, **samma dag:** om `low[T] ≤ stop` behandlas affären som stoppad ut dag T. Dagsdata avslöjar inte om stoppen nåddes före eller efter fyllningen; det konservativa antagandet (förlust −1R minus kostnader) används. Antalet sådana tvetydiga affärer rapporteras separat.
5. **Stängning:** uppdatera `equity[T]`; beräkna delvinst- och trailing-beslut (§8) på `close[T]` för exekvering i steg 1 nästa dag.

Har en position både en exit-order (steg 1) och en aktiv stop samma dag, fylls exit-ordern vid öppningen (steg 2) och stoppen blir utan verkan. Delvinst flyttar stoppen till breakeven först efter att delvinsten fyllts.

## 7. Positionsstorlek `daily`

Normativ källa: `RISK_MODEL.md` (§1 Qullamaggies principer, §2 våra regler, §3 testparametrar). Sammanfattning: `shares = floor(equity × risk_per_trade / (fill − stop))`, begränsat av `max_position_pct`, portföljgränser och avgiftsandel; `equity` = kontovärde vid stängning T−1. `[Q]` Risk per affär 0,25–1,5 %, max 30 % per aktie. Parametervärden är testvariabler (D-008, D-009).

## 8. Delvinst och trailing stop `daily`

- `[Q]` Efter 3–5 dagar: sälj en tredjedel till hälften, flytta stop till breakeven.
- `[T]` Mekanisering: vid stängning dag `entry_day + partial_days` beslutas försäljning av `partial_fraction`; fylls vid öppning nästa dag (§6.1 steg 1–2). Därefter stop på resten = `fill`. Default `partial_fraction = 0.333`, `partial_days = 3`.
- `[Q]` Trail resten på 10- eller 20-dagars MA; exit vid första stängning under. Valet "depends on how fast the stock is"; nybörjare använder 10-dagars.
- `[T]` v0.2 låser `trail_ma = 10`. Beslut vid `close[T] < SMA10[T]`; fylls vid `open[T+1]` minus slippage. 20-dagars testas som variant.
- `[T]` Beslut tas på stängningskurs, exekvering nästa öppning. Intradagsexekvering av delvinst är `intraday` och ingår inte.

## 9. Marknadsfilter `daily`

- `[Q]` Stora R-multiplar beskrivs som vanliga "in a bullish market"; i svaga marknader "fewer or no trades". Ingen definition ges.
- `[H]` Mekaniskt: nya positioner tillåts endast om `close(index)[T-1] > SMA(index, regime_ma)`. Default `regime_index = QQQ`, `regime_ma = 20`. Test: strategin utan filter är baseline; filtret måste förbättra EV efter kostnader för att behållas.

## 10. Kostnadsmodell

Alla kostnader är parametrar och fylls av Q-003. Default tills dess: `commission_per_share`, `min_commission`, `fx_spread_bps` (per sida, USD/SEK), `slippage_bps` (per sida), `borrow`: ej relevant (long only). Kostnader dras per fyllning, inte i efterhand på totalen. Backtest rapporterar alltid resultat med och utan kostnader.

## 11. Testprotokoll

1. **Universum:** point-in-time enligt §1, inklusive avnoterade bolag. Datakälla och täckning dokumenteras i `data/README.md`.
2. **Tidskonvention:** enligt inledningen. Varje feature har ett test som bevisar att den inte läser data ≥ T.
3. **Period:** hela tillgängliga historiken delas i träning / validering / test utan överlapp, tidsordnade. Testperioden rörs inte förrän parametrarna låsts på träning + validering.
4. **Walk-forward:** parametrar väljs på rullande fönster (`train_years`, `test_years`) och utvärderas på nästföljande fönster. Rapportera **fördelningen** av resultat över fönster, inte bara aggregatet.
5. **Parameterstabilitet:** för varje parameter rapporteras resultatet i ett grannskap kring valt värde. En parameter vars grannar presterar markant sämre betraktas som överanpassad.
6. **Rapport:** specversion, entry-modell, kostnadsparametrar, antal affärer, EV i R, profit factor, träffprocent, genomsnittlig vinnare/förlorare i R, max drawdown, CAGR, resultat per år, resultat med/utan marknadsfilter, antal tvetydiga samma-dagsaffärer (§6.1 steg 4).
7. **Osäkerhet:** affärer är inte oberoende (de klustrar i tid och i starka marknader). 95 %-intervall för EV beräknas med blockbootstrap över kalendermånader, inte per affär. Resultat redovisas även per marknadsregim (§9-filtret på/av som indelning), så att det syns om allt edge kommer från ett fåtal perioder.
8. **Kostnadskänslighet:** varje rapport körs med kostnadsmodellen ×1,0, ×1,5 och ×2,0. En strategi som bara är lönsam vid ×1,0 räknas inte som robust.
9. **Reproducerbarhet:** varje rapport genereras av ett kommando från rådata, med commit-hash.

## 12. Acceptans- och avbrottskriterier

Sätts **före** första backtestet och ändras inte under fas 6. Nivåerna nedan är förslag (`[T]`), inte verifierade gränser för denna strategi; de blir beslut (D-xxx) först efter projektägarens godkännande.

Två frågor besvaras separat:

**A. Finns statistisk evidens för positivt edge?** Krav: nedre gränsen i 95 %-intervallet för EV efter kostnader (§11.7) > 0 R vid kostnad ×1,0, och punktskattningen > 0 vid ×1,5. Utan A är B ointressant.

**B. Räcker edgen för projektets affärsmål?**

| Kriterium | "Inget edge" – avbryt eller omdefiniera | "Värt att gå vidare" |
|---|---|---|
| Antal affärer i testet | < 300 | ≥ 300 |
| EV per affär efter kostnader (punktskattning) | ≤ 0,05 R | ≥ 0,15 R |
| Profit factor efter kostnader | < 1,1 | ≥ 1,3 |
| Andel walk-forward-fönster med positivt EV | < 50 % | ≥ 70 % |
| Max drawdown vid `risk_per_trade = 0.005` | > 25 % | ≤ 20 % |

Resultat mellan kolumnerna är "oklart" och kräver ett nytt beslut, inte fler parametersök. Walk-forward-fönster överlappar inte i testperiod men delar träningsdata; andelen positiva fönster är därför ett grovt mått och ersätter inte A.

## 13. Öppna parametrar

Utan stöd i källan; värden ovan är startförslag.

- P-001 `min_dollar_volume`, `min_price` (§1)
- P-002 `tightness_ratio`, `max_consol_depth`, `max_pct_below_high`, `max_closes_below_sma20` (§3) – kalibreras mot handplockade historiska exempel i fas 5. Hypoteser från visuell granskning av 15 NVDA-diagram 2026-10-27 (ett bolag, tre kandidatdagar – inget är bevisat, inga trösklar ändrade):
  - `[H]` P-002a: `max_pct_below_high = 0.10` kan vara för generöst. NVDA 2017-06-27 klarade 9/9 vid 9,7 % under toppen och såg kortsiktigt svag ut. Att testa: 0,05–0,07, eller ett kompletterande krav på korttidsmomentum (close över SMA10, positiv 5-dagarsförändring) – båda finns nu som diagnostiska mått i setup-bilderna.
  - `[H]` P-002b: `max_closes_below_sma20 = 3` kan vara för strängt. NVDA jan–feb 2017 fälldes på detta trots stark föregående uppgång. Att testa: 5, eller att räkna andelen av fönstret i stället för antal (längre fönster tillåts fler).
  - `[H]` P-002c: `prior_move_window = 63` med krav på 30 % kan missa konsolideringar efter en uppgång som ligger strax utanför fönstret (NVDA okt 2017, juni 2018). Att testa: 90 dagar, eller att mäta uppgången från fönstrets lägsta i stället för lägsta *före* toppen. Risk: fler falska kandidater.
  - `[H]` P-002d: samma formation ger kandidater flera dagar i rad (NVDA 2016-08-31 och 09-01). Det är inte en tröskelfråga utan hör till §4: en kandidat per konsolidering (nu synlig som `setup_group_id`).
  - Kalibrering kräver fler bolag än tre megacaps; se SCANNER.md §9.
- P-003 `vol_mult` och om volymfilter ska användas alls (§4)
- P-004 `max_gap_entry`, `slippage_bps`, `latest_entry_time` (§5, §10)
- P-005 riskparametrar (§7) – testas enligt `RISK_MODEL.md` §3
- P-006 `partial_days` inom 3–5 och `partial_fraction` inom 1/3–1/2 (§8)
- P-007 `trail_ma` 10 vs 20 (§8)
- P-008 `regime_index`, `regime_ma`, filter på/av (§9)
- P-009 `orb_minutes` (§5.1, kräver intradagsdata)
- P-010 Kostnadsparametrar (§10) – Q-003
- P-011 Acceptansnivåer i §12

## 14. Koppling till ML

Scannern enligt §1–4 skriver varje kandidat (även de som inte triggas) till kandidattabellen med `candidate_id`, ticker, dag T, specversion och feature-snapshot vid T−1. Utfall enligt §5–8 lagras separat per `candidate_id` och entry-modell. Detta är träningsdata för Modell B (affärsutvärdering). Modell A (mönsterigenkänning) tränas på OHLCV-fönster och grafbilder renderade från samma fönster som §3; etikettkällan är Q-006. Ingen ML-funktion påverkar §6–7.

## Versionshistorik

- 0.2.1 (2026-10-25): §7 ersatt med hänvisning till `RISK_MODEL.md`. Inga regeländringar.
- 0.2 (2026-10-25): efter granskning av v0.1. §2: `[Q]` och `[T]` separerade. §3: tightness-mått bytt till median av rullande 5-dagarsintervall, higher-lows-regel tillagd, `consol_min_days` 10→12. §5.1: samverkan ORB-high/breakoutnivå och tidsordning definierad. §5.2/§6: påståendet om "vidare stop" borttaget, invariant `stop < fill`, §6.1 exekveringsordning med konservativ hantering av tvetydiga samma-dagsutfall. §8: beslut vid stängning, exekvering nästa öppning. §11: blockbootstrap, regimuppdelning, kostnadskänslighet. §12: statistisk evidens (A) skild från affärsmål (B). Ej slutgodkänd.
- 0.1 (2026-10-25): första utkast med `[Q]`-regler ur primärkällan, daily/intraday-separation, testprotokoll, acceptanskriterier och öppna parametrar. Ej granskad.
- 0.0 (2026-10-12): skelett.
