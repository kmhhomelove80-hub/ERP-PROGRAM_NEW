(() => {
  const form = document.querySelector('[data-live-filter], #stock-filter');
  if (!form) return;
  const results = document.querySelector(form.dataset.results || '#stock-results');
  const status = document.querySelector(form.dataset.status || '#stock-search-status');
  let timer, controller, serial = 0;
  async function refresh(url) {
    clearTimeout(timer);
    const version = ++serial;
    if (controller) controller.abort();
    controller = new AbortController();
    status.textContent = '조회 중…';
    results.setAttribute('aria-busy', 'true');
    const endpoint = new URL(url);
    endpoint.searchParams.set('partial', '1');
    try {
      const response = await fetch(endpoint, {signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('request failed');
      const html = await response.text();
      if (version !== serial) return;
      results.innerHTML = html;
      history.replaceState(null, '', url);
      const download = document.querySelector('#stock-export');
      if (download) download.search = new URL(url).search;
      status.textContent = '조회 완료';
    } catch (error) {
      if (error.name !== 'AbortError' && version === serial) status.textContent = '조회하지 못했습니다. 검색 버튼으로 다시 시도하세요.';
    } finally {
      if (version === serial) results.removeAttribute('aria-busy');
    }
  }
  function url() {
    const target = new URL(location.href);
    const params = new URLSearchParams(new FormData(form));
    for (const key of ['sort', 'direction']) {
      if (target.searchParams.has(key)) params.set(key, target.searchParams.get(key));
    }
    target.search = params.toString();
    return target;
  }
  function schedule(event) {
    if (event?.target.name === 'q' && form.elements.scan) form.elements.scan.value = '';

    clearTimeout(timer);
    status.textContent = '조회 대기…';
    // Invalidate and cancel in-flight work immediately, even during debounce.
    serial++;
    if (controller) controller.abort();
    timer = setTimeout(() => refresh(url()), 300);
  }
  // Some phone keyboards keep composition active until blur. Search the current
  // text after a pause even during composition; only replace results, never the input.
  form.addEventListener('compositionstart', schedule);
  form.addEventListener('compositionupdate', schedule);
  form.addEventListener('compositionend', schedule);
  form.addEventListener('input', schedule);
  form.addEventListener('change', schedule);
  form.addEventListener('submit', event => {event.preventDefault(); refresh(url());});
  results.addEventListener('click', event => {
    const link = event.target.closest('nav a, a[data-sort-link]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      if (event.button !== 0 || event.altKey) return;
      event.preventDefault();
      const target = new URL(link.href);
      if (link.hasAttribute('data-sort-link')) {
        for (const [key, value] of new FormData(form)) target.searchParams.set(key, value);
      }
      refresh(target);
    }
  });
})();
