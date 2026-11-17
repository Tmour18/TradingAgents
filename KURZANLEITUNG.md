# Kurzanleitung: Swing-Papierhandel mit 100 € (Trade Republic)

Stand: 09.11.2026. Gilt für **Variante A**: Das Geld liegt im MSCI-World-Sparplan,
das Swing-System läuft parallel **nur auf Papier**.

## 1. Das Wichtigste in 5 Sätzen

1. Jeden Börsentag um **14:45 Uhr** bekommst du eine Push-Nachricht: "Papier-Kauf X" oder "heute nichts".
2. In den ersten **4 Wochen (bis ca. 07.12.2026)** kaufst du **nichts echt**, du schaust nur zu.
3. Jeden **Sonntagabend** kommt eine Auswertung: Papier-System gegen MSCI-World-ETF.
4. Echtes Geld erst, wenn das Papier-Journal den ETF schlägt. Der 2-Jahres-Backtest hat ihn **nicht** geschlagen (siehe Abschnitt 6).
5. Alles läuft in **einer** Claude-Sitzung ("Trading"-Sitzung, in der wir das eingerichtet haben). Dort erscheinen die Berichte, und dort fragst du: "Wie sieht's aus?"

## 2. Was automatisch passiert

| Wann | Was | Ergebnis |
|---|---|---|
| Mo–Fr 14:45 (vor US-Börsenstart 15:30) | Scan des US-Markts (trader-scanner), Prüfung der besten Treffer: Stop/Ziel, Gebühren, Historie, News (trader-news), Quartalszahlen (trader-analyst), Risiko-Check (trader-risk). Claude bewertet und wählt **höchstens einen** Kandidaten. Offene Papier-Positionen werden geprüft. | Push-Nachricht, Bericht in `trader/berichte/DATUM.md`, Journal `trader/journal.json` |
| So 18:17 | Auswertung des Journals: Rendite nach Gebühren, max. Drawdown, Trefferquote, Anzahl Trades, Vergleich mit MSCI World | Push-Nachricht |

Alles wird auf GitHub gesichert: Repo `Tmour18/TradingAgents`, Branch `claude/exciting-keller-1pka0s`.

## 3. Die Regeln (Datei `trader/swing.json`)

Diese Regel ist von mir ergänzt worden; sie stammt nicht aus day-trader-skill. Dort gibt es nur Werkzeuge für Daytrading.

- **Kandidaten:** US-Aktien über 5 $ mit mehr als 1 Mio. gehandelten Aktien pro Tag, mit einem Swing-Setup (20-Tage-Hoch-Ausbruch, Bull-Flag, Rückeroberung des 20-Tage-Durchschnitts, Erholung nach "überverkauft").
- **Stop-Loss:** Einstieg − 3 × ATR(14). ATR ist die durchschnittliche Tagesschwankung der letzten 14 Tage.
- **Kursziel:** Einstieg + 2 × (Einstieg − Stop), also das Doppelte des Risikos ("2R").
- **Spätestens raus:** nach 60 Handelstagen (ca. 3 Monate), egal wie es steht.
- **Betrag:** so, dass Verlust am Stop + 2 € Gebühren höchstens **10 €** ausmacht; höchstens 100 €.
- **Gebührenprüfung (BLOCK):** Der Gewinn am Ziel nach Gebühren muss mindestens doppelt so hoch sein wie die Gebühren (4 €) und mindestens so hoch wie der Verlust am Stop.
- **Höchstens 1** offene Position gleichzeitig.
- **Claude lehnt zusätzlich ab:** schwere negative Nachrichten (Ermittlungen, Betrug, Insolvenz, Kapitalerhöhung), Quartalszahlen innerhalb der nächsten ca. 10 Handelstage, einen Stop weiter als 25 % entfernt.
- **Papier-Kauf** zum Eröffnungskurs des nächsten Handelstags. Berührt eine Tageskerze Stop und Ziel, zählt der Stop.

## 4. Was du tun musst

**Jetzt einmalig: den MSCI-World-Sparplan anlegen** (in der Trade-Republic-App)
1. Suche: **iShares Core MSCI World** (ISIN **IE00B4L5Y983**).
2. "Sparplan" wählen, Betrag und Rhythmus festlegen (z. B. 100 € einmal oder monatlich). Die Ausführung im Sparplan ist gebührenfrei. Prüf die genauen Bedingungen in der App.

**Während der Papierphase:** Nachrichten lesen, nachfragen, nichts kaufen.

**Später, falls echtes Geld (erst nach meiner Empfehlung):**
1. Aktie in der App über den **Firmennamen** suchen. Eine ISIN liefern die freien Datenquellen nicht. Achte darauf, dass Name und Ticker übereinstimmen.
2. **Kurs in der App prüfen.** Die Daten im Bericht sind verzögerte Yahoo-Kurse in $; die App zeigt € inklusive Wechselkurs. Liegt der Kurs in der App mehr als ca. 2 % über dem Referenz-Einstieg (in €), nicht kaufen und nachfragen.
3. Kaufen → Betrag in € eingeben (Bruchstücke gehen) → Order bestätigen (1 € Gebühr).
4. **Sofort eine Stop-Loss-Verkaufsorder** zum Stop-Kurs in € anlegen, falls die App das für die Position anbietet. Sonst mindestens einen **Kursalarm** am Stop setzen.
5. **Kursalarm** am Ziel setzen. Wird er ausgelöst, verkaufen (1 € Gebühr).
6. Mir kurz Kaufkurs in €, Betrag und Datum schreiben, dann stimmt das Journal.

Wichtig: Ein Stop-Loss ist keine Garantie. Bei einer Kurslücke (z. B. nach Quartalszahlen über Nacht) kann der Verkauf deutlich unter dem Stop erfolgen.

## 5. Kosten

- Repo B (Daten) und Claude in deinem Abo: keine Extrakosten.
- Trade Republic: 1 € pro Kauf und 1 € pro Verkauf; Sparplan-Ausführungen sind gebührenfrei.
- Repo A (TradingAgents mit eigenem API-Konto) wird zurzeit **nicht** genutzt.

## 6. Ehrliche Einordnung (Backtest, 2 Jahre, 63 US-Aktien)

| | 100 € wurden zu | max. Rückgang |
|---|---|---|
| ursprüngliche Regel (5 € Max-Verlust, bis 4 Wochen) | 16 € | −85 % |
| **Variante A (diese Regel)** | 111 € | −35 % |
| MSCI World gekauft und gehalten | 117–118 € | −22 % |

Variante A war die beste von 7 getesteten Varianten, das Ergebnis ist also eher geschönt. Die Aktienliste enthält nur Firmen, die es heute noch gibt. Spreads sind nicht eingerechnet.
**Fazit: Das System hat den ETF im Test nicht geschlagen.** Deshalb liegt das Geld im Sparplan.

## 7. Befehle (für Claude oder dich am PC)

```bash
python3 kombi/swing.py plan                    # Tageslauf (ganzer US-Markt, ca. 5 Minuten)
python3 kombi/swing.py plan --symbols KO,AAPL  # nur bestimmte Aktien
python3 kombi/swing.py journal add TICKER --notiz "Begründung"   # Vorschlag ins Papier-Journal
python3 kombi/swing.py journal update          # offene Papier-Positionen prüfen
python3 kombi/swing.py journal auswertung      # Rendite, Drawdown, Trefferquote vs. ETF
python3 kombi/swing.py backtest                # Regel auf 2 Jahren Historie testen
python3 kombi/swing.py backtest --set target_r=3   # eine Variante testen
```

Auf einem neuen Rechner: `./einrichten.sh` ausführen.

## 8. Wenn etwas nicht stimmt

- **Benachrichtigungen ändern oder stoppen:** In der Trading-Sitzung Claude sagen, z. B. "Stopp die tägliche Nachricht" oder "Verschieb sie auf 15:30". Die geplanten Aufgaben erscheinen nicht in der Aufgabenliste der App, weil Claude sie über eine Schnittstelle angelegt hat.
- **Daten fehlen oder sind veraltet:** Steht so im Bericht. Dann gibt es keine Empfehlung.
- **Regeln ändern:** Erst als Backtest prüfen lassen, dann `trader/swing.json` anpassen.

Bildung und Werkzeuge, keine Anlageberatung. Nichts in diesem Projekt platziert Orders; alle Orders gibst du selbst in der App auf.
