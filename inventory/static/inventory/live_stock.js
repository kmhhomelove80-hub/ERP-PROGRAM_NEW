(() => {
  const form = document.querySelector('[data-live-filter], #stock-filter');
  if (!form) return;
  const results = document.querySelector(form.dataset.results || '#stock-results');
  const status = document.querySelector(form.dataset.status || '#stock-search-status');
  let timer, controller, serial = 0, moreController, moreBusy=false,morePaused=false;
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting)) maybeMore();
  }, {rootMargin:'300px'}) : null;
  function maybeMore() {
    if(morePaused)return;
    const table=results.querySelector('.desktop-stock-table');
    if(table?.offsetHeight && table.scrollHeight>table.clientHeight+20 && table.scrollTop+table.clientHeight<table.scrollHeight-Math.max(240,table.clientHeight))return;
    loadMore();
  }
  results.addEventListener('scroll',e=>{if(e.target.matches('.desktop-stock-table'))maybeMore();},true);
  function watchMore() {
    observer?.disconnect();
    const link=results.querySelector('[data-load-more]'),nav=results.querySelector('[data-stock-more]');
    nav?.classList.add('auto-scroll-ready');
    if(link&&nav&&!morePaused)observer?.observe(nav);
  }
  function cancelMore() { moreController?.abort(); moreController=null; moreBusy=false; morePaused=false; observer?.disconnect(); }
  async function loadMore() {
    const link=results.querySelector('[data-load-more]');
    if(!link || moreBusy) return;
    const version=serial;
    moreBusy=true;morePaused=false;const nav=link.closest('nav');nav.classList.remove('load-failed');const state=nav.querySelector('[data-stock-load-status]');if(state)state.textContent='불러오는 중…'; moreController=new AbortController();
    const endpoint=new URL(link.href); endpoint.searchParams.set('partial','1');
    link.textContent='불러오는 중…';
    try {
      const response=await fetch(endpoint,{signal:moreController.signal});
      if(!response.ok || response.redirected) throw Error('request failed');
      const html=await response.text(); if(version!==serial) return;
      const next=new DOMParser().parseFromString(html,'text/html');
      for(const selector of ['.desktop-stock-table tbody','.mobile-stock-cards']) {
        const target=results.querySelector(selector);
        const existing=new Set([...target.querySelectorAll('[data-stock-id]')].map(n=>n.dataset.stockId));
        next.querySelectorAll(selector+' > [data-stock-id]').forEach(row=>{
          if(!existing.has(row.dataset.stockId)) target.append(row);
        });
      }
      results.querySelector('[data-stock-more]').replaceWith(next.querySelector('[data-stock-more]'));
      status.textContent='추가 조회 완료';
    } catch(error) {
      if(error.name!=='AbortError' && version===serial) {
        nav.classList.add('load-failed');link.textContent='다시 시도';if(state)state.textContent='추가 조회 실패';morePaused=true;observer?.disconnect();return;
      }
    } finally { if(version===serial) moreBusy=false; }
    if(version===serial) watchMore();
  }
  results.addEventListener('click',event=>{
    if(event.target.closest('[data-load-more]') && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault(); event.stopImmediatePropagation(); loadMore();
    }
  });
  watchMore();
  async function refresh(url) {
    clearTimeout(timer);
    cancelMore();
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
      watchMore();
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
    cancelMore();
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
