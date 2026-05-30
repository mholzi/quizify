# Review: wissenschaft-de.json (de)

Review run 2026-05-30, scope: wissenschaft_de_050–102 (53 new). Originale IDs (001–049) wurden Mai-27/28 bereits gereviewt und sind hier nicht enthalten.

## Summary

- ✅ **Clean**: 41
- ⚠️ **Concerns**: 0 (post-fix; ursprünglich 5)
- 🚨 **Fix needed**: 0 (post-fix; ursprünglich 7)
- 🗑️ **Deleted**: 5 (Ambiguität nicht sauber lösbar)
- 🔧 **Fixed**: 7

Pack-count: 100 → 95 nach Deletions. 53 reviewte → 41 clean + 7 gefixt + 5 gelöscht = 48 verbleibend in Scope.

### Top issues addressed first

1. **wissenschaft_de_099** 🚨 — Frage fragte nach „Eis schneller schmelzen", Antwort/Funfact beschrieb aber Mpemba-Effekt (Gefrieren von heißem Wasser). Frage-Antwort-Mismatch — gefixt.
2. **wissenschaft_de_078** 🚨 — Frage „Wovor schützt der Mantel beim Tintenfisch beim Verfolgen seiner Beute?" passt semantisch nicht zu Antwort „Er nutzt ihn als Rückstoßantrieb" (Frage fragt nach Schutz, Antwort beschreibt Antrieb). Gefixt.
3. **wissenschaft_de_055** 🗑️ — Pi-im-Flussmäander (Stølum 1996) ist umstrittene Einzel-Studie, nicht robust reproduziert. Gelöscht.
4. **wissenschaft_de_080** 🗑️ — Quantenkohärenz in Photosynthese (Engel 2007) wurde durch Duan 2017 / Cao 2020 stark relativiert. Gelöscht.
5. **wissenschaft_de_089** 🗑️ — „Graphen härter als Diamant" verwechselt Zugfestigkeit mit Härte. Gelöscht.

## Per-question findings

| ID  | Verdict | Factual | Distractors | Fun-fact | Clarity | Difficulty | Summary |
|---|---|---|---|---|---|---|---|
| wissenschaft_de_050 | ✅ ok | pass | pass | pass | pass | appropriate | Zeta-Regularisierung -1/12 — korrekt, präzise. |
| wissenschaft_de_051 | ✅ ok | pass | pass | pass | pass | appropriate | Geburtstagsparadox 50% bei 23 — korrekt. |
| wissenschaft_de_052 | ✅ ok | pass | pass | pass | pass | appropriate | 4-Farben-Satz korrekt, Appel/Haken 1976. |
| wissenschaft_de_053 | ✅ ok | pass | pass | pass | pass | appropriate | Möbiusband 1 Seite — clean. |
| wissenschaft_de_054 | 🔧 fixed | **fail** | pass | pass | **concern** | appropriate | Frage umformuliert — alte Version "Was ist eine Primzahl, die in einer Folge..." war grammatikalisch und konzeptionell unklar. |
| wissenschaft_de_055 | 🗑️ deleted | **concern** | pass | **concern** | pass | appropriate | Pi-Mäander-Behauptung (Stølum 1996) nicht robust reproduziert; pop-science. |
| wissenschaft_de_056 | ✅ ok | pass | pass | pass | pass | appropriate | Banach-Tarski — clean. |
| wissenschaft_de_057 | ✅ ok | pass | pass | pass | pass | appropriate | 52! Korrekt, 8×10⁶⁷. |
| wissenschaft_de_058 | ✅ ok | pass | pass | pass | pass | appropriate | Monty Hall 2/3 — clean. |
| wissenschaft_de_059 | ✅ ok | pass | pass | pass | pass | appropriate | Eulersche Identität — clean. |
| wissenschaft_de_060 | ✅ ok | pass | pass | pass | pass | appropriate | Milchstraße 100-400 Mrd Sterne — clean. |
| wissenschaft_de_061 | ✅ ok | pass | pass | pass | pass | appropriate | Neutronenstern-Dichte — korrekt. |
| wissenschaft_de_062 | ✅ ok | pass | pass | pass | pass | appropriate | Rote Riesen — clean. |
| wissenschaft_de_063 | ✅ ok | pass | pass | pass | pass | appropriate | Lichtlaufzeit 8 min — clean. |
| wissenschaft_de_064 | ✅ ok | pass | pass | pass | pass | appropriate | Olympus Mons — korrekt. |
| wissenschaft_de_065 | ✅ ok | pass | pass | pass | pass | appropriate | Beschleunigte Expansion, Nobelpreis 2011 — clean. |
| wissenschaft_de_066 | ✅ ok | pass | pass | pass | pass | appropriate | Jupiter ~95 Monde — aktuell. |
| wissenschaft_de_067 | ✅ ok | pass | pass | pass | pass | appropriate | Schall im Vakuum — clean. |
| wissenschaft_de_068 | ✅ ok | pass | pass | pass | pass | appropriate | 46 Chromosomen — clean. |
| wissenschaft_de_069 | ✅ ok | pass | pass | pass | pass | appropriate | DNA 2% kodierend — clean. |
| wissenschaft_de_070 | 🔧 fixed | pass | pass | **concern** | pass | appropriate | Funfact-Zahlen (60% Banane, 85% Maus) waren pop-science / ungenau. Funfact ersetzt durch belastbare Aussage. |
| wissenschaft_de_071 | 🔧 fixed | **concern** | pass | **concern** | pass | appropriate | Genom-Größe — Australischer Lungenfisch hat 43 Gb, aber Marmor-Lungenfisch (P. aethiopicus) hat ~130 Gb (deutlich größer). Funfact + Frage präzisiert. |
| wissenschaft_de_072 | ✅ ok | pass | pass | pass | pass | appropriate | Endosymbionten — korrekt. |
| wissenschaft_de_073 | ✅ ok | pass | pass | pass | pass | appropriate | Turritopsis dohrnii — clean. |
| wissenschaft_de_074 | ✅ ok | pass | pass | pass | pass | appropriate | Bärtierchen ESA 2007 — korrekt. |
| wissenschaft_de_075 | ✅ ok | pass | pass | pass | pass | appropriate | 2-3 Mio Zellen/sec — clean. |
| wissenschaft_de_076 | 🗑️ deleted | **concern** | pass | pass | **concern** | appropriate | Hornhaut trägt ~2/3 der Brechkraft, Linse nur ~1/3 — "Linse" als Alleinantwort faktisch wackelig. |
| wissenschaft_de_077 | 🔧 fixed | pass | pass | pass | **concern** | appropriate | Originalfrage suggerierte Durchschnitt aller Pflanzen, Antwort gilt nur für Aronstabgewächse. Frage präzisiert. |
| wissenschaft_de_078 | 🔧 fixed | pass | pass | pass | **fail** | appropriate | Frage-Antwort-Mismatch — Frage "Wovor schützt" passt nicht zu "Rückstoßantrieb". Frage umformuliert. |
| wissenschaft_de_079 | ✅ ok | pass | pass | pass | pass | appropriate | Schwänzeltanz, Karl von Frisch — clean. |
| wissenschaft_de_080 | 🗑️ deleted | **concern** | pass | **concern** | pass | appropriate | Quantenkohärenz-Photosynthese wissenschaftlich umstritten. |
| wissenschaft_de_081 | ✅ ok | pass | pass | pass | pass | appropriate | Clownfische Geschlechtswechsel — clean. |
| wissenschaft_de_082 | 🗑️ deleted | **concern** | pass | pass | **concern** | appropriate | "Wichtigste Bestäubergruppe nach Honigbienen" nicht eindeutig. |
| wissenschaft_de_083 | ✅ ok | pass | pass | pass | pass | appropriate | Heisenberg — korrekt formuliert. |
| wissenschaft_de_084 | ✅ ok | pass | pass | pass | pass | appropriate | 2. Hauptsatz Thermodynamik — clean. |
| wissenschaft_de_085 | ✅ ok | pass | pass | pass | pass | appropriate | Verschränkung, Nobelpreis 2022 — clean. |
| wissenschaft_de_086 | ✅ ok | pass | pass | pass | pass | appropriate | Absoluter Nullpunkt — clean. |
| wissenschaft_de_087 | 🔧 fixed | **concern** | pass | pass | **concern** | appropriate | "Schnellste Bewegung im Körper" mehrdeutig (Schall im Gewebe ~1500 m/s). Frage präzisiert auf Nervenleitgeschwindigkeit. |
| wissenschaft_de_088 | ✅ ok | pass | pass | pass | pass | appropriate | Eis VII bei hohem Druck — korrekt. |
| wissenschaft_de_089 | 🗑️ deleted | **fail** | pass | **concern** | pass | appropriate | Graphen-härter-als-Diamant verwechselt Zugfestigkeit mit Härte. |
| wissenschaft_de_090 | ✅ ok | pass | pass | pass | pass | appropriate | Foucault-Pendel — korrekt. |
| wissenschaft_de_091 | ✅ ok | pass | pass | pass | pass | appropriate | Mark II Motte, Grace Hopper — clean. |
| wissenschaft_de_092 | ✅ ok | pass | pass | pass | pass | appropriate | Ada Lovelace 1843 — clean. |
| wissenschaft_de_093 | ✅ ok | pass | pass | pass | pass | appropriate | Enigma — korrekt, Schlüsselzahl passt für 3-Rotor-Heeres-Enigma. |
| wissenschaft_de_094 | ✅ ok | pass | pass | pass | pass | appropriate | Mooresches Gesetz — clean. |
| wissenschaft_de_095 | ✅ ok | pass | pass | pass | pass | appropriate | Halting-Problem, Turing 1936 — clean. |
| wissenschaft_de_096 | ✅ ok | pass | pass | pass | pass | appropriate | UNIVAC I 13 Tonnen, Eisenhower 1952 — clean. |
| wissenschaft_de_097 | ✅ ok | pass | pass | pass | pass | appropriate | Wal-Flusspferd-Verwandtschaft — clean. |
| wissenschaft_de_098 | ✅ ok | pass | pass | pass | pass | appropriate | Elektrisch + chemisch — korrekt. |
| wissenschaft_de_099 | 🔧 fixed | **fail** | pass | pass | **fail** | appropriate | Frage sprach von "Eis schmelzen", Mpemba-Effekt ist aber Gefrieren. Frage korrigiert auf Gefrieren. |
| wissenschaft_de_100 | ✅ ok | pass | pass | pass | pass | appropriate | Plasma 4. Aggregatzustand — clean. |
| wissenschaft_de_101 | ✅ ok | pass | pass | pass | pass | appropriate | Pilze näher an Tieren (Opisthokonta) — korrekt. |
| wissenschaft_de_102 | ✅ ok | pass | pass | pass | pass | appropriate | Voyager 1 2012 — korrekt. |

## Detailed notes

### wissenschaft_de_054 🔧 (gefixt)

- **Original-Frage**: "Was ist eine Primzahl, die in einer Folge von immer mehr Stellen auftaucht?"
- **Befund**: Frage grammatikalisch und konzeptionell unklar — was bedeutet "in einer Folge von immer mehr Stellen auftauchen"? Die Antwort "Es gibt unendlich viele Primzahlen" passte semantisch nicht zur Frage. Außerdem Funfact ungenau: Euklids Beweis ergibt einen Primteiler, der NICHT in der Liste war (nicht "unbekannt").
- **Fix**: Frage umformuliert zu "Wie viele Primzahlen gibt es insgesamt?". Funfact geschärft.

### wissenschaft_de_055 🗑️ (gelöscht)

- **Frage**: "Welche mathematische Konstante steckt überraschend im Pfad eines mäandrierenden Flusses?"
- **Marked correct**: Pi
- **Befund**: Die Stølum-1996-Behauptung (Sinuosität ≈ π) basiert auf Einzel-Studie und wurde in späterer Forschung nicht robust reproduziert. Reale Flusssinuositäten variieren stark mit Untergrund, Gefälle und Vegetation. Eher populärwissenschaftliche Anekdote als belastbares Quiz-Fakt.
- **Entscheidung**: Gelöscht (Markus' Regel: ambiguity that can't be cleanly resolved → delete).

### wissenschaft_de_070 🔧 (gefixt)

- **Frage**: "Wie viel DNA teilen Mensch und Schimpanse ungefähr?"
- **Marked correct**: Etwa 98-99 % (korrekt)
- **Befund**: Frage und Hauptantwort sind solide. Aber Funfact zitierte zwei umstrittene Zahlen: "60 % mit Banane" (in der Literatur eher 40-50 %, oft fragwürdig zitiert) und "85 % mit Maus" (je nach Methodik ~85 % oder ~92 %).
- **Fix**: Funfact umgeschrieben — entfernt die fragwürdigen Vergleichszahlen, behält die Substanz (Vergleichsmethoden, Bedeutung der Unterschiede).

### wissenschaft_de_071 🔧 (gefixt)

- **Original-Frage**: "Welches Tier hat das größte Genom (mehr DNA als der Mensch)?"
- **Befund**: Antwort "Lungenfisch" ist korrekt, aber Funfact nannte spezifisch den "Australischen Lungenfisch" mit 43 Mrd. Basenpaaren. Tatsächlich hat der Marmor-Lungenfisch (Protopterus aethiopicus) ein noch größeres Genom (~130 Mrd. Basenpaare). "Größtes Genom unter Tieren" ist außerdem noch unklarer wenn man Amöben einschließt (aber das sind keine Tiere).
- **Fix**: Frage spezifiziert auf "Wirbeltier", Funfact mit dem genaueren Marmor-Lungenfisch (>100 Mrd. Basenpaare).

### wissenschaft_de_076 🗑️ (gelöscht)

- **Frage**: "Welches Bauteil im menschlichen Auge bildet das Bild seitenverkehrt ab?"
- **Marked correct**: Die Augenlinse
- **Befund**: Faktisch wackelig — die Hornhaut trägt ~2/3 der Brechkraft des Auges, die Linse nur ~1/3. Die Bildumkehr entsteht durch das GESAMTE optische System (Hornhaut + Linse). "Die Linse" als Alleinantwort ist eine starke Vereinfachung; die Hornhaut (Distraktor!) wäre als alleinige Antwort sogar plausibler.
- **Entscheidung**: Gelöscht — Ambiguität nicht sauber lösbar ohne die Frage-Premise zu zerschießen.

### wissenschaft_de_077 🔧 (gefixt)

- **Original-Frage**: "Wie hoch ist die durchschnittliche Körpertemperatur einer Pflanze relativ zur Umgebung?"
- **Befund**: Frage suggeriert "Durchschnitt aller Pflanzen", aber Antwort/Funfact gilt nur für eine kleine Gruppe (Aronstabgewächse, Lotos). Die meisten Pflanzen sind tatsächlich ungefähr Umgebungstemperatur.
- **Fix**: Frage präzisiert auf "Welche Pflanzen können ihre Blütentemperatur aktiv heben?". Antwort jetzt eindeutig zur Frage passend.

### wissenschaft_de_078 🔧 (gefixt)

- **Original-Frage**: "Wovor schützt der Mantel beim Tintenfisch beim Verfolgen seiner Beute?"
- **Marked correct**: "Er nutzt ihn als Rückstoßantrieb"
- **Befund**: Klassischer Frage-Antwort-Mismatch — Frage fragt "wovor schützt", Antwort beschreibt aber Antrieb. Semantisch inkohärent.
- **Fix**: Frage umformuliert zu "Wie bewegt sich ein Tintenfisch blitzschnell vorwärts, wenn er Beute jagt oder flieht?". Antwort passt jetzt klar. Distraktor 3 ("Anziehung an Strömungen") ist plausibler als das ursprüngliche "wirft Mantel ab als Köder".

### wissenschaft_de_080 🗑️ (gelöscht)

- **Frage**: "Was ist das Spektakulärste an der Photosynthese auf Quantenebene?"
- **Marked correct**: "Sie nutzt Quantenkohärenz für hohe Effizienz"
- **Befund**: Die These der biologischen Quantenkohärenz in der Photosynthese (Engel 2007, Fleming et al.) wurde durch neuere Studien (Duan et al. 2017 in *Nature*, Cao et al. 2020) stark relativiert. Heute überwiegend als klassische vibrational coupling interpretiert. Als definitive Quiz-Antwort nicht mehr haltbar.
- **Entscheidung**: Gelöscht — Status des Phänomens umstritten in der aktuellen Forschung.

### wissenschaft_de_082 🗑️ (gelöscht)

- **Frage**: "Was ist die wichtigste Bestäuber-Insektengruppe nach Honigbienen?"
- **Marked correct**: "Wildbienen und Schwebfliegen"
- **Befund**: "Wichtigste" ist regional und studienabhängig — Schmetterlinge (Distraktor) und Käfer (Distraktor) sind in vielen Ökosystemen ebenfalls dominante Bestäuber. "Wildbienen und Schwebfliegen" als Paar ist außerdem eine künstliche Gruppierung. Die Wahl zwischen 4 plausiblen Gruppen ist nicht sauber entscheidbar.
- **Entscheidung**: Gelöscht — Ambiguität nicht auflösbar ohne Frage komplett neu zu schreiben.

### wissenschaft_de_087 🔧 (gefixt)

- **Original-Frage**: "Was ist die schnellste Bewegung im menschlichen Körper?"
- **Marked correct**: "Die Nervenimpulse selbst"
- **Befund**: "Schnellste Bewegung im Körper" ist mehrdeutig. Schallwellen im Körpergewebe sind mit ~1500 m/s deutlich schneller als Nervensignale (max. 120 m/s). Reflexionen, Blutfluss-Druckwellen etc. ebenfalls. Die Frage ist als Superlativ-Behauptung wackelig.
- **Fix**: Frage konkretisiert auf "Mit welcher Geschwindigkeit leiten die schnellsten Nervenfasern Signale weiter?" — keine Superlativ-Behauptung mehr, Antwort eindeutig korrekt.

### wissenschaft_de_089 🗑️ (gelöscht)

- **Frage**: "Welches Material ist gleichzeitig härter als Diamant und biegsam?"
- **Marked correct**: "Graphen"
- **Befund**: Verwechselt Zugfestigkeit mit Härte. Graphen ist sehr zugfest (~130 GPa, ~200× Stahl), aber im klassischen Härte-Sinn (Mohs-Skala, Indentation-Härte) NICHT härter als Diamant. Nur spezielle Konfigurationen wie die 2017 entdeckte "Diamen-Doppelschicht" zeigen unter Spitzendruck größere lokale Härte. Die Aussage "härter als Diamant" für reines Graphen ist populärwissenschaftliche Verkürzung.
- **Entscheidung**: Gelöscht — als definitive Quiz-Antwort zu missverständlich, Verwechslung der Materialeigenschaften.

### wissenschaft_de_099 🔧 (gefixt)

- **Original-Frage**: "Welches Phänomen erklärt, warum man Eis schneller schmelzen kann, wenn es vorher heißer war?"
- **Marked correct**: "Der Mpemba-Effekt"
- **Befund**: 🚨 Frage-Antwort-Mismatch. Der Mpemba-Effekt ist NICHT über das Schmelzen von Eis, sondern über das GEFRIEREN von heißem Wasser (heißes Wasser gefriert unter bestimmten Bedingungen schneller als kaltes). Frage ist faktisch falsch, Funfact beschreibt richtigen Mpemba-Effekt — Widerspruch.
- **Fix**: Frage korrigiert auf "Welches Phänomen beschreibt, dass heißes Wasser unter manchen Bedingungen schneller gefriert als kaltes?" — jetzt passend zur Antwort und zum Funfact.

## Fixes applied

| ID | What changed | Re-verify verdict |
|---|---|---|
| wissenschaft_de_054 | Frage komplett umformuliert; Funfact geschärft (Euklid-Beweis). | ✅ ok |
| wissenschaft_de_070 | Funfact ersetzt (Banane/Maus-Zahlen raus). | ✅ ok |
| wissenschaft_de_071 | Frage auf "Wirbeltier" spezifiziert; Funfact mit Marmor-Lungenfisch. | ✅ ok |
| wissenschaft_de_077 | Frage präzisiert (von "Durchschnitt aller Pflanzen" auf "welche Pflanzen heben Blütentemperatur"). | ✅ ok |
| wissenschaft_de_078 | Frage umformuliert ("wovor schützt" → "wie bewegt sich"); Distraktor 3 verbessert. | ✅ ok |
| wissenschaft_de_087 | Frage konkretisiert (von "schnellste Bewegung im Körper" → "Geschwindigkeit der Nervenfasern"). | ✅ ok |
| wissenschaft_de_099 | Frage von "Eis schmelzen" auf "heißes Wasser gefrieren" korrigiert. | ✅ ok |

## Deletions

| ID | Reason for deletion |
|---|---|
| wissenschaft_de_055 | Pi-Mäander (Stølum 1996) — Einzel-Studie, nicht reproduziert, pop-science. |
| wissenschaft_de_076 | Augenlinse als Alleinantwort faktisch wackelig — Hornhaut trägt ~2/3 der Brechkraft. |
| wissenschaft_de_080 | Quantenkohärenz-Photosynthese — durch Duan 2017 / Cao 2020 relativiert. |
| wissenschaft_de_082 | "Wichtigste Bestäubergruppe nach Honigbienen" — regional/studienabhängig, nicht eindeutig. |
| wissenschaft_de_089 | Graphen härter als Diamant — verwechselt Zugfestigkeit mit Härte. |

## Re-verification

7 gefixte Fragen wurden re-reviewt mit denselben 5 Kriterien:
- ✅ **Clean now**: 7
- ⚠️ **Still concerns**: 0
- 🚨 **Still fix_needed**: 0

(Gelöschte Fragen werden nicht re-verifiziert — sie sind raus.)

**Pack-Stand nach Review**: 100 → 95 Fragen (53 gereviewt: 41 clean + 7 gefixt + 5 gelöscht).
