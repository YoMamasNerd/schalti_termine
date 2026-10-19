(function() {
  const scriptEl = document.currentScript;
  const btn = document.getElementById('btn-live-check');
  if (!scriptEl || !btn) return;

  const form = btn.closest('form');
  const hinweis = document.getElementById('live-check-hinweis');
  const umhaengenButtons = document.querySelectorAll('#karte-wechsel button[type="submit"]');
  const originalDisabled = Array.from(umhaengenButtons).map(b => b.disabled);

  function badgeFuer(pk) {
    return document.querySelector('.marke-status[data-fl-pk="' + pk + '"]');
  }

  btn.addEventListener('click', async () => {
    if (!form) return;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Prüft…';
    umhaengenButtons.forEach(b => { b.disabled = true; });
    if (hinweis) hinweis.style.display = 'none';

    try {
      const formData = new FormData(form);
      const resp = await fetch(scriptEl.dataset.liveCheckUrl, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json'
        }
      });
      const daten = await resp.json();
      if (!daten.ok) {
        throw new Error(daten.meldung || 'Live-Prüfung fehlgeschlagen.');
      }
      daten.ergebnisse.forEach(erg => {
        const badge = badgeFuer(erg.pk);
        if (!badge) return;
        badge.classList.remove('status-frei', 'status-gebucht', 'status-reserviert');
        if (erg.verfuegbar === true) {
          badge.classList.add('status-frei');
          badge.textContent = 'frei';
          badge.removeAttribute('title');
        } else if (erg.verfuegbar === false) {
          badge.classList.add('status-gebucht');
          badge.textContent = 'belegt';
          badge.setAttribute('title',
            erg.konflikte && erg.konflikte.length
              ? erg.konflikte.join(', ')
              : 'Belegt oder gesperrt im Zeitfenster dieses Termins.');
        } else {
          badge.classList.add('status-reserviert');
          badge.textContent = 'unbekannt';
          badge.setAttribute('title',
            erg.fsm === 'fehler'
              ? 'FSM-Fehler: ' + (erg.meldung || 'unbekannt')
              : 'FSM-Live-Prüfung für diesen Fahrlehrer nicht aktiv.');
        }
      });
    } catch (err) {
      if (hinweis) {
        hinweis.textContent = err.message || 'Live-Prüfung fehlgeschlagen.';
        hinweis.style.display = 'inline';
      }
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
      umhaengenButtons.forEach((b, i) => { b.disabled = originalDisabled[i]; });
    }
  });
})();