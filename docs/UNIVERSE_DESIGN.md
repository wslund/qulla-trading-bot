# UNIVERSE_DESIGN – point-in-time-aktieuniversum (P8-förstudie)

**Version:** 0.1 (förstudie, 2026-11-01). Ingen implementation. Underlag: `STRATEGY_SPEC.md` §1, `PORTFOLIO_DESIGN.md` §11 och P8, `DATA_PILOT.md` §6–7 (det finns ingen `DATA_POLICY.md` i repot; användningsvillkoren står i `DATA_PILOT.md` §7), `DECISIONS.md` Q-002/D-011, datalagret (`data/`), scannern (`scanner/`) och portföljmotorn (`backtesting/`). Taggar: `[V]` verifierat mot leverantörens egen dokumentation under förstudien (datum anges), `[U]` uppgift från tredje part eller ej verifierad, `[D]` designval att fastställa.

Grundregel från spec §1: *universumet för dag T består av bolag som var noterade dag T−1, inklusive senare avnoterade; listor med endast nu noterade bolag får inte användas.* D-012: utan detta är varje resultat ett funktionstest.

## 0. Två nivåer – och varför de inte får blandas ihop

| | **Forskningsuniversum** (billigt, för att driva pipelinen) | **Utvärderingsuniversum** (för resultat enligt D-012) |
|---|---|---|
| Syfte | köra scanner/portfölj på hundratals tickers, hitta buggar, mäta `too_small`/`cost_to_risk`-andelar | skatta EV, PF, drawdown; walk-forward; §12-kriterierna |
| Krav | många tickers, avnoterade *får* saknas men ska räknas | varje handelsdag: komplett lista över noterade stamaktier med start/slut, inklusive konkurs/uppköp, utan framtidsinformation |
| Källa | Massive/Polygon `All Tickers` med `date` + Alpaca SIP-priser, eller EODHD | Norgate Platinum **eller** Sharadar, verifierat med stickprov mot SEC-källor (§8) |
| Rubrik i rapport | "funktionstest, ej evidens" (som i dag) | "point-in-time, universum vX, täckning Y %" – först efter kvalitetstesterna i §8 |

Ingen källa i §5 beskrivs som point-in-time-komplett förrän §8:s tester körts på den; det är en egenskap vi mäter, inte en etikett vi läser.

## 1. Fyra lager som hålls isär

| Lager | Fråga | Avgörs av | Information känd |
|---|---|---|---|
| **L1 Noterad** | fanns instrumentet på NYSE/Nasdaq/NYSE American dag T−1? | noteringsregister (start/slut per instrument) | T−1 |
| **L2 Handlingsbar** | gick det att köpa/sälja normalt? (inte handelsstoppad, inte sista dag före avnotering, inte under 1 USD-pennystock-regel om vi vill) | prisdata (fanns stapel, volym > 0), handelsstopplista om tillgänglig | T−1 |
| **L3 Pris-/volymfilter** | spec §1: `min_price`, median dollaromsättning 20 dagar ≥ `min_dollar_volume`, instrumenttyp = stamaktie | features (`dollar_volume_20d`, `close_prev`) + instrumenttyp från registret | T−1 |
| **L4 Signalfilter** | spec §2–4 (momentum, konsolidering, trigger) | scannern, oförändrad | T−1 |

L1–L3 är universumets ansvar (`Universe.members(T−1)` returnerar instrument som klarar L1 och L2; L3 beräknas av scannern men parametrarna ägs av spec §1). L4 ändras inte av detta arbete. Att skilja dem gör att rapporten kan visa *varför* en dag har N kandidater: hur många instrument var noterade, hur många handlingsbara, hur många klarade pris/volym, hur många gav signal.

## 2. Identifierare

- **`instrument_id`** (`[D]`): egen surrogatnyckel per *aktieklass* (share class), stabil över tickerbyten och namnbyten. Tickern är ett attribut med giltighetsintervall, aldrig nyckel.
- Koppling till externa id: `share_class_figi` (öppen standard, finns i Massive `[V]`), `cik` (SEC, bolagsnivå – inte aktieklass), `permaticker` (Sharadar, `[U]` ej verifierat i förstudien), `cusip` (licensbelagd; Alpaca accepterar den som filter men returnerar den inte `[V]`). Regel: `instrument_id` sätts av oss; varje extern id lagras i `identifier_map` med källa och datum.
- **Tickerbyten:** `ticker_history(instrument_id, ticker, valid_from, valid_to)`. Uppslag ticker→instrument sker alltid *med datum*; FB→META är samma instrument, två rader.
- **Återanvända tickers:** samma ticker kan tillhöra olika instrument i olika intervall (klassiskt problem). Kvalitetstest: inga överlappande intervall för samma ticker; prisserier lagras per `instrument_id`, inte per ticker. Alpaca-hämtning per `symbol` + `asof` (som SIVB-testet) måste därför styras av `ticker_history`.
- Dagens Parquet-lager är nycklat på ticker (`processed/daily/<source>/<TICKER>.parquet`). Migrering: ny katalog `processed/daily/<source>/by_instrument/<instrument_id>.parquet` med `ticker_history` som brygga; de 19 pilottickrarna får `instrument_id` via mapping – ingen omräkning av features behövs eftersom features är per serie.

## 3. Avnotering, uppköp, konkurs – vad som händer i kedjan

| Händelse | Prisdata | Universum | Motor (öppen position) |
|---|---|---|---|
| Konkurs/avregistrering | serien slutar; sista pris ofta efter brant fall | `listed_to` = sista handelsdag; ej medlem från dagen efter | position värderas till 0 från dagen efter sista stapel (RISK_MODEL §2.7, konservativt) och stängs `delisted_zero`; `[D]` variant: sista close |
| Kontantuppköp | serien slutar nära budpriset | som ovan | `[D]` stäng till sista close (`delisted_cash`) – 0 vore fel; kräver att vi kan skilja fallen (`delist_reason`) |
| Aktieuppköp/fusion | serien slutar; aktier byts | som ovan | `[D]` stäng till sista close, flagga `delisted_stock_merger` (vi modellerar inte innehav i förvärvaren) |
| Byte av lista (Nasdaq↔NYSE) | kontinuerlig | medlem hela tiden, `exchange` byter | inget |
| Till OTC | SIP-serie kan fortsätta | ej medlem (L1), position stängs `delisted_otc` till sista close på huvudlistan `[D]` |
| Tickerbyte | kontinuerlig per instrument | inget | inget (nycklat på `instrument_id`) |
| Split / omvänd split | as-traded-brott | inget | finns (3f `apply_split`, kontot `apply_split`) – kräver splitdata för **avnoterade** serier också (Yahoo saknar dem) |
| Spin-off | prisfall utan split | inget | `[D]` behandlas som utdelning i `adj_factor` (features), inte som split; öppen position: `[D]` ignorera spin-off-värdet (konservativt, underskattar P&L) |

`delist_reason` är därför ett obligatoriskt fält i utvärderingsuniversumet och "okänd" måste vara ett tillåtet värde som tvingar till den konservativa 0-värderingen.

## 4. Vad våra nuvarande källor kan och inte kan `[V]`

| | Kan | Kan inte |
|---|---|---|
| **Alpaca SIP (dagsdata, raw)** | as-traded OHLCV från 2016-01-04 (DATA_PILOT §6); avnoterade serier nås via `asof=` om vi *vet* tickern och datumet (SIVB verifierat); `GET /v1/corporate-actions` med typer för splittar, utdelningar, `cash_merger`, `stock_merger`, `spin_off`, `reorganization`, `worthless_removal`, `name_change` (dok. läst 2026-11-01) | ingen lista över historiskt noterade instrument (`/v2/assets` är nuläge); inga avnoterings- eller noteringsdatum; corporate actions-endpointens **tidigaste historik anges inte** i dokumentationen och måste mätas; inga symbolbyten som egen typ |
| **Alpaca minutdata** | per session för kända (ticker, dag) | samma som ovan; äldre dagar hela UTC-dygnet (hanterat) |
| **Yahoo** | splittar/utdelningar för *överlevande*; andra priskälla | avnoterade saknas eller omdöpta (SIVB 0 rader); ingen notering/avnoteringshistorik; retroaktiva ändringar |
| **Parquet-lagret** | append-only rådata, upsert per datum, `price_basis`, kvalitetsrapport per serie, `attach_splits` | nycklat på ticker; ingen plats för `listed_from/to`, `delist_reason`, tickerhistorik, identifierare; kandidattabeller per ticker utan universumtillhörighet per dag |

Slutsats: Alpaca kan vara **prisleverantör** även i utvärderingsuniversumet (plus corporate actions, om historiken räcker), men **medlemskapslistan måste komma från en annan källa**.

## 5. Datakällor – vad som faktiskt står hos leverantören

Priser lästa 2026-11-01 på leverantörens sidor om inget annat anges. Allt som rör *kvalitet* är `[U]` tills §8 körts.

| Källa | Avnoterade | Historik | Identifierare | Corporate actions | Kostnad | Kända begränsningar |
|---|---|---|---|---|---|---|
| **Norgate Data, US Platinum** `[V]` | ja, "delisted securities (select types)" tillbaka till 1990; historiska indexkonstituenter | till 1990 (Diamond: 1950) | egen; Windows-program + Python-paket, ingen webb-API | finns i produkten `[U]` (sidan nämner inte split/tickerbyten uttryckligen) | **630 USD/år** (346,50 USD/6 mån); Silver/Gold (270/360 USD/år) saknar avnoterade | kräver Windows-klient (vi kör CachyOS → VM eller Wine `[U]`); licens för personligt bruk; "select types" måste tolkas; exakt *noteringsdatum* kontra första prisdag ej verifierat |
| **Sharadar (via Nasdaq Data Link)** `[V]` delvis | "includes active and delisted tickers", >20 000 bolag | dagspriser sedan 1998 | `permaticker` `[U]` | `ACTIONS`-tabell `[U]` | **pris ej hittat** på de lästa sidorna; licens personlig/institutionell – måste hämtas från data.nasdaq.com | Nasdaq-ägd; API-nedladdning av hela tabeller; point-in-time-fält (`firstpricedate`, `lastpricedate`, `isdelisted`) `[U]` |
| **EODHD** `[V]` | `exchange-symbol-list/US?delisted=1`; "delisted tickers keep their full history up to the delisting date" | avnoterade före 2018: **endast EOD**; efter 2018: + fundamenta/splittar/utdelningar; efter 2021: + intradag | `Isin` (kan vara null), ingen FIGI | `symbol-change-history` (US) med datum; splittar bara för avnoterade efter 2018 | plan/pris ej angivet i artikeln (prissida ej läst) | **inga avnoteringsdatum i listan** (bara `General.IsDelisted` + `UpdatedAt` i fundamenta); täckning per ticker "kontakta support" |
| **Massive (f.d. Polygon)** `[V]` | `GET /v3/reference/tickers?date=…&active=false` ger tickers som fanns/avnoterats per datum; `delisted_utc` (kan saknas) | tickerlistor från **2003-09-10**; "effective-dated, not a copy saved on that day" – inte en äkta ögonblicksbild | `share_class_figi`, `composite_figi`, `cik` | `tickers/{id}/events` för namn-/symbolbyten | Stocks Basic 0 USD (2 års referenshistorik); referenshistorik "all history" från Starter; prisdata: Starter **29 USD/mån, 5 års historik** enligt tredjepartssida verifierad 2026-07 `[U]`; Developer 79 USD/10 år; Advanced 199 USD/20+ år | ingen nedladdningsbar security master; äldre poster saknar `type`/`primary_exchange` → filter tappar riktiga aktier; "effective-dated" betyder att dagens kunskap om gårdagen används – look-ahead-risk för *attribut*, inte för medlemskap |
| **Alpaca** `[V]` | se §4 | priser 2016– | symbol (+ `asof`), CUSIP som filter | ja (typer ovan), historikstart ej dokumenterad | ingår i paper-konto | ingen medlemskapslista |
| **Yahoo/Stooq** | nej/okänt | – | ticker | splittar (Yahoo) | gratis | inte för universum |
| **Tiingo** `[U]` | oklart; "supported tickers" med start/slutdatum nämns i community-kod, ej verifierat | "30+ års" prisdata enligt egen marknadsföring | ticker | okänt | ej läst | inte verifierat i förstudien |
| **CRSP (via WRDS)** `[U]` | akademisk guldstandard med `permno`, avnoteringsavkastning (`dlret`) | 1925– | permno/permco/cusip | fullständiga | institutionell licens; ej realistiskt för en privatperson | nämns bara som referens för vad "komplett" betyder |
| **Fria byggstenar** `[U]` | SEC EDGAR: `submissions`-API per CIK ger tidigare namn och nuvarande tickers; Form 25 (avnotering) och 8-K punkt 3.01 (notis om avnotering) är primärkällor för *datum och orsak*; Nasdaq Trader `nasdaqlisted.txt`/`otherlisted.txt` är dagliga nulägeslistor | EDGAR till 1990-talet; Nasdaq Trader bara nuläge | CIK | – | gratis | tung parsning; CIK är bolag, inte aktieklass; ingen historisk lista bakåt (men **vi kan börja arkivera Nasdaq Trader dagligen från nu** → äkta ögonblicksbilder framåt) |

Kostnadsfrågor att avgöra (`[D]`): (a) 630 USD/år Norgate mot Sharadar-pris (okänt) mot Massive Starter 348 USD/år + fortsatt Alpaca för priser; (b) om Windows-klient är acceptabel; (c) licensvillkor för att spara härledda tabeller i vårt Parquet-lager (DATA_PILOT §7 ska utökas per leverantör innan något lagras).

## 6. Survivorship och framtidsinformation – reglerna

1. **Medlemskap vid T−1.** `members(T−1)` = instrument med `listed_from ≤ T−1 < listed_to` och L2 uppfyllt T−1. Ingen uppgift från T eller senare används för att avgöra medlemskap.
2. **Noteringsdatum ≠ första prisdag.** Första stapeln i prisleverantörens serie får användas som `listed_from` *bara* om registret saknar datum, och då flaggas `listed_from_source = "first_bar"`. Spec §2 kräver dessutom ≥ 50 staplar för SMA50, så nya noteringar är automatiskt uteslutna tills historiken finns – det är en egenskap hos features, inte hos universumet.
3. **Effective-dated register** (Massive) ger attribut som de ser ut *i dag* för ett historiskt datum: `type`, `primary_exchange`, namn. Medlemskap (fanns/fanns inte) är tillförlitligare än attribut. Regel: attribut som används för filtrering (stamaktie, börs) tas från registret men `[D]` instrumenttypen får bara utesluta när den är *känd*; okänd typ → behåll och räkna.
4. **Backfill-bias:** leverantörer lägger till historik för instrument i efterhand (t.ex. en avnoterad serie som dyker upp vid nästa hämtning). Varje universumsnapshot får `snapshot_id` och `fetched_at`; körningar anger vilket snapshot de använt; en omkörning med nyare snapshot är en ny körning.
5. **Ingen rensning på utfall:** instrument tas aldrig bort för att serien "ser konstig ut" (brant fall, låg volym) – det är precis de som bär survivorship-effekten. Kvalitets-FAIL (strukturfel) utesluter, WARN gör det inte (som i dag).
6. **Avnoterade i resultatet:** rapporten redovisar antal affärer i instrument som senare avnoterades och deras R-bidrag separat; ett universum där den siffran är 0 över 10 år är en varningsflagga, inte ett bra resultat.
7. **Tidsgräns för L3:** `dollar_volume_20d` och `close_prev` är redan T−1-features (FEATURE_VERSION 1.1); universumet lägger inga egna volymmått.

## 7. Minimal versionshanterad datamodell `[D]`

Parquet under `data/universe/<provider>/<snapshot_id>/`, append-only som övriga lager, plus `manifest.json` (provider, hämtningstid, radantal per tabell, licensnot, `universe_version`).

```
instruments        instrument_id, name_latest, share_class_figi?, cik?, security_type,
                   country, source, fetched_at
listings           instrument_id, exchange, listed_from, listed_to (null = aktiv),
                   listed_from_source (register|first_bar), delist_reason
                   (bankruptcy|cash_merger|stock_merger|otc|voluntary|unknown), source
ticker_history     instrument_id, ticker, valid_from, valid_to, source
corporate_actions  instrument_id, type (split|reverse_split|cash_dividend|spin_off|
                   cash_merger|stock_merger|name_change|symbol_change|worthless),
                   ex_date, announce_date?, record_date?, ratio?, cash?, source, fetched_at
identifier_map     instrument_id, id_type (figi|cik|cusip|permaticker|eodhd_code), id_value,
                   valid_from?, valid_to?, source
universe_days      (materialiserad, valfri) date, instrument_id, l1_listed, l2_tradable,
                   l3_price_volume, reason_excluded  – en rad per instrument och session
```

`universe_version` (sträng) följer med i `config.json`, `summary.json`, `Trade.versions` och `Rejection` precis som `spec_version` i dag. Reglerna för L2 (vad "handlingsbar" betyder: stapel finns och `volume > 0`; `[D]` minsta antal staplar före T) och L3-parametrarna (spec §1, ej låsta) dokumenteras i manifestet.

## 8. Kvalitetstester (måste vara gröna innan en källa får kallas utvärderingsuniversum)

**Strukturella (deterministiska, offline på snapshotet):**
1. Inga överlappande `ticker_history`-intervall per ticker; inga luckor i `listings` för samma instrument utan `delist_reason`.
2. Varje `listed_to` har en prisstapel ≤ `listed_to` och ingen stapel > `listed_to` + 5 sessioner på huvudlistan (annars är avnoteringsdatumet fel eller serien fortsätter OTC).
3. Varje `listed_from` ≤ första prisstapel + 5 sessioner (annars saknas prisdata i början → `listed_from_source` måste vara `first_bar` och instrumentet uteslutas tills 50 staplar finns).
4. Splittar i `corporate_actions` matchar prisbrott i as-traded-serien (återanvänd `quality.py`:s split-like-detektor) – inklusive för avnoterade serier.
5. `instrument_id` unik per `share_class_figi`/`permaticker`; ingen ticker pekar på två instrument samma dag.

**Täckning (stickprov mot primärkällor, dokumenteras i DATA_PILOT):**
6. 30 kända avnoteringar 2016–2026 (konkurser: SIVB, SBNY, FRC, BBBY, PRTY; kontantuppköp: TWTR, ATVI, SGEN, VMW; aktieuppköp: XLNX, MXIM; till OTC: minst 3) – finns de med rätt sista dag (±1 session) och rätt orsak? Resultat som andel.
7. 20 kända tickerbyten (FB→META, GOOG-klasser, SQ→XYZ, TWTR, BRK-B-formatet) – samma `instrument_id` före/efter.
8. 10 återanvända tickers (kräver lista; SEC/EDGAR-sök) – två instrument, inga överlapp.
9. Antal noterade stamaktier per år jämfört med offentlig statistik (World Bank/WFE-antal noterade bolag i USA `[U]`): avvikelse > 15 % → förklara.
10. Survivorship-mått: andel instrument i universumet 2016-01-04 som saknas 2026-10-31. Om < 15 % är listan sannolikt överlevarbaserad (branschvis omsättning är högre `[U]`).

**Look-ahead (syntetiska, som §8 i STEP3_DESIGN):**
11. Flytta en avnotering en dag framåt i snapshotet → `members(T−1)` ändras bara från det datumet; inga tidigare dagar.
12. Mutera attribut (typ/börs) för framtida datum → medlemskap före datumet oförändrat.

## 9. Anslutning till scanner och portfölj (inga regeländringar)

- `backtesting/universe.py`: `class Universe(Protocol): members(asof: date) -> set[instrument_id]`, `ticker_on(instrument_id, asof) -> str`, `delist_info(instrument_id) -> (listed_to, reason)`, `version`. `PilotUniverse(tickers)` (konstant, `version="pilot-19"`), `PointInTimeUniverse(snapshot_dir)`.
- **Scanner:** körs per instrument-serie som i dag (`compute_features` → `apply_rules`); ingen regel ändras. Nytt: kandidattabellen får `instrument_id` och `universe_version`; `is_candidate` förblir L4. L3 (pris/volym) beräknas redan av features; universumet läser `dollar_volume_20d`/`close_prev` i `members()` eller lämnar det till `apply_rules` (`[D]` – i dag är likviditetsregeln en av de 9 aktiva reglerna, så enklast är att L3 stannar i scannern och universumet bara hanterar L1–L2).
- **Portföljmotor:** `run_portfolio(series, cands, params, universe=...)`: i `pre_open` filtreras kandidatrader på `instrument_id ∈ universe.members(T−1)`; rader utanför loggas `not_in_universe` (nytt avslag, steg `universe`). Öppna positioner vars instrument lämnar universumet (`listed_to` passerad) stängs i `open`-fasen enligt §3 med nya exitorsaker (`delisted_zero`/`delisted_cash`/`delisted_stock_merger`/`delisted_otc`) – detta är P8:s enda ändring i exits och gäller bara händelser som i dag inte kan inträffa (pilotdatan har inga avnoteringar).
- **Rapport:** `candidates`-sektionen får L1–L4-räkning per dag (medel), `universe_version`, täckningsmåtten från §8 punkt 6–10 och "affärer i senare avnoterade instrument".
- **Ingen ny scannerlogik, inga nya trösklar:** `min_price`/`min_dollar_volume` är spec §1:s befintliga parametrar; om de är olåsta i `Thresholds` förblir de det.

## 10. Stegvis plan

| Steg | Innehåll | Kostnad | Resultat |
|---|---|---|---|
| U0 | Börja arkivera Nasdaq Trader-listorna dagligen (cron, append-only) och Alpaca `/v2/assets` | 0 | äkta ögonblicksbilder framåt från i dag |
| U1 | `universe.py` med `PilotUniverse`, datamodellen i §7 som tomma tabeller + strukturella tester 1–5 på syntetiska data; `not_in_universe` och `delisted_*` i motorn (syntetiskt testade) | 0 | kod klar för vilken källa som helst |
| U2 | **Forskningsuniversum:** Massive Basic (0 USD) – `All Tickers?date=` för 2016-01-04 och kvartalsvis framåt (2 års referenshistorik på Basic räcker inte; verifiera om `date` bakåt kräver Starter) → lista över ~5 000 stamaktier per kvartal; priser från Alpaca per `ticker_history` + `asof`; Alpaca corporate actions för splittar. Mät täckning (§8 6–10) | 0–29 USD/mån | pipelinen körs på hela marknaden; `too_small`-andelen vid 10 000 SEK blir känd på riktigt |
| U3 | Beslut om utvärderingskälla efter U2:s mätningar: Norgate Platinum (630 USD/år, Windows) eller Sharadar (pris att hämta) | 630 USD/år eller Sharadar-pris | – |
| U4 | **Utvärderingsuniversum:** ladda källan till §7-tabellerna, kör §8 helt, dokumentera i DATA_PILOT, uppdatera DATA_PILOT §7 med licensen | ingår | första körning som får kallas point-in-time – med täckningssiffrorna i rubriken |
| U5 | Jämför U2 och U4 på samma period: antal affärer, EV, andel avnoterade – skillnaden *är* survivorship-effekten i vår pipeline | – | kvantifierad bias |

Beslut att fatta före U1 (`[D]`): avnoteringsvärdering per orsak (§3), spin-off-hantering, L2-definition, om L3 stannar i scannern, `instrument_id`-källa (egen surrogat + FIGI). Dessa förs in som D-019– när de tas. Q-002 i DECISIONS uppdateras med hänvisning hit.

## Versionshistorik
- 0.1 (2026-11-01): förstudie efter P1–P6/ORB och D-018. Leverantörsuppgifter lästa från Norgate (paketsida), EODHD (artikel om avnoterade), Massive (All Tickers-dokumentation och kunskapsbas), Alpaca (corporate actions-referens), QuantRocket (Sharadar-sidor); Massive-priser via tredjepartssida. Inget verifierat mot faktisk data ännu.
