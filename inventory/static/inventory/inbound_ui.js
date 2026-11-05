(() => {
'use strict';
const root=document.getElementById('inbound-tool');if(!root||!document.getElementById('inbound-file'))return;
const $=id=>document.getElementById('inbound-'+id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>n.toLocaleString('ko-KR',{maximumFractionDigits:12});
let rows=[],undoRows=null,invalid=true,revision=0,controller=null,worker=null,fileSerial=0,choices={},candidates={};
$('date').value=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
const title=()=>$('date').value.replaceAll('-','. ')+'. 입고 LIST';
function tab(preview){$('editor').hidden=preview;$('preview').hidden=!preview;$('editTab').classList.toggle('active',!preview);$('previewTab').classList.toggle('active',preview);}
function editor(){
 $('editBody').innerHTML=rows.map((r,i)=>`<tr><td class="selectCol"><input type="checkbox" class="rowSelect" data-group="${i}" aria-label="${esc(r.code)} 원본 선택"></td><td><b>${esc(r.code)}</b><div>${esc(r.name)}</div><small>${esc(r.spec)}</small></td><td><small>${esc(r.date)} · ${esc(r.from)} → ${esc(r.to)}<br>ERP 적요: ${esc(r.note)}<br>ERP 비고: ${esc(r.erpRemark)}${r.helper?'<br>RAW 비고: '+esc(r.helper):''}</small></td><td class="num">${fmt(r.qty)}</td><td><input class="split" aria-label="${esc(r.code)} 분할 수량" data-i="${i}" data-key="split" value="${esc(r.split)}" placeholder="예: 32+32"></td><td><input class="remark" aria-label="${esc(r.code)} 기본 최종 비고" data-i="${i}" data-key="remark" value="${esc(r.remark)}" placeholder="로케이션"></td></tr>`).join('');
 $('selectAllEdit').checked=false;
}
function update(){
 let errors=[];for(const [i,r] of rows.entries())try{Inbound.parts(r);}catch(e){errors.push(`${i+1}번째 상세 행 (${r.code}): ${e.message}`);}
 if(!$('date').value)errors.push('문서 날짜를 선택하세요.');
 let out=[];if(!errors.length)try{out=Inbound.output(rows);}catch(e){errors.push(e.message);}
 invalid=!!errors.length||!out.length;root.dataset.invalid=String(invalid);
 for(const id of ['copyERP','download','print','receive'])$(id).disabled=invalid;
 $('msg').textContent=errors.join('\n');
 root.querySelectorAll('.split').forEach(e=>{try{Inbound.parts(rows[+e.dataset.i]);e.classList.remove('bad');e.removeAttribute('aria-invalid');}catch{e.classList.add('bad');e.setAttribute('aria-invalid','true');}});
 $('itemCount').textContent=new Set(out.map(r=>r.code)).size;$('rowCount').textContent=out.length;$('totalQty').textContent=errors.length?'—':fmt(Inbound.sum(out.map(r=>r.qty)));
 $('printTitle').textContent=title();$('preview').classList.toggle('hideTotal',$('hideTotal').checked);
 $('previewBody').querySelectorAll('tbody').forEach(e=>e.remove());$('selectAllPreview').checked=false;
 if(!out.length)return;
 let html='',group=-1;
 for(const r of out){
  if(r.group!==group){if(group!==-1)html+='</tbody>';html+='<tbody class="preview-group">';group=r.group;}
  html+=`<tr><td class="selectCol"><input type="checkbox" class="rowSelect" data-group="${r.group}" data-part="${r.part}" aria-label="${esc(r.code)} ${r.part+1}행 선택"></td><td class="codeCell">${esc(r.code)}</td><td>${esc(r.name)}</td><td class="specCell">${esc(r.spec)}</td><td class="num">${fmt(r.qty)}</td>${r.first?`<td class="num total" rowspan="${r.span}">${fmt(r.total)}</td>`:''}<td><input class="rowNote" aria-label="${esc(r.code)} ${r.part+1}행 비고" data-group="${r.group}" data-part="${r.part}" value="${esc(r.remark)}"><span class="printNote">${esc(r.remark)}</span></td></tr>`;
 }
 html+='</tbody>';const total=fmt(Inbound.sum(out.map(r=>r.qty)));
 html+=`<tbody class="summary"><tr><td class="selectCol"></td><td colspan="2">합계</td><td class="specCell"></td><td class="num">${total}</td><td class="num total">${total}</td><td>${out.length}행 · ${new Set(out.map(r=>r.code)).size}품목</td></tr></tbody>`;
 $('previewBody').insertAdjacentHTML('beforeend',html);
}
function locationTable(){
 const unique=[...new Map(rows.map(r=>[r.code,r.name])).entries()];
 $('locationBody').innerHTML=unique.map(([code,name])=>{
  const options=candidates[code]||[];
  return `<tr><td>${esc(code)}</td><td>${esc(name)}</td><td><select data-code="${esc(code)}" aria-label="${esc(code)} 추천 위치"><option value="">기존 위치 없음 / 직접 지정</option>${options.map(r=>`<option value="${esc(r.location)}" ${choices[code]===r.location?'selected':''}>${esc(r.location)} · ${fmt(r.quantity)}개</option>`).join('')}</select></td><td><input maxlength="150" data-manual-code="${esc(code)}" aria-label="${esc(code)} 직접 지정 위치" value="${esc(options.some(r=>r.location===choices[code])?'':choices[code]||'')}" placeholder="코드별로 한 번 입력"></td></tr>`;
 }).join('');
}
async function lookup(myRevision){
 if(controller)controller.abort();controller=new AbortController();const codes=[...new Set(rows.map(r=>r.code))];
 $('locationStatus').textContent='기존 위치를 확인하고 있습니다…';
 try{
  for(let i=0;i<codes.length;i+=250){
   const response=await fetch(root.dataset.locationsUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':root.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({codes:codes.slice(i,i+250)}),signal:controller.signal});
   if(!response.ok||response.redirected)throw Error('로그인과 입고 관리 권한을 확인하세요.');
   const data=await response.json();if(myRevision!==revision)return;
   Object.assign(candidates,data.locations);
  }
  if(myRevision!==revision)return;
  for(const code of codes)if(!(code in choices)&&candidates[code]?.length)choices[code]=candidates[code][0].location;
  locationTable();$('locationStatus').textContent=`${codes.length}개 코드 확인 완료 · 추천 위치 일괄 적용을 누르세요. 직접 작성한 비고는 보호합니다.`;
 }catch(e){if(e.name!=='AbortError'&&myRevision===revision)$('locationStatus').textContent='위치를 불러오지 못했습니다. 코드별 직접 지정은 가능합니다. '+e.message;}
}
function load(grid,name){
 const parsed=Inbound.parseGrid(grid);revision++;rows=parsed.rows;undoRows=null;choices={};candidates={};
 $('workspace').hidden=false;$('filename').textContent=name;$('status').textContent=`상세 ${rows.length}행 불러옴 · 합계 등 ${parsed.skipped}행 제외`;
 $('undoDelete').disabled=true;editor();locationTable();tab(false);update();lookup(revision);
}
$('locationBody').addEventListener('change',e=>{if(e.target.dataset.code!==undefined)choices[e.target.dataset.code]=e.target.value;});
$('locationBody').addEventListener('input',e=>{if(e.target.dataset.manualCode!==undefined)choices[e.target.dataset.manualCode]=e.target.value.trim();});
$('applyLocations').onclick=()=>{try{const result=Inbound.applyLocations(rows,choices);update();$('copyStatus').textContent=`${result.applied}행 위치 적용 · 직접 작성한 ${result.protectedRows}행 비고 보호`;}catch(e){$('msg').textContent=e.message;}};
$('editBody').addEventListener('input',e=>{
 if(!e.target.dataset.key)return;const r=rows[+e.target.dataset.i],key=e.target.dataset.key;r[key]=e.target.value;
 if(key==='split'){r.removedParts=[];r.remarks=[];r.manualParts=[];}
 else{r.remarkManual=true;r.remarks=r.remarks.map((v,i)=>r.manualParts[i]?v:undefined);}
 update();
});
$('previewBody').addEventListener('input',e=>{
 const t=e.target;if(!t.classList.contains('rowNote'))return;
 const r=rows[+t.dataset.group],part=+t.dataset.part;r.remarks[part]=t.value;r.manualParts[part]=true;t.nextElementSibling.textContent=t.value;
});
$('editTab').onclick=()=>tab(false);$('previewTab').onclick=()=>tab(true);$('date').onchange=update;$('hideTotal').onchange=update;
for(const [id,container] of [['selectAllEdit','editBody'],['selectAllPreview','previewBody']])$(id).onchange=e=>$(container).querySelectorAll('.rowSelect').forEach(b=>b.checked=e.target.checked);
$('deleteSelected').onclick=()=>{
 const preview=!$('preview').hidden,selected=[...$(preview?'previewBody':'editBody').querySelectorAll('.rowSelect:checked')];
 if(!selected.length){$('copyStatus').textContent='삭제할 행을 체크하세요.';return;}
 undoRows=structuredClone(rows);
 if(preview){for(const e of selected){const r=rows[+e.dataset.group];r.removedParts=[...new Set([...(r.removedParts||[]),+e.dataset.part])];}rows=rows.filter(r=>r.removedParts?.length!==Inbound.parts(r).length);}
 else{const groups=new Set(selected.map(e=>+e.dataset.group));rows=rows.filter((r,i)=>!groups.has(i));}
 editor();locationTable();update();$('undoDelete').disabled=false;$('copyStatus').textContent=selected.length+'행 삭제 · 삭제 취소로 직전 상태를 복구할 수 있습니다.';
};
$('undoDelete').onclick=()=>{if(!undoRows)return;rows=undoRows;undoRows=null;editor();locationTable();update();$('undoDelete').disabled=true;};
function legacyCopy(data){
 const handler=e=>{e.preventDefault();e.clipboardData.setData('text/plain',data.text);e.clipboardData.setData('text/html',data.html);};
 const ta=document.createElement('textarea');ta.value=data.text;ta.style.cssText='position:fixed;opacity:0';document.body.appendChild(ta);ta.select();document.addEventListener('copy',handler);let ok=false;
 try{ok=document.execCommand('copy');}finally{document.removeEventListener('copy',handler);ta.remove();}
 if(!ok)throw Error('브라우저에서 복사를 허용하지 않습니다. 엑셀을 내려받아 셀을 복사하세요.');
}
$('receive').onclick=()=>{
 update();if(invalid)return;
 try{
  const cells=Inbound.erpCells(rows);
  if(cells.length>1000)throw Error('입고 화면으로는 한 번에 1,000행까지 보낼 수 있습니다.');
  if(cells.some(r=>!String(r[4]??'').trim()))throw Error('모든 행의 최종 비고에 로케이션을 입력하세요.');
  if(cells.some(r=>!Number.isSafeInteger(r[3])||r[3]<=0||r[3]>2147483647))throw Error('선일 ERP 입고 수량은 1 이상의 정수여야 합니다. 소수 수량은 확인 후 수정하세요.');
  const form=document.createElement('form');form.method='post';form.action=root.dataset.receiveUrl;
  const fields={action:'handoff',inbound_nonce:root.dataset.receiveNonce,inbound_revision:String(revision),inbound_rows:JSON.stringify(cells),csrfmiddlewaretoken:root.querySelector('[name=csrfmiddlewaretoken]').value};
  for(const [key,value] of Object.entries(fields)){const input=document.createElement('input');input.type='hidden';input.name=key;input.value=value;form.append(input);}
  $('receive').disabled=true;$('copyStatus').textContent='입고 화면으로 전달 중입니다. 아직 재고는 변경하지 않습니다.';document.body.append(form);form.requestSubmit();
 }catch(e){$('copyStatus').textContent='입고 전달 중단: '+e.message;}
};
window.addEventListener('pageshow',()=>{$('receive').disabled=invalid;});
$('copyERP').onclick=async()=>{
 update();if(invalid)return;
 try{const data=Inbound.clipboard(rows);try{
  if(!navigator.clipboard?.write||typeof ClipboardItem==='undefined')throw Error('fallback');
  await navigator.clipboard.write([new ClipboardItem({'text/plain':new Blob([data.text],{type:'text/plain'}),'text/html':new Blob([data.html],{type:'text/html'})})]);
 }catch{legacyCopy(data);}
 $('copyStatus').textContent=data.count+'행 복사 완료 · 품목코드 / 품목명 / 규격 / 수량 / 최종 비고 순서입니다.';
 }catch(e){$('copyStatus').textContent='복사 실패: '+e.message;}
};
async function openFile(file){
 if(!file)return;if(rows.length&&!confirm('새 파일로 현재 편집 내용을 바꿀까요?'))return;
 const fileVersion=++fileSerial;
 if(worker)worker.terminate();
 try{
  if(file.size>20*1024*1024)throw Error('20MB 이하 파일을 선택하세요.');
  const bytes=await file.arrayBuffer();if(fileVersion!==fileSerial)return;$('status').textContent='파일을 읽고 있습니다…';
  await new Promise((resolve,reject)=>{
   const task=worker=new Worker(root.dataset.workerUrl),timeout=setTimeout(()=>{task.terminate();reject(Error('읽기 제한 시간을 초과했습니다. 파일 크기와 형식을 확인하세요.'));},30000);
   task.onerror=()=>{clearTimeout(timeout);task.terminate();reject(Error('파일 판독 도구를 실행하지 못했습니다.'));};
   task.onmessage=event=>{clearTimeout(timeout);task.terminate();if(event.data.error)reject(Error(event.data.error));else{try{if(fileVersion!==fileSerial){resolve();return;}load(event.data.grid,file.name+' · '+event.data.sheet);resolve();}catch(e){reject(e);}}};
   task.postMessage(bytes,[bytes]);
  });
 }catch(e){if(fileVersion===fileSerial)$('status').textContent='불러오기 실패: '+e.message;}finally{if(fileVersion===fileSerial)$('file').value='';}
}
$('file').onchange=e=>openFile(e.target.files[0]);$('drop').ondragover=e=>e.preventDefault();$('drop').ondrop=e=>{e.preventDefault();openFile(e.dataTransfer.files[0]);};
$('sample').onclick=()=>{if(rows.length&&!confirm('현재 자료를 가상 예제로 바꿀까요?'))return;load([['품목코드','품목명','규격','수량','적요','비고'],['DEMO-001','가상 조명','20W',64,'ERP 참고 적요','32+32'],['품목별 합계','','',64],['DEMO-002','가상 부품','50W',12,'참고 정보',''],['전체 합계','','',76]],'가상 ERP 예제');};
$('print').onclick=()=>{update();if(invalid)return;tab(true);window.print();};
window.addEventListener('beforeprint',update);
$('download').onclick=()=>{
 update();if(invalid)return;
 try{
  const out=Inbound.output(rows),data=[[title()],['품목코드','품목명','규격','수량','총수량','비고']],merges=[{s:{r:0,c:0},e:{r:0,c:5}}];
  out.forEach((r,i)=>{data.push([r.code,r.name,r.spec,r.qty,r.first?r.total:null,r.remark]);if(r.first&&r.span>1)merges.push({s:{r:i+2,c:4},e:{r:i+1+r.span,c:4}});});
  const sheet=XLSX.utils.aoa_to_sheet(data);sheet['!merges']=merges;sheet['!cols']=[{wch:24},{wch:45},{wch:60},{wch:12},{wch:12,hidden:$('hideTotal').checked},{wch:40}];
  const book=XLSX.utils.book_new();XLSX.utils.book_append_sheet(book,sheet,'입고LIST');XLSX.writeFile(book,'입고LIST_'+$('date').value+'.xlsx');
 }catch(e){$('msg').textContent=e.message;}
};
})();
