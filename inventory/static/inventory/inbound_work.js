(()=>{
 const root=document.querySelector('#shared-inbound');if(!root)return;
 let revision=Number(root.dataset.revision),inflight=0,dirty=false;
 const global=document.querySelector('#work-save-status');
 const confirm=document.querySelector('#work-confirm');
 for(const form of root.querySelectorAll('.work-row')){
  let chain=Promise.resolve();const status=form.querySelector('.row-status');
  form.querySelector('[name=location]').addEventListener('input',()=>{form.querySelector('[name=placed]').checked=false;form.dataset.unsaved='yes';dirty=true;confirm?.querySelector('button')?.setAttribute('disabled','');status.textContent='저장 전';});
  const save=()=>{
   const values={id:form.row_id.value,location:form.location.value,placed:form.placed.checked};
   form.dataset.unsaved='yes';dirty=true;confirm?.querySelector('button')?.setAttribute('disabled','');
   chain=chain.then(async()=>{
    if(form.dataset.error){status.textContent='저장 실패 · 입력 내용을 확인하고 새로 조회하세요.';return;}
    inflight++;form.dataset.saving='yes';status.textContent='저장 중…';
    try{
     const response=await fetch(root.dataset.saveUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify([{...values,version:form.version.value}])});
     if(response.redirected)throw Error('로그인이 만료됐습니다. 입력 내용을 보관하고 다시 로그인하세요.');
     const data=await response.json();if(!response.ok)throw Error(data.error||'저장 실패');
     form.version.value=data.rows[0].version;
     document.querySelector('#work-total').textContent=data.total;document.querySelector('#work-located').textContent=data.located;document.querySelector('#work-done').textContent=data.done;document.querySelector('#work-progress').textContent=Math.round(data.done*100/data.total);
     if(data.revision!==revision+1)global.textContent='다른 기록도 변경됐습니다. 전체 새로 조회 후 검토하세요.';
     revision=Math.max(revision,data.revision);status.textContent='저장 완료';
     if(form.location.value===values.location&&form.placed.checked===values.placed)delete form.dataset.unsaved;
     const tr=form.closest('tr');tr.querySelector('.row-actor').textContent=data.rows[0].updated_by;tr.querySelector('.row-time').textContent=new Date(data.rows[0].updated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
    }catch(e){form.dataset.error='yes';status.textContent=e.message;}
    finally{delete form.dataset.saving;inflight--;}
   });
  };
  form.addEventListener('change',save);
  form.addEventListener('submit',e=>{e.preventDefault();save();},true);
 }
 document.addEventListener('submit',e=>{
  if((e.target.id==='work-bulk'||e.target.id==='work-confirm')&&(dirty||inflight)){e.preventDefault();e.stopImmediatePropagation();alert('편집한 기록을 저장한 후 전체 새로 조회하여 검토하세요.');}
 },true);
 addEventListener('beforeunload',e=>{if(inflight||root.querySelector('[data-error]')||root.querySelector('[data-unsaved]')){e.preventDefault();e.returnValue='';}});
})();
