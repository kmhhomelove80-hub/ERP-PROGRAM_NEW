(() => {
  const form = document.querySelector('#receipt-form');
  if (!form) return;
  function update() {
    const isNew = form.elements.item_mode.value === 'new';
    for (const [selector, visible] of [['#receipt-new', isNew], ['#receipt-existing', !isNew]]) {
      const section = form.querySelector(selector);
      section.hidden = !visible;
      for (const field of section.querySelectorAll('input, select, textarea')) {
        field.disabled = !visible;
        field.required = visible && ['item', 'new_code', 'new_name'].includes(field.name);
      }
    }
  }
  form.addEventListener('change', event => { if (event.target.name === 'item_mode') update(); });
  update();
})();
