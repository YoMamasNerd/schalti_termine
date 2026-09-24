(function() {
const scriptEl = document.currentScript;
  const schalter = document.querySelectorAll('.tab-schalter');
  const inhalte = document.querySelectorAll('.tab-inhalt');

  window.aktiviereTab = function(tabId) {
    if (!tabId || !document.getElementById(tabId)) {
      tabId = 'tab-mein-profil';
    }
    schalter.forEach(btn => {
      if (btn.getAttribute('data-tab') === tabId) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    inhalte.forEach(inhalt => {
      if (inhalt.id === tabId) {
        inhalt.style.display = 'block';
      } else {
        inhalt.style.display = 'none';
      }
    });
  };

  schalter.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      aktiviereTab(tabId);
      window.location.hash = tabId;
    });
  });

  // Hash aus URL lesen
  if (window.location.hash) {
    const hashId = window.location.hash.replace('#', '');
    if (document.getElementById(hashId)) {
      aktiviereTab(hashId);
    }
  }

  // Hilfsfunktion Zwischenablage
  window.kopiereInZwischenablage = function(text, label = 'Text') {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        if (typeof window.schaltiToast === 'function') {
          window.schaltiToast(`${label} in die Zwischenablage kopiert!`, 'success', 'check-circle');
        } else {
          showToast(`${label} kopiert!`);
        }
      }).catch(() => {
        prompt(`${label} kopieren:`, text);
      });
    } else {
      prompt(`${label} kopieren:`, text);
    }
  };

  // --- Live Auto-Save für alle Einstellungsformulare ---
  const toast = document.getElementById('settings-autosave-toast');
  const toastText = document.getElementById('settings-toast-text');
  let toastTimeout = null;

  function showToast(text, isError = false) {
    if (typeof window.schaltiToast === 'function') {
      window.schaltiToast(text, isError ? 'danger' : 'success', isError ? 'exclamation-circle' : 'check-circle');
    }
    // Auch DOM-Element aktualisieren für Tests/Fallback
    if (toast && toastText) {
      if (toastTimeout) clearTimeout(toastTimeout);
      toastText.textContent = text;
      toast.style.background = isError ? '#991b1b' : '#065f46';
      toast.style.display = 'inline-flex';
      setTimeout(() => { toast.style.opacity = '1'; }, 10);
      toastTimeout = setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => { toast.style.display = 'none'; }, 300);
      }, 2500);
    }
  }

  async function submitFormAsync(form) {
    const formData = new FormData(form);
    const actionUrl = form.getAttribute('action') || window.location.href;

    try {
      const response = await fetch(actionUrl, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json'
        }
      });
      const data = await response.json();
      if (data.ok) {
        showToast(data.nachricht || 'Automatisch gespeichert');
      } else {
        showToast('Fehler: ' + (data.fehler || 'Ungültige Eingabe'), true);
      }
    } catch (err) {
      showToast('Netzwerkfehler beim Speichern', true);
    }
  }

  document.querySelectorAll('.js-auto-save-form').forEach(form => {
    // Sofort speichern bei Select, Checkbox, Radio, Color
    form.querySelectorAll('select, input[type="checkbox"], input[type="radio"], input[type="color"]').forEach(el => {
      el.addEventListener('change', () => {
        submitFormAsync(form);
      });
    });

    // Debounced speichern bei Text, Email, Number, Textarea, Password
    const textInputs = form.querySelectorAll('input[type="text"], input[type="email"], input[type="number"], input[type="tel"], input[type="password"], textarea');
    let debounceTimer = null;
    textInputs.forEach(el => {
      el.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          submitFormAsync(form);
        }, 700);
      });
    });

    // Manueller Button-Klick: asynchron speichern ohne Neuladen
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitFormAsync(form);
    });
  });

  // --- SMTP Live-Test & Test-E-Mail ---
  const btnSmtpTest = document.getElementById('btn-smtp-live-test');
  const btnSmtpMail = document.getElementById('btn-sende-test-mail');
  const smtpTestBox = document.getElementById('smtp-test-box');
  const smtpTestSpinner = document.getElementById('smtp-test-spinner');
  const smtpTestErgebnis = document.getElementById('smtp-test-ergebnis');
  const formSmtp = document.getElementById('form-smtp');

  async function fuehreSmtpTestAus(aktion = 'auth') {
    if (!formSmtp) return;
    const formData = new FormData(formSmtp);
    formData.set('aktion', aktion);

    if (aktion === 'mail') {
      const empfInput = document.getElementById('smtp-test-mail-empfaenger');
      if (empfInput && empfInput.value) {
        formData.set('test_empfaenger', empfInput.value.trim());
      }
    }

    if (smtpTestBox) {
      smtpTestBox.style.display = 'block';
      smtpTestBox.style.background = 'var(--grund)';
      smtpTestBox.style.border = '1px solid var(--linie)';
    }
    if (smtpTestSpinner) smtpTestSpinner.style.display = 'flex';
    if (smtpTestErgebnis) smtpTestErgebnis.style.display = 'none';

    if (btnSmtpTest) btnSmtpTest.disabled = true;
    if (btnSmtpMail) btnSmtpMail.disabled = true;

    try {
      const resp = await fetch(scriptEl.dataset.smtpTestUrl, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        }
      });
      const daten = await resp.json();
      if (smtpTestSpinner) smtpTestSpinner.style.display = 'none';
      if (smtpTestErgebnis) {
        smtpTestErgebnis.style.display = 'block';
        if (daten.ok) {
          if (smtpTestBox) {
            smtpTestBox.style.background = 'var(--schalti-gruen-hell, #f0fdf4)';
            smtpTestBox.style.border = '1px solid var(--schalti-gruen, #86efac)';
          }
          smtpTestErgebnis.innerHTML = `
            <div style="display:flex; align-items:flex-start; gap:0.6rem; color:var(--schalti-gruen-dunkel, #166534)">
              <span style="font-size:1.2rem; line-height:1">✓</span>
              <div>
                <strong style="display:block; margin-bottom:0.2rem">${daten.meldung}</strong>
                ${daten.details ? `<div style="font-size:0.85rem;">${daten.details}</div>` : ''}
              </div>
            </div>
          `;
        } else {
          if (smtpTestBox) {
            smtpTestBox.style.background = 'var(--schalti-rot-hell, #fef2f2)';
            smtpTestBox.style.border = '1px solid var(--schalti-rot, #fca5a5)';
          }
          smtpTestErgebnis.innerHTML = `
            <div style="display:flex; align-items:flex-start; gap:0.6rem; color:var(--schalti-rot-dunkel, #991b1b)">
              <span style="font-size:1.2rem; line-height:1">✕</span>
              <div>
                <strong style="display:block; margin-bottom:0.2rem">${daten.meldung || 'Fehler beim SMTP-Test'}</strong>
                ${daten.details ? `<div style="font-size:0.85rem;">${daten.details}</div>` : ''}
              </div>
            </div>
          `;
        }
      }
    } catch (err) {
      if (smtpTestSpinner) smtpTestSpinner.style.display = 'none';
      if (smtpTestErgebnis) {
        smtpTestErgebnis.style.display = 'block';
        if (smtpTestBox) {
          smtpTestBox.style.background = 'var(--schalti-rot-hell, #fef2f2)';
          smtpTestBox.style.border = '1px solid var(--schalti-rot, #fca5a5)';
        }
        smtpTestErgebnis.innerHTML = `
          <div style="display:flex; align-items:flex-start; gap:0.6rem; color:var(--schalti-rot-dunkel, #991b1b)">
            <span style="font-size:1.2rem; line-height:1">✕</span>
            <div>
              <strong style="display:block; margin-bottom:0.2rem">Netzwerk- oder Serverfehler</strong>
              <div style="font-size:0.85rem;">${err.message || err}</div>
            </div>
          </div>
        `;
      }
    } finally {
      if (btnSmtpTest) btnSmtpTest.disabled = false;
      if (btnSmtpMail) btnSmtpMail.disabled = false;
    }
  }

  if (btnSmtpTest) {
    btnSmtpTest.addEventListener('click', () => fuehreSmtpTestAus('auth'));
  }
  if (btnSmtpMail) {
    btnSmtpMail.addEventListener('click', () => fuehreSmtpTestAus('mail'));
  }
})();
