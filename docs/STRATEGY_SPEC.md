# STRATEGY_SPEC – Qullamaggie long breakout

**Version:** 0.1 (utkast, 2026-10-25). Ej granskad. Backtestresultat refererar alltid till specversion och entry-modell.

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

- `[Q]` "This move can be anywhere from 30-100%+ and usually lasts a few days to a few weeks", inom de senaste 1–3 månaderna. Mekaniskt: `max(close[T-1-prior_move_window : T-1]) / min(close i samma fönster, före maxet) − 1 ≥ min_prior_move`. Default `min_prior_move = 0.30`, `prior_move_window = 63` handelsdagar.
- `[Q]` Källan scannar "top 1-2% of stocks by performance over 1-, 3-, and 6-month windows". Mekaniskt: percentilrankning av avkastning över 21, 63 och 126 dagar inom dagens point-in-time-universum, krav topp `top_pct`. **Variant, ej primär i v0.1:** rankning kräver hela universumet dag för dag och testas efter den absoluta regeln ovan.
- `[T]` Trendfilter: `close[T-1] > SMA10 > SMA20 > SMA50` är **inte** ett krav. Källan säger bara att priset "surfar" 10- och 20-dagars MA och ibland rör 50-dagars under konsolideringen. v0.1 kräver endast `close[T-1] > SMA50[T-1]` och `SMA50` stigande över 10 dagar. Strängare varianter är `[H]`.
- Källan nämner inte relativ styrka som begrepp; 1/3/6-månadersrankningen är närmaste motsvarighet.

## 3. Konsolidering `daily`

- `[Q]` "An orderly pullback and consolidation with higher lows and tightening range in the consolidation phase." Längd "usually 2 weeks to 2 months". Formen kan vara flat kanal, symmetrisk eller fallande triangel; han kallar den ibland high-tight flag. Pris "surfar" de stigande 10- och 20-dagars MA.
- `[T]` Konsolideringsfönstret börjar dagen efter `prior_move`-toppen och slutar dag T−1. Längd `consol_days` ∈ [`consol_min_days`, `consol_max_days`], default [10, 45].
- `[T]` Avsmalnande intervall: range (högsta−lägsta) i fönstrets senare hälft ≤ `tightness_ratio` × range i första hälften. Default `tightness_ratio = 0.6`. Källan ger inget kvantitativt mått; detta är helt vår konstruktion.
- `[T]` Djup: lägsta stängning i fönstret ≥ (1 − `max_consol_depth`) × toppen. Default `max_consol_depth = 0.25`.
- `[T]` Läge: `close[T-1]` inom `max_pct_below_high` av fönstrets högsta. Default `0.10`.
- `[T]` "Higher lows" mekaniseras inte i v0.1; djup- och lägeskraven antas fånga större delen. Separat higher-lows-test är `[H]`.
- `[T]` Pris över SMA20 vid T−1 (surf-regeln). Hur ofta priset får stänga under SMA20 inom fönstret: `max_closes_below_sma20`, default 3.

## 4. Breakout-trigger `daily`

- `[Q]` "Range expansion" ur konsolideringen. Nivå: konsolideringsfönstrets högsta (`breakout_level`).
- `[T]` Trigger dag T: `high[T] > breakout_level`. Med dagsdata vet vi först i efterhand att nivån bröts; därför modelleras entry som stop-order (§5).
- `[H]` Volymkrav vid breakout: `volume[T] ≥ vol_mult × SMA(volume, 20)`. Källan anger inget volymkrav. Default av i v0.1; testas som variant.
- `[T]` En kandidat får bara triggas en gång per konsolidering. Efter misslyckad breakout (stoppad ut) krävs ny konsolidering enligt §3.

## 5. Entry

### 5.1 `orb_entry` `intraday`
- `[Q]` Entry vid brott av opening range high: "the high of the first 1-minute, 5-minute, or 60-minute candle" (60-minutersstapeln 9:30–10:00). "You can use whatever timeframe or a combination of them." Mestadels marknadsorder. Tidsramvalet är uttryckligen diskretionärt.
- `[T]` v0.1 låser `orb_minutes = 5` som primär variant; 1 och 60 testas när intradagsdata finns.
- `[T]` Entry endast om ORB-high ≥ `breakout_level` (annars är det ingen breakout-dag).
- `[Q]` Anticipatory entry före breakout förekommer men beskrivs som mindre effektiv och krävande; ingår inte.
- `[Q]` "Sometimes I buy the retrace" vid missad breakout; ingår inte i v0.1 (`[H]` framtida variant).

### 5.2 `daily_stop_entry` `daily` – surrogat
- `[T]` Stop-order vid `breakout_level` dag T. Fyllning: `fill = max(open[T], breakout_level) × (1 + slippage_bps/10 000)`. Om `open[T] > breakout_level × (1 + max_gap_entry)` avstås affären (gap för stort). Default `max_gap_entry = 0.05`.
- **Detta är inte ett test av opening-range-metoden.** Fyllnadspris, stop-avstånd och därmed R-fördelning skiljer sig systematiskt från ORB. Alla rapporter från fas 6 ska ange `entry_model = daily_stop_entry`.

## 6. Stop-loss

- `[Q]` "Place the stop at the low of the day." Max avstånd: stoppen "should be no wider than" aktiens ADR/ATR (exempel i källan: 5 % ADR → max 5 % stop).
- `[T]` `intraday`: stop = `min(low på dag T fram till entry)`, begränsat till `fill × (1 − adr20)`. Om LOD-stoppen är vidare än ADR används ADR-gränsen.
- `[T]` `daily` surrogat: stop = `max(low[T-1], fill × (1 − adr20))`. Motivering: `low[T]` är okänd vid entry; föregående dags lägsta är den närmaste strukturnivån som är känd. Detta ger i regel **vidare** stop än LOD-regeln och därmed lägre R per vinnare.
- `[T]` ADR20 = medelvärde över 20 dagar av `high/low − 1`.
- `[T]` Gap: stoppen simuleras som stop-order. Om `open < stop` fylls den vid `open × (1 − slippage_bps/10 000)`, annars vid `stop × (1 − slippage_bps/10 000)`.
- `[T]` Minsta stop-avstånd `min_stop_pct` (default 0.01) för att undvika orimlig positionsstorlek.

## 7. Positionsstorlek `daily`

- `[Q]` Risk per affär "usually 0.25-1%", för konton under några miljoner "0.5-1.5%". Max 30 % av kontot i en aktie över natten; typiskt 10–20 %.
- `[T]` `shares = floor(equity × risk_per_trade / (fill − stop))`, därefter `min(shares, equity × max_position_pct / fill)`. Default `risk_per_trade = 0.005`, `max_position_pct = 0.20`. Slutliga värden: Q-005.
- `[T]` `max_open_positions` (default 8) och `max_gross_exposure` (default 1.0, ingen hävstång). Källan anger inga portföljgränser utöver 30 % per aktie.
- `[T]` `equity` = kontovärde vid stängning T−1 inklusive orealiserat resultat.

## 8. Delvinst och trailing stop `daily`

- `[Q]` Efter 3–5 dagar: sälj en tredjedel till hälften, flytta stop till breakeven. Mekaniskt: sälj `partial_fraction` av positionen vid stängning dag `entry_day + partial_days`. Default `partial_fraction = 0.333`, `partial_days = 3`. Stop på resten = `fill`.
- `[Q]` Trail resten på 10- eller 20-dagars MA; exit vid första stängning under. Valet "depends on how fast the stock is"; nybörjare använder 10-dagars.
- `[T]` v0.1 låser `trail_ma = 10`. Exit-order läggs dagen efter stängning under MA och fylls vid `open[T+1]` minus slippage. 20-dagars testas som variant.
- `[T]` Delvinst och trailing simuleras på stängningskurs; intradagsexekvering av delvinst är `intraday` och ingår inte.

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
6. **Rapport:** specversion, entry-modell, kostnadsparametrar, antal affärer, EV i R, profit factor, träffprocent, genomsnittlig vinnare/förlorare i R, max drawdown, CAGR, resultat per år, resultat med/utan marknadsfilter.
7. **Reproducerbarhet:** varje rapport genereras av ett kommando från rådata, med commit-hash.

## 12. Acceptans- och avbrottskriterier

Sätts **före** första backtestet och ändras inte under fas 6. Förslag (`[T]`, beslut krävs, blir D-xxx):

| Kriterium | "Inget edge" – avbryt eller omdefiniera | "Värt att gå vidare" |
|---|---|---|
| Antal affärer i testet | < 300 | ≥ 300 |
| EV per affär efter kostnader | ≤ 0,05 R | ≥ 0,15 R |
| Profit factor efter kostnader | < 1,1 | ≥ 1,3 |
| Andel walk-forward-fönster med positivt EV | < 50 % | ≥ 70 % |
| Max drawdown vid `risk_per_trade = 0.005` | > 25 % | ≤ 20 % |

Resultat mellan kolumnerna är "oklart" och kräver ett nytt beslut, inte fler parametersök.

## 13. Öppna parametrar

Utan stöd i källan; värden ovan är startförslag.

- P-001 `min_dollar_volume`, `min_price` (§1)
- P-002 `tightness_ratio`, `max_consol_depth`, `max_pct_below_high`, `max_closes_below_sma20` (§3) – kalibreras mot handplockade historiska exempel i fas 5
- P-003 `vol_mult` och om volymfilter ska användas alls (§4)
- P-004 `max_gap_entry`, `slippage_bps` (§5, §10)
- P-005 `risk_per_trade`, `max_position_pct`, `max_open_positions` (§7) – beror på Q-005
- P-006 `partial_days` inom 3–5 och `partial_fraction` inom 1/3–1/2 (§8)
- P-007 `trail_ma` 10 vs 20 (§8)
- P-008 `regime_index`, `regime_ma`, filter på/av (§9)
- P-009 `orb_minutes` (§5.1, kräver intradagsdata)
- P-010 Kostnadsparametrar (§10) – Q-003
- P-011 Acceptansnivåer i §12

## 14. Koppling till ML

Scannern enligt §1–4 skriver varje kandidat (även de som inte triggas) till kandidattabellen med `candidate_id`, ticker, dag T, specversion och feature-snapshot vid T−1. Utfall enligt §5–8 lagras separat per `candidate_id` och entry-modell. Detta är träningsdata för Modell B (affärsutvärdering). Modell A (mönsterigenkänning) tränas på OHLCV-fönster och grafbilder renderade från samma fönster som §3; etikettkällan är Q-006. Ingen ML-funktion påverkar §6–7.

## Versionshistorik

- 0.1 (2026-10-25): första utkast med `[Q]`-regler ur primärkällan, daily/intraday-separation, testprotokoll, acceptanskriterier och öppna parametrar. Ej granskad.
- 0.0 (2026-10-12): skelett.
