const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const html = fs.readFileSync(path.join(__dirname, '../inventory/templates/inventory/delivery_receiver.html'), 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const key = 'sunil.delivery.forward.v1';
function source() {
  return {version: 1, issued_at: Math.floor(Date.now()/1000), transfer_id: '062acb863bd7489c96355d8a251099f1',
    project: '연동 시험', rows: [{line_id: 0, values: ['P1', 'H1', 'B1', '시험 품목', 6, 'CODE', 10]},
      {line_id: 1, values: ['P2', 'H1', 'B2', '시험 품목', 4, 'CODE', 10]}]};
}
async function receive(draft, {login = true, storage = new Map(), existing = '', accept = true, hash} = {}) {
  const fields = Object.fromEntries(['customer_rows','pasted_rows','bridge_token','request_id','project'].map(k => ['id_'+k, {value:'', dispatchEvent(){}}]));
  fields.id_customer_rows.value = existing;
  const notices = [];
  const location = {pathname:'/picking/new/', search:'', hash: hash ?? (draft ? '#sunil_forward='+zlib.gzipSync(JSON.stringify(draft)).toString('base64url') : '')};
  const context = {atob, Blob, DecompressionStream, Uint8Array, console, location,
    history: {replaceState(){location.hash='';}}, confirm:()=>accept, Event:class {},
    sessionStorage: {getItem:k=>storage.get(k)??null, setItem:(k,v)=>storage.set(k,v), removeItem:k=>storage.delete(k)},
    document: {getElementById:id=>login ? fields[id]??null : null, createElement:()=>({}), querySelector:()=>({prepend:p=>notices.push(p.textContent)})}};
  await vm.runInNewContext(script, context);
  return {fields, notices, storage, location};
}
test('실제 압축 전송 자료가 탭과 줄바꿈으로 입력된다', async()=>{
  const draft = source(); const result = await receive(draft);
  assert.equal(result.fields.id_customer_rows.value, draft.rows.map(r=>r.values.join('\t')).join('\n'));
  assert.deepEqual(JSON.parse(result.fields.id_bridge_token.value), draft);
  assert.equal(result.fields.id_request_id.value, draft.transfer_id);
  assert.equal(result.location.hash, ''); assert.equal(result.storage.size, 0);
});
test('로그인 전 자료를 보관하고 로그인 후 한 번만 복원한다', async()=>{
  const draft = source();const storage = new Map();
  await receive(draft, {login:false, storage}); assert.ok(storage.has(key));
  const result = await receive(null, {storage});
  assert.equal(result.fields.id_request_id.value, draft.transfer_id); assert.equal(storage.size,0);
  const repeated = await receive(null, {storage});assert.equal(repeated.fields.id_customer_rows.value,'');
});
test('기존 입력 교체를 거절하면 기존 자료를 보존한다', async()=>{
  const result = await receive(source(), {existing:'기존 자료', accept:false});
  assert.equal(result.fields.id_customer_rows.value,'기존 자료');assert.equal(result.fields.id_bridge_token.value,'');
  assert.equal(result.storage.size,0);
});
test('손상되거나 만료된 전송은 입력하지 않는다', async()=>{
  for (const options of [{hash:'#sunil_forward=broken'}, {}]) {
    const draft=source();draft.issued_at-=86401;
    const result=await receive(draft, options);
    assert.equal(result.fields.id_customer_rows.value,'');assert.equal(result.fields.id_bridge_token.value,'');
    assert.ok(result.notices.some(n=>n.includes('받지 못했습니다')));
  }
});
