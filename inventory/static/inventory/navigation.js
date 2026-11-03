/* Exclusive navigation, including browsers without details[name] support. */
(() => {
  const nav = document.querySelector('.primary-nav');
  if (!nav) return;
  const menus = [...nav.querySelectorAll(':scope > details')];
  document.addEventListener('click', event => {
    const summary = event.target.closest('summary');
    const current = summary && menus.includes(summary.parentElement)
      ? summary.parentElement : null;
    if (current) {
      // Close peers synchronously before the browser toggles this summary.
      menus.forEach(menu => { if (menu !== current) menu.open = false; });
    } else if (!nav.contains(event.target)) {
      menus.forEach(menu => { menu.open = false; });
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = menus.find(menu => menu.open);
    if (!open) return;
    menus.forEach(menu => { menu.open = false; });
    open.querySelector('summary').focus();
    event.preventDefault();
  });
})();
