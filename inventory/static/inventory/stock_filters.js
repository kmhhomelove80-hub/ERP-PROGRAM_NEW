(()=>{
 const dialog=document.querySelector('#stock-column-dialog');if(!dialog)return;
 const form=document.querySelector('#stock-column-form'),values=document.querySelector('#stock-column-values'),search=document.querySelector('#stock-option-search'),more=document.querySelector('#stock-option-more'),status=document.querySelector('#stock-column-status');
 const numeric=new Set(['quantity','allocated','available','pending']);let field='',selected=new Set(),offset=0,controller,serial=0,timer;
 function current(){return JSON.parse(document.querySelector('#stock-filter-data').textContent);}
 function mark(){const data=current();for(const button of document.querySelectorAll('.stock-column-filter')){const k=button.dataset.field;button.classList.toggle('active',!!(data['f_'+k]||data['x_'+k]||data['min_'+k]||data['max_'+k]));}document.querySelector('#stock-column-summary').hidden=!Object.keys(data).length;}
 mark();document.addEventListener('stock-results-updated',mark);
 document.addEventListener('click',e=>{const button=e.target.closest('.stock-column-filter');if(!button)return;
  field=button.dataset.field;const data=current();selected=new Set(data['f_'+field]||[]);form.elements.contains.value=data['x_'+field]||'';form.elements.minimum.value=data['min_'+field]||'';form.elements.maximum.value=data['max_'+field]||'';
  document.querySelector('#stock-column-title').textContent=button.textContent.replace('▾','')+' 필터';document.querySelector('#stock-column-range').hidden=!numeric.has(field);search.value='';dialog.showModal();load(true);
 });
 async function load(reset){const mine=++serial;controller?.abort();controller=new AbortController();status.textContent='전체 검색 결과에서 조회 중…';more.hidden=true;if(reset){values.replaceChildren();offset=0;}
  try{const target=new URL(dialog.dataset.optionsUrl,location.origin);target.search=location.search;target.searchParams.set('field',field);target.searchParams.set('search',search.value);target.searchParams.set('offset',offset);target.searchParams.delete('page');
   const response=await fetch(target,{signal:controller.signal});if(!response.ok||response.redirected)throw Error('조회에 실패했습니다. 로그인 상태와 필터를 확인하세요.');const data=await response.json();if(mine!==serial)return;
   for(const item of data.values){const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=item.value;box.checked=selected.has(item.value);box.addEventListener('change',()=>box.checked?selected.add(box.value):selected.delete(box.value));label.append(box,document.createTextNode(item.label+' ('+item.count+'행)'));values.append(label);}
   offset=data.next_offset;more.hidden=offset===null;status.textContent='여러 값을 선택하거나 포함 조건·수량 범위를 지정하세요. 미선택은 값 제한 없음입니다.';
  }catch(e){if(e.name!=='AbortError'&&mine===serial)status.textContent=e.message;}
 }
 search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>load(true),250);});more.addEventListener('click',()=>load(false));dialog.addEventListener('close',()=>{clearTimeout(timer);serial++;controller?.abort();});
 function go(clear){const next=new URL(location.href);for(const prefix of ['f_','x_','min_','max_'])next.searchParams.delete(prefix+field);next.searchParams.delete('page');next.searchParams.delete('partial');
  if(!clear){for(const v of selected)next.searchParams.append('f_'+field,v);if(form.elements.contains.value.trim())next.searchParams.set('x_'+field,form.elements.contains.value.trim());if(numeric.has(field)){if(form.elements.minimum.value)next.searchParams.set('min_'+field,form.elements.minimum.value);if(form.elements.maximum.value)next.searchParams.set('max_'+field,form.elements.maximum.value);}}
  location.assign(next);
 }
 form.addEventListener('submit',e=>{e.preventDefault();if(selected.size>200){status.textContent='열당 200개 이하를 선택하세요.';return;}go(false);});document.querySelector('#stock-column-clear').addEventListener('click',()=>go(true));
})();
