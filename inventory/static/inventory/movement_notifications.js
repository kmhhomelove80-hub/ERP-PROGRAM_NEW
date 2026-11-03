/* Read-only pending-request notice for employees allowed to approve movements. */
(() => {
  const notice = document.getElementById('movement-pending-notice');
  if (!notice) return;
  let busy = false;
  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    try {
      const response = await fetch(notice.dataset.notificationsUrl, {cache: 'no-store', credentials: 'same-origin', redirect: 'error'});
      if (response.status === 403) { notice.hidden = true; return; }
      if (!response.ok) return;
      const result = await response.json();
      if (!Number.isInteger(result.pending) || result.pending < 0) return;
      notice.querySelector('[data-pending-count]').textContent = result.pending.toLocaleString();
      notice.hidden = result.pending === 0;
    } catch (_) { /* Preserve the last known count; never change stock from here. */ }
    finally { busy = false; }
  }
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('focus', refresh);
})();
