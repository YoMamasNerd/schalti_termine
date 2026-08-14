# Terminauswahl in die eigene Seite einbetten

Die Terminauswahl gibt es unter `/einbetten/` als eigene Seite ohne Kopfzeile,
Navigation und Fuß. Sie ist dafür gedacht, in einem `<iframe>` mitten in der
Seite der Fahrschule zu sitzen.

**Ist ein iframe noch zeitgemäß?** Ja. Jedes Buchungs-Widget arbeitet so, und
was als „modernes Einbindungs-Skript" verkauft wird, setzt am Ende genau
diesen Rahmen – nur mit mehr JavaScript dazwischen. Der Rahmen hat einen
handfesten Vorteil: Was darin passiert, kann das Aussehen der umgebenden Seite
nicht durcheinanderbringen, und umgekehrt.

---

## Der kürzeste Weg

Zwei Schritte.

**1 · Der Fahrschulseite die Einbettung erlauben.** In der `.env`:

```bash
EMBED_ORIGINS=https://fahrschule-schaltwerk.de,https://www.fahrschule-schaltwerk.de
```

Ohne diesen Eintrag darf **niemand** die Seite einrahmen – auch nicht die
eigene. Das ist Absicht: Ein offener Rahmen lädt zum Klickfang ein, bei dem
eine fremde Seite die Auswahl unsichtbar über ihre eigenen Schaltflächen legt.
Beide Schreibweisen eintragen, mit und ohne `www`, sonst bleibt eine davon
leer.

Nach der Änderung `docker compose up -d` erneut ausführen.

**2 · In die Seite einsetzen**, dort wo die Auswahl erscheinen soll:

```html
<iframe src="https://termine.meine-fahrschule.de/einbetten/"
        title="Freie Beratungstermine"
        style="width:100%;height:760px;border:0"
        loading="lazy"></iframe>
```

Das war es. Ein Klick auf eine Uhrzeit öffnet das Buchungsformular in einem
neuen Tab.

---

## Mitwachsende Höhe

Ein Rahmen wächst nicht mit seinem Inhalt: Er ist so hoch, wie die umgebende
Seite ihn macht. Mit `height:760px` genügt das meistens; wählt jemand einen Tag
mit vielen Uhrzeiten, bekommt der Rahmen eine eigene Bildlaufleiste.

Wer das sauberer möchte, ergänzt einmalig dieses Stück auf der eigenen Seite.
Die Auswahl meldet ihre Höhe von sich aus – hier steht nur das Gegenstück:

```html
<iframe id="termine-rahmen"
        src="https://termine.meine-fahrschule.de/einbetten/"
        title="Freie Beratungstermine"
        style="width:100%;height:760px;border:0"
        loading="lazy"></iframe>

<script>
  window.addEventListener("message", function (e) {
    // Nur der eigenen Terminseite glauben – sonst könnte jede fremde Seite
    // die Höhe des Rahmens fernsteuern.
    if (e.origin !== "https://termine.meine-fahrschule.de") return;
    if (!e.data || e.data.typ !== "schalti-termine:hoehe") return;
    document.getElementById("termine-rahmen").style.height = e.data.hoehe + "px";
  });
</script>
```

Bleibt das Stück weg, funktioniert die Auswahl trotzdem – der Rahmen scrollt
dann eben selbst.

---

## Nur einen Fahrlehrer oder eine Terminart zeigen

Die Auswahl versteht dieselben Parameter wie die öffentliche Seite:

```html
<!-- Nur die Termine von Anna -->
<iframe src="https://termine.meine-fahrschule.de/einbetten/?fahrlehrer=anna-berger" …>

<!-- Nur Erstberatungen -->
<iframe src="https://termine.meine-fahrschule.de/einbetten/?art=erstberatung" …>
```

Die Kürzel stehen im Django-Admin beim jeweiligen Eintrag unter „URL-Kürzel".
Ist nur ein Fahrlehrer oder nur eine Terminart aktiv, entfallen die
Auswahlfelder ohnehin von selbst.

---

## Warum das Formular den Rahmen verlässt

Der Klick auf eine Uhrzeit öffnet das Buchungsformular in einem neuen Tab,
statt es im Rahmen zu zeigen. Das ist kein Versehen.

Ein Formular braucht Cookies – für den Schutz vor gefälschten Absendungen.
Cookies in einem fremden Rahmen gelten für Browser als Cookies von Dritten,
und die werden zunehmend blockiert; in Safari seit Jahren, in Chrome je nach
Einstellung. Bliebe das Formular im Rahmen, würde die Buchung bei einem Teil
der Kundschaft ohne erkennbaren Grund scheitern. Der Kalender selbst liest nur
und kommt ohne Cookies aus – er kann bleiben, wo er ist.

> **Ausnahme:** Läuft die Buchung auf einer Unterdomain derselben Domain – also
> `termine.fahrschule-schaltwerk.de` unter `fahrschule-schaltwerk.de` –, gelten
> die Cookies nicht als Cookies von Dritten, und der ganze Ablauf könnte im
> Rahmen bleiben. Wer das möchte, sagt Bescheid; es ist eine kleine Änderung,
> aber sie steht und fällt mit dieser Voraussetzung.

---

## Aussehen

Die Auswahl bringt das Stylesheet der App mit, dessen Farben von der
Fahrschulseite stammen – sie sollte sich also einfügen. Der Hintergrund des
Rahmens ist durchsichtig, die umgebende Fläche scheint durch.

Die helle oder dunkle Fassung richtet sich nach der Einstellung des Besuchers,
nicht nach der umgebenden Seite. Wer eine dunkle Seite betreibt, auf der
Besucher mit heller Systemeinstellung landen, sieht die Auswahl hell im
Dunklen. Falls das stört: sagen, dann wird die Fassung über einen Parameter
festlegbar.

---

## Wenn nichts erscheint

| Beobachtung | Ursache |
| --- | --- |
| Der Rahmen bleibt leer, die Konsole meldet etwas zu `frame-ancestors` | Die Adresse der Fahrschulseite steht nicht in `EMBED_ORIGINS` – oder mit `www`, während sie ohne aufgerufen wird. |
| Der Rahmen bleibt leer, die Konsole meldet `X-Frame-Options` | `EMBED_ORIGINS` ist leer. Dann bleibt die Sperre bewusst bestehen. |
| Der Rahmen zeigt die Auswahl, aber abgeschnitten | Die Höhe reicht nicht. Entweder `height` erhöhen oder das Stück von oben ergänzen. |
| Ein Klick auf eine Uhrzeit tut nichts | Ein Popup-Blocker hält den neuen Tab auf. Selten, weil der Klick vom Besucher kommt. |
| Die Auswahl taucht doppelt bei Google auf | Sollte nicht passieren: Die Seite trägt `noindex`. |
