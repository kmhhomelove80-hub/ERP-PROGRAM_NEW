/* Common shell, safe whole-result scrolling and transient navigation restoration. */
(() => {
 const main=document.querySelector('main'),header=document.querySelector('body>header');if(!main)return;
 const toggle=document.querySelector('#mobile-menu-toggle');
 toggle?.addEventListener('click',()=>{const open=header.classList.toggle('menu-open');toggle.setAttribute('aria-expanded',String(open));});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){header?.classList.remove('menu-open');toggle?.setAttribute('aria-expanded','false');}});
 if(header)new ResizeObserver(()=>document.documentElement.style.setProperty('--header-height',header.offsetHeight+'px')).observe(header);
 let busy=false,controller=null,restoring=false,autoPaused=false;let observed=null;const loaded=[];
 const nav=()=>main.querySelector('[data-scroll-page]');
 const table=()=>[...main.querySelectorAll('.table')].find(t=>t.offsetHeight>0);
 const next=()=>nav()?.querySelector('[data-scroll-next]');
 function mark(){document.body.classList.toggle('list-incomplete',!!main.querySelector('[data-scroll-next],[data-load-more]'));}
 const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!restoring)maybeLoad();},{rootMargin:'200px'});
 function watch(){
  const link=next();mark();if(observed===link)return;
  if(observed)observer.unobserve(observed);observed=link;if(link&&!autoPaused)observer.observe(link);
 }
 async function load(url=next()?.href){
  if(busy||!url)return false;const oldNav=nav();if(!oldNav)return false;
  busy=true;autoPaused=false;controller=new AbortController();const status=oldNav.querySelector('[data-scroll-status]');status.textContent='불러오는 중…';
  try{
   const response=await fetch(url,{signal:controller.signal});if(!response.ok||response.redirected)throw Error('조회 실패');
   const html=await response.text();if(!oldNav.isConnected)return false;
   const doc=new DOMParser().parseFromString(html,'text/html'),body=doc.querySelector('main')||doc;
   const newNav=body.querySelector('[data-scroll-page]');if(!newNav)throw Error('목록 형식 변경');
   const movement=main.querySelector('.movement-cards'),incoming=body.querySelector('.movement-cards');
   if(movement&&incoming){const seen=new Set([...movement.children].map(n=>n.dataset.recordId));[...incoming.children].filter(n=>!seen.has(n.dataset.recordId)).forEach(n=>movement.append(n));}
   else{
    const targets=[...main.querySelectorAll('table tbody')],sources=[...body.querySelectorAll('table tbody')];
    if(!targets.length||targets.length!==sources.length)throw Error('목록 형식 변경');
    targets.forEach((target,i)=>{const seen=new Set([...target.children].map(n=>n.dataset.recordId).filter(Boolean));[...sources[i].children].filter(n=>!n.dataset.recordId||!seen.has(n.dataset.recordId)).forEach(n=>target.append(n));});
   }
   oldNav.replaceWith(newNav);loaded.push(url);document.dispatchEvent(new Event('list-appended'));return true;
  }catch(error){
   if(error.name!=='AbortError'&&oldNav.isConnected){status.textContent='추가 조회 실패 · 아래 링크를 눌러 다시 시도하세요.';observer.disconnect();observed=null;autoPaused=true;}
   return false;
  }finally{busy=false;controller=null;watch();}
 }
 function maybeLoad(){if(autoPaused)return;const t=table();if(t&&t.scrollHeight>t.clientHeight+20&&t.scrollTop+t.clientHeight<t.scrollHeight-200)return;load();}
 main.addEventListener('scroll',e=>{if(e.target.matches?.('.table')&&!restoring)maybeLoad();},true);
 document.addEventListener('click',e=>{
  const link=e.target.closest('[data-scroll-next]');if(link&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey){e.preventDefault();e.stopImmediatePropagation();load(link.href);}
 },true);
 new MutationObserver(()=>{if(controller&&observed&&!observed.isConnected)controller.abort();watch();}).observe(main,{childList:true,subtree:true});watch();
 document.addEventListener('change',e=>{
  if(e.target.matches('[data-check-all]'))document.querySelectorAll('.'+e.target.dataset.checkAll).forEach(b=>b.checked=e.target.checked);
 });
 document.addEventListener('submit',e=>{
  const form=e.target;
  if(form.dataset.submitting==='yes'){e.preventDefault();e.stopImmediatePropagation();return;}
  let message=form.dataset.finalConfirm;
  if(form.matches('[data-stocktake-start]'))message=form.querySelector('[name=kind]').value==='count'?'반드시 모든 실사 대상 자재의 PICKING 및 포장을 완료하고 로케이션 재고와 분리한 상태에서 재고조사를 진행해야 합니다.\nPICKING·포장이 끝나지 않았다면 취소하고 완료 후 다시 시도하세요.\n전산 출고는 완료하지 않아도 됩니다.\n확인: 완료 후 진행 / 취소: 나중에 진행':'포장분을 포함한 전산 현재고를 조정합니다. 관련 처리를 잠시 중지했는지 확인하세요.';
  if(form.id==='allocation-action-form')message+=`\n이번 처리 수량: ${document.querySelector('#processing-quantity').textContent}개\n처리 범위: ${form.querySelector('[name=scope]:checked').value==='filtered'?'필터 결과 전체':'체크한 행'}`;
  if(message&&!confirm(message)){e.preventDefault();e.stopImmediatePropagation();return;}
  if(form.method.toLowerCase()==='post'){
   form.dataset.submitting='yes';form.setAttribute('aria-busy','true');
   if(location.pathname.startsWith('/production/settings/')){
    const status=document.createElement('p');status.setAttribute('role','status');status.textContent='파일 읽기·검사 중입니다. 완료되면 다음 화면으로 이동합니다.';form.append(status);
   }
  }
 },true);
 const key='sunil-view:'+document.body.dataset.user+':'+location.pathname+location.search;
 window.addEventListener('pagehide',()=>{
  try{sessionStorage.setItem(key,JSON.stringify({time:Date.now(),urls:loaded.slice(0,100),y:scrollY,tables:[...main.querySelectorAll('.table')].map(t=>[t.scrollTop,t.scrollLeft]),selected:[...main.querySelectorAll('[name=allocations]:checked')].map(b=>[b.value,b.closest('tr').innerText])}));}catch{}
 });
 window.addEventListener('pageshow',async event=>{
  document.querySelectorAll('form[data-submitting]').forEach(f=>{delete f.dataset.submitting;f.removeAttribute('aria-busy');});main.querySelectorAll('[name=allocations]').forEach(b=>b.disabled=false);
  if(event.persisted){document.dispatchEvent(new Event('selection-restored'));return;}
  if(performance.getEntriesByType('navigation')[0]?.type!=='back_forward')return;
  try{
   const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(!saved||Date.now()-saved.time>1800000)return;
   restoring=true;for(const url of saved.urls){if(!next()||!await load(next().href))break;}
   const selected=new Map(saved.selected);main.querySelectorAll('[name=allocations]').forEach(b=>{b.checked=selected.get(b.value)===b.closest('tr').innerText;});
   document.dispatchEvent(new Event('selection-restored'));main.querySelectorAll('.table').forEach((t,i)=>{if(saved.tables[i]){t.scrollTop=saved.tables[i][0];t.scrollLeft=saved.tables[i][1];}});scrollTo(0,saved.y);
  }catch{}finally{restoring=false;watch();}
 });
 document.addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button||!button.getAttribute('onclick')?.includes('window.print')||!next())return;
  e.preventDefault();e.stopImmediatePropagation();button.disabled=true;
  try{while(next()){if(!await load())throw Error('목록을 끝까지 불러오지 못했습니다. 다시 시도하세요.');}window.print();}catch(error){alert(error.message);}finally{button.disabled=false;}
 },true);
})();
