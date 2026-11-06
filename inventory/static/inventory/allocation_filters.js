(()=>{
 const dialog=document.querySelector('#allocation-filter-dialog');if(!dialog)return;
 const form=document.querySelector('#column-filter-form'),values=document.querySelector('#column-filter-values'),search=document.querySelector('#column-option-search'),more=document.querySelector('#column-option-more'),status=document.querySelector('#column-filter-status');
 const current=JSON.parse(document.querySelector('#allocation-filter-data').textContent),url=new URL(location.href);let field='',selected=new Set(),offset=0,controller=null,serial=0,timer=null;
 for(const button of document.querySelectorAll('.column-filter')){
  const key=button.dataset.field;button.classList.toggle('active',!!(current['f_'+key]||current['x_'+key]||key==='quantity'&&(current.min_quantity||current.max_quantity)));
  button.addEventListener('click',()=>{
   field=key;selected=new Set(current['f_'+key]||[]);form.elements.contains.value=current['x_'+key]||'';form.elements.minimum.value=current.min_quantity||'';form.elements.maximum.value=current.max_quantity||'';
   document.querySelector('#column-filter-title').textContent=button.textContent.replace('▾','')+' 필터';document.querySelector('#column-quantity-range').hidden=field!=='quantity';search.value='';values.replaceChildren();offset=0;dialog.showModal();load(true);
  });
 }
 async function load(reset){
  const mine=++serial;if(controller)controller.abort();controller=new AbortController();status.textContent='전체 예약 자료에서 값을 조회 중…';more.hidden=true;
  if(reset){values.replaceChildren();offset=0;}
  try{
   const target=new URL(dialog.dataset.optionsUrl,location.origin);target.search=url.search;target.searchParams.set('field',field);target.searchParams.set('search',search.value);target.searchParams.set('offset',offset);target.searchParams.delete('page');
   const response=await fetch(target,{signal:controller.signal});if(!response.ok||response.redirected)throw Error('조회에 실패했습니다. 로그인 상태와 조건을 확인하세요.');const data=await response.json();if(mine!==serial)return;
   for(const item of data.values){const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=item.value;box.checked=selected.has(item.value);box.addEventListener('change',()=>{if(box.checked)selected.add(box.value);else selected.delete(box.value);});label.append(box,document.createTextNode(item.label+' ('+item.count+'행)'));values.append(label);}
   offset=data.next_offset;more.hidden=offset===null;status.textContent='여러 값을 선택하거나 포함 조건으로 검색하세요. 선택하지 않으면 이 열의 값 제한은 없습니다.';
  }catch(e){if(e.name!=='AbortError'&&mine===serial)status.textContent=e.message;}
 }
 search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>load(true),250);});more.addEventListener('click',()=>load(false));
 function go(clear){
  const next=new URL(location.href);next.searchParams.delete('f_'+field);next.searchParams.delete('x_'+field);next.searchParams.delete('page');next.searchParams.delete('partial');
  if(field==='quantity'){next.searchParams.delete('min_quantity');next.searchParams.delete('max_quantity');}
  if(field==='project')next.searchParams.delete('q');if(field==='seq')next.searchParams.delete('seq');
  if(!clear){for(const v of selected)next.searchParams.append('f_'+field,v);if(form.elements.contains.value.trim())next.searchParams.set('x_'+field,form.elements.contains.value.trim());if(field==='quantity'){if(form.elements.minimum.value)next.searchParams.set('min_quantity',form.elements.minimum.value);if(form.elements.maximum.value)next.searchParams.set('max_quantity',form.elements.maximum.value);}}
  next.searchParams.set('changed','1');
  try{sessionStorage.removeItem('sunil-view:'+document.body.dataset.user+':'+location.pathname+location.search);}catch{}
  location.assign(next);
 }
 form.addEventListener('submit',e=>{e.preventDefault();if(selected.size>200){status.textContent='열당 200개 이하를 선택하세요.';return;}go(false);});document.querySelector('#column-filter-clear').addEventListener('click',()=>go(true));
})();
