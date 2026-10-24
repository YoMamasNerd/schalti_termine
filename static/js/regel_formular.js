document.addEventListener("DOMContentLoaded", function() {
  const scriptEl = document.querySelector('script[src*="regel_formular.js"]');
  const vorschauUrl = scriptEl ? scriptEl.dataset.vorschauUrl : null;
  const form = document.getElementById("regel-formular");
  const artSelect = document.getElementById("id_regel_art");
  const boxAngebot = document.getElementById("box_angebot");
  const boxSperre = document.getElementById("box_sperre");
  const intervallInput = document.getElementById("id_intervall_wochen");
  const boxReferenz = document.getElementById("box_referenzwoche");
  const referenzInput = document.getElementById("id_referenzwoche");
  const gueltigAbInput = document.getElementById("id_gueltig_ab");

  let referenzManuellGeaendert = false;
  if (referenzInput && gueltigAbInput && referenzInput.value && referenzInput.value !== "2024-01-01" && referenzInput.value !== gueltigAbInput.value) {
    referenzManuellGeaendert = true;
  }

  function toggleFields() {
    if (artSelect && boxAngebot && boxSperre) {
      if (artSelect.value === "sperre") {
        boxAngebot.style.display = "none";
        boxSperre.style.display = "block";
      } else {
        boxAngebot.style.display = "block";
        boxSperre.style.display = "none";
      }
    }
    if (intervallInput && boxReferenz) {
      const intervall = parseInt(intervallInput.value || "1", 10);
      if (intervall > 1) {
        boxReferenz.style.display = "block";
        if (referenzInput && gueltigAbInput && (!referenzInput.value || referenzInput.value === "2024-01-01") && gueltigAbInput.value) {
          referenzInput.value = gueltigAbInput.value;
        }
      } else {
        boxReferenz.style.display = "none";
      }
    }
  }

  if (referenzInput) {
    referenzInput.addEventListener("change", function() {
      referenzManuellGeaendert = true;
    });
  }

  if (gueltigAbInput && referenzInput) {
    gueltigAbInput.addEventListener("change", function() {
      if (!referenzManuellGeaendert && gueltigAbInput.value) {
        referenzInput.value = gueltigAbInput.value;
      }
    });
  }

  if (artSelect) {
    artSelect.addEventListener("change", toggleFields);
  }
  if (intervallInput) {
    intervallInput.addEventListener("input", toggleFields);
    intervallInput.addEventListener("change", toggleFields);
  }
  toggleFields();

  // Live-Vorschau bei Formular-Änderungen
  let vorschauTimer = null;
  async function aktualisiereVorschau() {
    if (!form || !vorschauUrl) return;
    const box = document.getElementById("regel-vorschau-box");
    if (!box) return;

    try {
      const formData = new FormData(form);
      const resp = await fetch(vorschauUrl, {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });
      if (resp.ok) {
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");
        const neueBox = doc.getElementById("regel-vorschau-box");
        if (neueBox) {
          box.replaceWith(neueBox);
        }
      }
    } catch (_) {
      // Vorschau-Fehler still ignorieren
    }
  }

  if (form && vorschauUrl) {
    form.addEventListener("change", function() {
      clearTimeout(vorschauTimer);
      vorschauTimer = setTimeout(aktualisiereVorschau, 150);
    });
    // Initiale Vorschau auch beim Anlegen einer neuen Regel laden
    const hatVorschauEintraege = document.querySelector("#regel-vorschau-box ul li");
    if (!hatVorschauEintraege) {
      setTimeout(aktualisiereVorschau, 100);
    }
  }
});
