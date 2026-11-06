(() => {
 const form=document.querySelector('#allocation-action-form');if(!form)return;
 const all=document.querySelector('#select-page'),status=document.querySelector('#selection-status');let anchor=null;
 const boxes=()=>[...form.querySelectorAll('input[name=allocations]')];
 const total=Number(document.querySelector('#filtered-quantity').dataset.quantity);
 const time=form.querySelector('[name=shipped_at]'),auto=form.querySelector('[name=shipped_at_auto]');
 function refreshTime(){
  if(!auto||!['true','on','1'].includes(auto.value.toLowerCase())||!time)return;
  const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date());
  time.value=parts.replace(' ','T');
 }
 time?.addEventListener('input',()=>{auto.value='';});
 document.addEventListener('allocation-time-refresh',refreshTime);refreshTime();
 function update(){
  const list=boxes(),chosen=list.filter(b=>b.checked),filtered=form.querySelector('[name=scope]:checked')?.value==='filtered';
  document.querySelector('#processing-quantity').textContent=(filtered?total:chosen.reduce((s,b)=>s+Number(b.dataset.quantity),0)).toLocaleString('ko-KR');
  all.checked=list.length>0&&chosen.length===list.length;all.indeterminate=chosen.length>0&&chosen.length<list.length;
  status.textContent=`${chosen.length}행 선택 · ${filtered?'필터 결과 전체 처리':'체크한 행 처리'}`;
 }
 form.addEventListener('click',e=>{
  if(!e.target.matches('[name=allocations]'))return;
  const list=boxes(),index=list.indexOf(e.target);form.querySelector('[name=scope][value=selected]').checked=true;
  if(e.shiftKey&&anchor!==null)for(let i=Math.min(anchor,index);i<=Math.max(anchor,index);i++)list[i].checked=e.target.checked;
  anchor=index;update();
 });
 all.addEventListener('change',()=>{
  // The header means the whole filter, including not-yet-loaded rows.
  form.querySelector('[name=scope][value='+ (all.checked?'filtered':'selected') +']').checked=true;
  boxes().forEach(b=>b.checked=all.checked);anchor=null;update();
 });
 form.addEventListener('change',e=>{if(e.target.name==='scope')update();});
 document.addEventListener('list-appended',()=>{if(form.querySelector('[name=scope]:checked')?.value==='filtered')boxes().forEach(b=>b.checked=true);update();});
 form.addEventListener('submit',()=>{
  const selected=form.querySelector('[name=scope]:checked')?.value==='filtered'?[]:boxes().filter(b=>b.checked).map(b=>b.value);
  let field=form.querySelector('[name=selected_json]');if(!field){field=document.createElement('input');field.type='hidden';field.name='selected_json';form.append(field);}field.value=JSON.stringify(selected);
  // One bounded JSON field avoids Django's 1,000-field limit on long selections.
  boxes().forEach(b=>b.disabled=true);
 });
 document.addEventListener('selection-restored',update);update();
})();
