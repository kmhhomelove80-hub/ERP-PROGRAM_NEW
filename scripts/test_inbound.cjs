const assert=require('node:assert/strict'),I=require('../inventory/static/inventory/inbound_core.js');
const grid=[['품목코드','품목명','규격','수량','비고'],['001','제품','D값','64','32+32'],['품목별 합계','','','64'],['001','동일코드','다른규격','0.3','0.1+0.2'],['전체 합계','','','64.3']];
let rows=I.parseGrid(grid).rows;assert.equal(rows.length,2);assert.deepEqual(I.output(rows).map(r=>r.qty),[32,32,.1,.2]);assert.equal(I.output(rows)[0].total,64);
assert.deepEqual(I.applyLocations(rows,{'001':'A01'}),{applied:4,protectedRows:0});rows[0].remarks[1]='';rows[0].manualParts[1]=true;I.applyLocations(rows,{'001':'B01'});assert.equal(I.output(rows)[1].remark,'');assert.equal(I.output(rows)[0].remark,'B01');
rows[0].removedParts=[0];assert.equal(I.output(rows)[0].total,32);assert.equal(I.sum(I.output(rows).map(r=>r.qty)),32.3);
let clip=I.clipboard(rows);assert.equal(clip.text.split('\r\n')[0],'001\t제품\tD값\t32\t');assert.equal((clip.html.match(/<td/g)||[]).length,15);
rows[0].split='31+32';assert.throws(()=>I.clipboard(rows),/분할 합계/);
const raw=grid.map(r=>Array(9).fill('').concat(r));raw[0][0]='check code1';raw[1][7]='16+48';assert.equal(I.parseGrid(raw).rows[0].split,'16+48');
console.log('입고 변환·정확한 분할·삭제 합계·비고 보호·5열 복사 검사 완료');
