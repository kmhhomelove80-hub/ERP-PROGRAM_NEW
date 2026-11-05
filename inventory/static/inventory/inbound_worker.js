/* Parsing runs outside the DOM; no uploaded file leaves this worker/browser. */
importScripts('vendor/xlsx.full.min.js');
self.onmessage=event=>{
 try {
  const bytes=new Uint8Array(event.data);
  let workbook;
  const options={sheetRows:10010,cellDates:false,cellHTML:false,bookVBA:false};
  if((bytes[0]===0x50&&bytes[1]===0x4b)||(bytes[0]===0xd0&&bytes[1]===0xcf))workbook=XLSX.read(bytes,{...options,type:'array'});
  else {
   let text;
   try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{text=new TextDecoder('euc-kr',{fatal:true}).decode(bytes);}
   if(text.includes('\0'))throw Error('지원하지 않는 파일 형식입니다.');
   workbook=XLSX.read(text,{...options,type:'string',raw:true});
  }
  const name=workbook.SheetNames.find(n=>n.trim().toUpperCase()==='RAW')||workbook.SheetNames[0];
  if(!name)throw Error('시트가 없습니다.');
  const sheet=workbook.Sheets[name];
  const bounds=XLSX.utils.decode_range(sheet['!fullref']||sheet['!ref']||'A1');
  if(bounds.e.r>=10010||bounds.e.c>=100)throw Error('최대 10,000행·100열 자료를 사용하세요.');
  const grid=XLSX.utils.sheet_to_json(sheet,{header:1,defval:'',raw:false});
  self.postMessage({grid: grid.map(row=>row.map(v=>v==null?'':String(v))),sheet:name});
 }catch(error){self.postMessage({error:'파일을 읽지 못했습니다: '+error.message});}
};
