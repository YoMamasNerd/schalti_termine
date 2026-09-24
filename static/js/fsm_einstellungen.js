const settingsForm = document.getElementById('fsm-settings-form');
const toast = document.getElementById('fsm-autosave-toast');
const toastText = document.getElementById('fsm-toast-text');
let toastTimeout = null;

function showToast(text, isError = false) {
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

async function autoSaveSettings(extraParams = {}) {
  if (!settingsForm) return;
  const formData = new FormData(settingsForm);
  for (const [k, v] of Object.entries(extraParams)) {
    formData.append(k, v);
  }

  try {
    const response = await fetch(window.location.href, {
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
      if (data.intervall_text) {
        const statusP = document.getElementById('fsm-sync-status-text');
        if (statusP) {
          statusP.textContent = 'Hintergrund-Synchronisation: ' + data.intervall_text + '.';
        }
      }
      return data;
    } else {
      showToast('Fehler beim Speichern', true);
    }
  } catch (err) {
    showToast('Netzwerkfehler beim Speichern', true);
  }
}

// Auto-Save bei allen Dropdowns, Checkboxen und Eingaben
if (settingsForm) {
  settingsForm.querySelectorAll('select, input[type="checkbox"]').forEach(el => {
    el.addEventListener('change', () => {
      autoSaveSettings();
    });
  });

  // Explizites Speichern per Button
  settingsForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitter = e.submitter;
    const isSync = submitter && submitter.name === 'sync_nach_speichern';

    if (isSync) {
      const loadingBox = document.getElementById('fsm-loading');
      const loadingText = document.getElementById('fsm-loading-text');
      const feedbackBox = document.getElementById('fsm-ajax-feedback');

      loadingText.textContent = 'Speichere Einstellungen & synchronisiere…';
      loadingBox.style.display = 'inline-flex';
      feedbackBox.style.display = 'none';
      document.querySelectorAll('#fsm-actions button, #btn-save-settings, #btn-save-and-sync').forEach(b => b.disabled = true);

      const result = await autoSaveSettings({ 'sync_nach_speichern': '1' });
      loadingBox.style.display = 'none';
      document.querySelectorAll('#fsm-actions button, #btn-save-settings, #btn-save-and-sync').forEach(b => b.disabled = false);

      if (result && result.ok) {
        feedbackBox.className = 'hinweis hinweis--erfolg';
        feedbackBox.innerHTML = '<strong>' + result.nachricht + '</strong>';
        feedbackBox.style.display = 'block';
        setTimeout(() => { window.location.reload(); }, 1200);
      }
    } else {
      autoSaveSettings();
    }
  });
}

// Action buttons (Import & Sofort-Sync)
document.querySelectorAll('.js-fsm-async-form').forEach(form => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const action = form.querySelector('input[name="aktion"]').value;
    const loadingBox = document.getElementById('fsm-loading');
    const loadingText = document.getElementById('fsm-loading-text');
    const feedbackBox = document.getElementById('fsm-ajax-feedback');

    if (action === 'import_fahrlehrer') {
      loadingText.textContent = 'Importiere Fahrlehrer aus FSM…';
    } else {
      loadingText.textContent = 'Synchronisiere Fahrpläne mit FSM…';
    }

    document.querySelectorAll('#fsm-actions button, #btn-save-settings, #btn-save-and-sync').forEach(b => b.disabled = true);
    loadingBox.style.display = 'inline-flex';
    feedbackBox.style.display = 'none';

    try {
      const formData = new FormData(form);
      const response = await fetch(window.location.href, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json'
        }
      });

      const data = await response.json();
      loadingBox.style.display = 'none';

      if (data.ok) {
        feedbackBox.className = 'hinweis hinweis--erfolg';
        feedbackBox.innerHTML = '<strong>' + data.nachricht + '</strong>';
        feedbackBox.style.display = 'block';
        setTimeout(() => { window.location.reload(); }, 1200);
      } else {
        feedbackBox.className = 'hinweis hinweis--fehler';
        feedbackBox.innerHTML = '<strong>Fehler:</strong> ' + (data.fehler || 'Unbekannter Fehler');
        feedbackBox.style.display = 'block';
        document.querySelectorAll('#fsm-actions button, #btn-save-settings, #btn-save-and-sync').forEach(b => b.disabled = false);
      }
    } catch (err) {
      loadingBox.style.display = 'none';
      feedbackBox.className = 'hinweis hinweis--fehler';
      feedbackBox.innerHTML = '<strong>Netzwerkfehler:</strong> ' + err.message;
      feedbackBox.style.display = 'block';
      document.querySelectorAll('#fsm-actions button, #btn-save-settings, #btn-save-and-sync').forEach(b => b.disabled = false);
    }
  });
});
