/* Adapted from ERP-for-SUNIL inbound-list, 8ba5ba2. No server writes. */
(function(root){
'use strict';
const clean=v=>String(v??'').trim();const SCALE=1000000000000n;
function units(value){
 let text=clean(value).replaceAll(',','');
 if(typeof value==='number'&&/e/i.test(text))text=value.toLocaleString('en-US',{useGrouping:false,maximumFractionDigits:12});
 if(!/^\d+(?:\.\d{1,12})?$/.test(text))throw Error('수량은 소수 12자리 이내의 양수여야 합니다.');
 const [integer,fraction='']=text.split('.');const amount=BigInt(integer)*SCALE+BigInt(fraction.padEnd(12,'0'));
 if(amount<=0n||amount>BigInt(Number.MAX_SAFE_INTEGER)*SCALE)throw Error('수량이 허용 범위를 벗어났습니다.');
 return amount;
}
function number(amount){const sign=amount<0n?'-':'';if(amount<0n)amount=-amount;const fraction=(amount%SCALE).toString().padStart(12,'0').replace(/0+$/,'');return Number(sign+(amount/SCALE).toString()+(fraction?'.'+fraction:''));}
function sum(values){return number(values.reduce((s,v)=>s+units(v),0n));}
function parseGrid(grid){
 const h=grid.findIndex(r=>r.some(v=>clean(v)==='품목코드')&&r.some(v=>clean(v)==='수량'));
 if(h<0)throw Error('품목코드·수량 헤더를 찾을 수 없습니다.');
 const raw=grid[h].some(v=>clean(v).toLowerCase()==='check code1'),offset=raw?9:0;
 const headers=grid[h].slice(offset).map(clean),col=n=>headers.indexOf(n);
 for(const n of ['품목코드','품목명','수량'])if(col(n)<0)throw Error(n+' 열이 없습니다.');
 const spec=['규격','D열값','사양'].find(n=>col(n)>=0);if(!spec)throw Error('규격(D열값) 열이 없습니다.');
 const aggregate=v=>/^(?:(?:품목별|품목|전체|총)합계|합계|소계|총계)$/.test(clean(v).replace(/\s/g,''));
 let skipped=0;const rows=[];
 for(let i=h+1;i<grid.length;i++){
  const original=grid[i],cells=original.slice(offset),get=n=>col(n)<0?'':clean(cells[col(n)]),code=get('품목코드');
  if(aggregate(code)||(!code&&cells.slice(0,4).some(aggregate))){skipped++;continue;}
  if(!cells.some(v=>clean(v)))continue;
  if(!code){if(cells.some(v=>clean(v)))throw Error((i+1)+'행 품목코드가 없습니다.');continue;}
  if(code.length>100)throw Error((i+1)+'행 품목코드가 너무 깁니다.');
  let qty;try{qty=number(units(get('수량')));}catch(e){throw Error((i+1)+'행: '+e.message);}
  const erpRemark=get('비고'),helper=raw?clean(original[7]):'',splitSource=helper.includes('+')?helper:erpRemark;
  rows.push({code,name:get('품목명'),spec:get(spec),qty,qtyText:get('수량'),date:get('이동일자'),from:get('이동출고창고'),to:get('이동입고창고'),note:get('적요'),erpRemark,helper,split:splitSource.includes('+')?splitSource:'',remark:'',remarks:[],manualParts:[],remarkManual:false});
  if(rows.length>10000)throw Error('상세 행은 최대 10,000행입니다.');
 }
 if(!rows.length)throw Error('처리할 상세 데이터가 없습니다.');return{rows,skipped};
}
function parts(row){
 if(!row.split.trim())return[row.qty];
 const tokens=row.split.split('+').map(clean);if(tokens.length>1000)throw Error('분할 행이 너무 많습니다.');
 const values=tokens.map(units);
 if(values.reduce((a,b)=>a+b,0n)!==units(row.qtyText??row.qty))throw Error('분할 합계가 원본 수량 '+row.qty+'와 다릅니다.');
 return values.map(number);
}
function output(rows){
 const result=[];
 rows.forEach((row,group)=>{
  const a=parts(row).map((qty,part)=>({qty,part})).filter(x=>!row.removedParts?.includes(x.part));
  const total=sum(a.map(x=>x.qty));
  for(const [j,x] of a.entries())result.push({...row,qty:x.qty,total,first:j===0,group,part:x.part,span:a.length,remark:row.remarks?.[x.part]??row.remark});
 });
 if(result.length>20000)throw Error('출력 상세 행은 최대 20,000행입니다.');return result;
}
function erpCells(rows){return output(rows).map(r=>[r.code,r.name,r.spec,r.qty,r.remark]);}
function clipboard(rows){
 const cells=erpCells(rows);if(!cells.length)throw Error('복사할 행이 없습니다.');
 const text=v=>String(v??'').replace(/[\t\r\n]+/g,' '),escape=v=>text(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 return{text:cells.map(r=>r.map(text).join('\t')).join('\r\n')+'\r\n',html:'<html xmlns:x="urn:schemas-microsoft-com:office:excel"><body><table>'+cells.map(r=>'<tr>'+r.map((v,i)=>'<td'+(i===3?'':' style="mso-number-format:\'\\@\'" x:str')+'>'+escape(v)+'</td>').join('')+'</tr>').join('')+'</table></body></html>',count:cells.length};
}
function applyLocations(rows,choices){
 let applied=0,protectedRows=0;
 for(const row of rows){
  const location=clean(choices[row.code]||'');if(!location)continue;
  parts(row).forEach((_,part)=>{
   if(row.removedParts?.includes(part))return;
   if(row.remarkManual||row.manualParts?.[part]){protectedRows++;return;}
   row.remarks[part]=location;applied++;
  });
 }
 return{applied,protectedRows};
}
root.Inbound={parseGrid,parts,output,erpCells,clipboard,applyLocations,sum};if(typeof module!=='undefined')module.exports=root.Inbound;
})(globalThis);
