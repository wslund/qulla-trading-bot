# PROJECT_MASTER – qulla-trading-bot

Projektets långsiktiga minne. Ersätter den ursprungliga projektbeskrivningen helt. Ändras bara vid beslut som påverkar mål, krav eller arkitektur.
Aktuell status: `SESSION_HANDOFF.md`. Beslut och öppna frågor: `DECISIONS.md`. Strategiregler: `STRATEGY_SPEC.md`.

## 1. Mål

Utveckla en automatiserad tradingbot som ger långsiktigt positiv nettoavkastning efter alla kostnader och ökar projektägarens kapital, med strikt money management.

- Kommersiellt tradingprojekt, inte forskning eller teknikexperiment. Teknikval styrs av målet.
- Viktigaste mått: nettoavkastning och risk efter courtage, spread, slippage, valutaväxling och skatt.
- Ingen strategi eller modell antas lönsam förrän den testats på verklig historisk data med realistiska kostnader.
- Systemet ska på sikt scanna ett stort aktieuniversum, identifiera Qullamaggie-liknande breakouts, bedöma vilka som har bäst förutsättningar, handla automatiskt via officiellt mäklar-API på svenskt ISK, hantera risk och återinvestering, och kräva minimal daglig inblandning.

## 2. Strategi

Long-side momentum breakout enligt Kristjan Kullamägi (Qullamaggie). Komponenter: starkt momentum och hög relativ styrka, betydande tidigare uppgång, konsolidering nära toppar med avtagande volatilitet, konstruktiv prisstruktur, breakout över relevant nivå, opening-range-breakout som möjlig entry, strukturbaserad stop-loss, riskbaserad positionsstorlek, delvinst och trailing stop.

Referenser: <https://qullamaggie.com/>, <https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/>, <https://www.youtube.com/watch?v=xx8GvtAxilk>.

Mekaniska regler definieras i `STRATEGY_SPEC.md`. Varje regel taggas `[Q]` dokumenterad Qullamaggie-regel, `[T]` vår mekaniska tolkning av något han bedömer visuellt, eller `[H]` otestad hypotes. En automatiserad version antas inte ge samma resultat som hans diskretionära handel.

## 3. Arkitektur

Src-layout, paketet `tradingbot`.

| Modul | Ansvar |
|---|---|
| `data/` | Hämtning, lagring i Parquet, splitjustering, point-in-time, kvalitetskontroll |
| `scanner/` | Universumfilter, features, regelbaserad setup-detektion → kandidater |
| `backtesting/` | Simulering med kostnadsmodell, R-multiplar, walk-forward, utvärdering |
| `risk/` | Positionsstorlek, exponerings- och förlustgränser; rena funktioner |
| `ml/` (senare) | Modell A och B, tränade på kandidattabellen |
| `brokers/` (senare) | Mäklar-API, orderläggning, avstämning |

Principer:

1. Signalgenerering, riskhantering och orderläggning är separata moduler. Riskmotorn är oberoende av ML; ML får aldrig ändra riskgränser.
2. Backtester och framtida live-motor delar signal- och riskkod men aldrig orderkod.
3. Alla parametrar i `configs/` (YAML). Inga hårdkodade siffror.
4. Enkelhet före generalitet. Ingen databas, mikrotjänster eller molninfrastruktur förrän ett konkret behov finns.
5. Moduler hålls små (riktmärke under 300 rader) så att de kan granskas i en chatt.
6. `research/` är fri yta för experiment. Inget i `src/` importerar därifrån.

### Förberett för ML utan att bygga ML nu

- Scannern producerar kandidater som poster: `candidate_id`, ticker, beslutstidpunkt, regelversion och en feature-snapshot beräknad strikt med data tillgänglig vid beslutstidpunkten. Lagras i Parquet.
- Etiketter (hur formationen ser ut) och utfall (vad som hände efteråt) lagras i separata tabeller kopplade via `candidate_id`. De blandas aldrig.
- Kandidattabellen har samma schema oavsett om den fylls av regelscannern eller senare av en ML-modell, och det är samma schema backtestern läser. Ett ML-filter kan därför läggas mellan scanner och backtester utan att någon av dem ändras.
- Grafbilder för bildbaserade modeller renderas deterministiskt från samma OHLCV-fönster som features, när den fasen börjar.

## 4. Data

Krav: ingen look-ahead, minimal survivorship bias (avnoterade bolag ingår), point-in-time, etiketter skilda från utfall, tidsbaserade tränings-, validerings- och testperioder utan överlapp, reproducerbarhet, dokumenterade källor och tidsstämplar. Historisk data hittas aldrig på.

Dagsdata (OHLCV) används för scanner och tidiga tester. Intradagsdata krävs för att simulera opening-range-entry korrekt. Ett backtest med dagsdata använder en förenklad entry-modell och får aldrig presenteras som ett exakt test av opening-range-metoden. Varje resultat anger specversion och entry-modell.

Dataset V0.2 (framtaget av ChatGPT) är en tidig, inte fullständigt verifierad prototyp och granskas enligt D-006 innan någon kod återanvänds.

## 5. Maskininlärning

Regelbaserad scanner byggs först: den är baseline, kandidatgenerator och måttstock. ML är ett centralt mål som byggs ovanpå:

- **Modell A – mönsterigenkänning.** Lär sig känna igen Qullamaggie-liknande formationer från OHLCV och grafer. Bildbaserade (CNN, ViT) och sekvensbaserade (1D-CNN, andra tidsseriemodeller) ansatser jämförs mot varandra och mot regelscannern. Etikettkälla är en öppen fråga (Q-006).
- **Modell B – affärsutvärdering.** Bedömer vilka kandidater som har bäst förväntat nettoresultat utifrån formation, momentum, volym, volatilitet, sektor, marknadsregim och historiska utfall för liknande setups. Nyheter och fundamenta läggs till först om basmodellen visar edge. Utvärderas på förväntansvärde och risk, inte träffprocent.

ML används bara där det ger mätbart mervärde mot en enklare metod på samma data. Sampelstorleken avgör modellval; tabulära metoder med walk-forward-validering prövas före djupinlärning.

## 6. Money management och risk

Riskmotorn ska hantera: riskbaserad positionsstorlek, max risk per affär, max portföljexponering, begränsning av korrelerade positioner, stop-loss och trailing stop, delvinst, dagliga och totala förlustgränser, tillgängligt kapital, courtage, spread, slippage, valutaväxling, gap och handelsstopp, orderbekräftelse och positionsavstämning, dubblettskydd och nödstopp. Exakta parametrar bestäms utifrån kapital och tester (Q-005).

## 7. Mäklare och ISK

Svenskt ISK är krav för verklig handel. Läge 2026-10-12: Interactive Brokers lanserade svenskt ISK 2025-09-30 (officiellt pressmeddelande). Om deras API tillåter automatiserad orderläggning på ISK-konton är inte verifierat (Q-001). Nordnet tar för närvarande inte emot nya API-kunder. Aktuella villkor kontrolleras alltid i officiell dokumentation. Ingen inofficiell webbläsarautomation. Ingen livehandel utan uttryckligt beslut av projektägaren; paper trading föregår alltid livehandel.

## 8. Teknik och konventioner

Python 3.12, uv med `uv.lock`, Ruff, pytest, GitHub Actions. pandas, NumPy och pyarrow läggs till när datalagret byggs; scikit-learn och PyTorch när ML-fasen börjar.

- Dokumentation, commit-meddelanden och kommentarer i konfigfiler: svenska. Kod, identifierare och docstrings: engelska. Etablerade facktermer behålls på engelska.
- Datafiler, modellvikter, API-nycklar och personuppgifter ligger aldrig i git. Hemligheter via `.env` (mall i `.env.example`).
- En gren per uppgift (`feat/...`, `review/...`), pull request mot `main`, grön CI före merge.
- Viktiga funktioner har tester. "Det fungerar" räknas bara med körbart bevis: test eller skript med utdata.

## 9. Arbetssätt med AI-assistenter

Projektägaren arbetar i vanliga chattar med Claude (främst kod och systemutveckling) och ChatGPT (främst research, dataset, analys och granskning). Rollerna är flexibla; båda får föreslå lösningar och granska varandra. Vid olika förslag jämförs alternativen kritiskt och det bäst motiverade väljs.

Dokumentsystem för att spara tokens:

- `SESSION_HANDOFF.md` startar varje ny chatt och skrivs över i slutet av varje chatt. Max en sida.
- `PROJECT_MASTER.md` bifogas bara när en ny fas börjar eller uppgiften rör arkitektur.
- `DECISIONS.md` är append-only. `STRATEGY_SPEC.md` är versionerad.
- Kod granskas som diffar, inte som beskrivningar. Ingen fil ägs av två assistenter samtidigt.

Chattregler: en uppgift per chatt; upprepa inte projektbeskrivningen; redovisa resultat, inte långa resonemang; skilj verifierat från osäkert; hitta aldrig på data, resultat eller API-funktioner; föreslå enklare lösningar när de räcker; bygg inget extra; be om förtydligande bara när det behövs; avsluta större uppgifter med uppdaterad handoff.

## 10. Utvecklingsfaser

0. Initialisering: skelett, CI, dokument. Klar 2026-10-12.
1. Research: mäklare/ISK/API, dataleverantör, kostnadsmodell (Q-001–Q-003).
2. `STRATEGY_SPEC.md` v0.1 inklusive acceptans- och avbrottskriterier.
3. Granskning av dataset V0.2.
4. Datalager: dagsdata, Parquet, point-in-time, kvalitetstester.
5. Regelbaserad scanner.
6. Backtester → första resultat → go/no-go mot kriterierna från fas 2.
7. Därefter, varje steg med eget beslut: Modell B, Modell A, paper trading, mäklarintegration, livehandel.
