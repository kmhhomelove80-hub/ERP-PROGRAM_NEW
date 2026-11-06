const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),XLSX=require(path.join(root,'inventory/static/inventory/vendor/xlsx.full.min.js')),I=require(path.join(root,'inventory/static/inventory/inbound_core.js'));
function read(bytes){let output;const self={postMessage:data=>output=data},context={self,TextDecoder,Uint8Array,XLSX,importScripts:()=>{}};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(root,'inventory/static/inventory/inbound_worker.js'),'utf8'),context);self.onmessage({data:bytes});assert.ok(!output.error,output.error);return output.grid;}
for(const kind of ['biff8','xlsx','xlsm']){
 const book=XLSX.utils.book_new(),sheet=XLSX.utils.aoa_to_sheet([['품목코드','품목명','규격','수량','비고'],['FIXTURE','가상 품목','가상 규격',6,'3.+3.']]);sheet.D2.z='0.';XLSX.utils.book_append_sheet(book,sheet,'RAW');
 const grid=read(XLSX.write(book,{type:'array',bookType:kind}));const parsed=I.parseGrid(grid);assert.deepEqual(I.output(parsed.rows).map(r=>r.qty),[3,3]);assert.equal(I.sum(I.output(parsed.rows).map(r=>r.qty)),6);
}
const text='품목코드\t품목명\t규격\t수량\t비고\nFIXTURE\t가상 품목\t가상 규격\t6.\t3.+3.';
assert.equal(I.parseGrid(read(Buffer.from(text))).rows[0].qty,6);
console.log('PASS: XLS/XLSX/XLSM/UTF8 worker 숫자 서식과 분할 회귀 검사');
