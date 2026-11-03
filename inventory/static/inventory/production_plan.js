'use strict';
(() => {
  const data = JSON.parse(document.getElementById('production-data').textContent);
  const get = id => document.getElementById(id);
  const table = get('plan-table'), detail = get('plan-selected');
  const query = get('plan-query'), scope = get('plan-scope'), schedule = get('plan-schedule');
  const compare = new Intl.Collator('ko', {numeric:true, sensitivity:'base'});
  let filtered = [], page = 0, sortColumn = null, descending = false, selected = null;
  const size = 100;
  const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
  ctx.font = '14px system-ui';
  const widths = [70,160,110,110,65,65,300,170,180,260];
  for (const [col,index] of [[1,0],[8,7]]) {
    widths[col] = Math.ceil(Math.max(widths[col], ...data.map(r => ctx.measureText(r.values[index]).width + 28)));
  }
  for (const target of [table,detail]) {
    const group = target.querySelector('colgroup');
    for (const width of widths) {const c=document.createElement('col');c.style.width=width+'px';group.append(c);}
    target.style.width=widths.reduce((a,b)=>a+b,0)+'px';
  }
  for (const value of [...new Set(data.map(r=>r.values[0]))].sort(compare.compare)) {
    const option=document.createElement('option');option.value=JSON.stringify(value);option.textContent=value || '(일정 없음)';schedule.append(option);
  }
  function cells(row) {
    const tr=document.createElement('tr');
    for (const value of [row.row,...row.values]) {const td=document.createElement('td');td.textContent=value;tr.append(td);}
    return tr;
  }
  function select(row) {
    selected=row;
    detail.tBodies[0].replaceChildren(cells(row));
    [...get('plan-parts').querySelectorAll('dd')].forEach((dd,i)=>dd.textContent=row.parts[i] || '—');
    for (const tr of table.tBodies[0].rows) tr.setAttribute('aria-selected',String(Number(tr.dataset.row)===row.row));
    get('detail-scroll').scrollLeft=get('plan-scroll').scrollLeft;
  }
  function render() {
    const fragment=document.createDocumentFragment();
    for (const row of filtered.slice(page*size,(page+1)*size)) {
      const tr=cells(row);tr.tabIndex=0;tr.dataset.row=row.row;tr.setAttribute('aria-selected',String(selected?.row===row.row));
      tr.addEventListener('click',()=>select(row));
      tr.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(row);}});
      fragment.append(tr);
    }
    table.tBodies[0].replaceChildren(fragment);
    get('plan-count').textContent=`검색 결과 ${filtered.length.toLocaleString()}건 / 전체 ${data.length.toLocaleString()}건`;
    get('plan-page').textContent=`${page+1} / ${Math.max(1,Math.ceil(filtered.length/size))} 페이지`;
    get('plan-prev').disabled=page===0;get('plan-next').disabled=(page+1)*size>=filtered.length;
  }
  function filter() {
    const words=query.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    filtered=data.filter(row=>{
      if(schedule.value && row.values[0]!==JSON.parse(schedule.value)) return false;
      const text=(scope.value===''?row.values.join(' '):row.values[Number(scope.value)]).toLocaleLowerCase();
      return words.every(word=>text.includes(word));
    });
    if(sortColumn!==null) filtered.sort((a,b)=>compare.compare(String(sortColumn===0?a.row:a.values[sortColumn-1]),String(sortColumn===0?b.row:b.values[sortColumn-1]))*(descending?-1:1));
    page=0;
    if(selected && !filtered.some(r=>r.row===selected.row)) {
      selected=null;detail.tBodies[0].replaceChildren();get('plan-parts').querySelectorAll('dd').forEach(dd=>dd.textContent='—');
    }
    render();
  }
  for(const input of [query,scope,schedule]) input.addEventListener(input===query?'input':'change',filter);
  for(const button of table.querySelectorAll('[data-sort]')) button.addEventListener('click',()=>{
    const col=Number(button.dataset.sort);descending=sortColumn===col?!descending:false;sortColumn=col;
    for(const th of table.tHead.rows[0].cells) th.removeAttribute('aria-sort');
    button.parentElement.setAttribute('aria-sort',descending?'descending':'ascending');filter();
  });
  get('plan-reset').addEventListener('click',()=>{query.value='';scope.value='';schedule.value='';filter();});
  get('plan-prev').addEventListener('click',()=>{page--;render();});get('plan-next').addEventListener('click',()=>{page++;render();});
  get('plan-scroll').addEventListener('scroll',()=>get('detail-scroll').scrollLeft=get('plan-scroll').scrollLeft);
  get('plan-export').addEventListener('click',()=>{
    const escape=value=>'"'+String(value).replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
    const rows=[[...table.tHead.rows[0].cells].map(th=>th.textContent.trim()),...filtered.map(r=>[r.row,...r.values])];
    const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(r=>r.map(escape).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='선박등기구_검색결과.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key.toLowerCase()==='f'){e.preventDefault();query.focus();}if(e.ctrlKey&&e.key.toLowerCase()==='o'&&get('production-open')){e.preventDefault();location.href=get('production-open').href;}});
  filter();
})();
