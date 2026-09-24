(function() {
  const artSelect = document.querySelector('select[name="regel_art"]');
  const terminartWrap = document.getElementById('feld-terminart-wrap');
  const sperrzeitTypWrap = document.getElementById('feld-sperrzeit-typ-wrap');
  const grundWrap = document.getElementById('feld-grund-wrap');

  function updateSichtbarkeit() {
    if (!artSelect) return;
    const istSperre = artSelect.value === 'sperre';
    if (terminartWrap) terminartWrap.style.display = istSperre ? 'none' : 'block';
    if (sperrzeitTypWrap) sperrzeitTypWrap.style.display = istSperre ? 'block' : 'none';
    if (grundWrap) grundWrap.style.display = istSperre ? 'block' : 'none';
  }

  if (artSelect) {
    artSelect.addEventListener('change', updateSichtbarkeit);
    updateSichtbarkeit();
  }
})();
