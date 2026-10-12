# STRATEGY_SPEC – Qullamaggie long breakout

**Version:** 0.0 (skelett). Reglerna definieras i fas 2 och blir v0.1.
Backtestresultat refererar alltid till en specversion och en entry-modell.

## Taggar

- `[Q]` – dokumenterad Qullamaggie-regel, med källa.
- `[T]` – vår mekaniska tolkning av något Qullamaggie bedömer visuellt eller diskretionärt.
- `[H]` – otestad hypotes.

Varje regel skrivs som: tagg, definition som går att implementera entydigt, parametrar med de namn som används i `configs/`, samt källa eller motivering.

## 1. Universum
Marknad, börser, minsta pris, minsta omsättning, exkluderingar (t.ex. ETF:er, SPAC:er). Status: ej definierad (beror på Q-001–Q-004).

## 2. Momentum och relativ styrka
Hur "betydande tidigare uppgång" och "hög relativ styrka" mäts, över vilka perioder. Status: ej definierad.

## 3. Konsolidering
Längd, djup, avtagande volatilitet, läge relativt tidigare topp och glidande medelvärden. Status: ej definierad.

## 4. Breakout-trigger
Vilken nivå som ska passeras och vilken bekräftelse som krävs (pris, volym). Status: ej definierad.

## 5. Entry-modeller
- `daily_stop_entry` `[T]` – förenklad modell för dagsdata: stop-order vid breakoutnivån, fylls till max(öppning, nivå) plus slippage. Är **inte** ett test av opening-range-metoden.
- `orb_entry` – opening-range-breakout på intradagsdata. Kräver intradagsdata och definieras när sådan finns.

Status: ej definierade i detalj.

## 6. Stop-loss
Strukturbaserad initial stop; maximalt avstånd. Status: ej definierad.

## 7. Positionsstorlek
Risk per affär i procent av kapital; maximal positionsstorlek. Status: ej definierad (Q-005).

## 8. Delvinst och trailing stop
Tidpunkt eller nivå för delvinst; trailing-regel för resterande position. Status: ej definierad.

## 9. Marknadsfilter
Regimfilter, t.ex. index relativt glidande medelvärde. Status: ej definierad.

## 10. Kostnadsmodell
Courtage, spread, slippage, valutaväxling. Status: ej definierad (Q-003).

## 11. Acceptans- och avbrottskriterier
Skrivs ned **innan** första backtestet körs: vilka resultat (förväntansvärde i R efter kostnader, max drawdown, antal affärer, stabilitet över delperioder) som räknas som "inget edge" respektive "värt att gå vidare". Status: ej definierade.

## Versionshistorik
- 0.0 (2026-10-12): skelett.
