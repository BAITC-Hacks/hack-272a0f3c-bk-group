// Run with: node scripts/test_operations_ui.mjs (no child-process isolation needed).
// Executes the shipped UI handlers with controlled API responses and a small DOM.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=readFileSync(new URL('../web/operations.js',import.meta.url),'utf8').replace(
  /\}\)\(\);\s*$/,
  'globalThis.ui={ops,load,onSubmit,onClick,documentEditor,documentDetail,entityEditor,taskRow,lineCents,docCents};})();'
);
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const freshData=()=>({products:[],counterparties:[],warehouses:[],tasks:[],overview:{},stock:[],money:{balance:'0.00',income:'0.00',expense:'0.00'}});
const documentRecord=(extra={})=>({id:'doc',kind:'payment_in',number:'P-1',date:'2026-09-23',status:'draft',version:1,amount:'10.00',lines:[],...extra});

class Element {
  constructor(id=''){
    this.id=id;this.dataset={};this.disabled=false;this.open=false;this.textContent='';this.innerHTML='';this.attributes={};
    const values=new Set();this.classList={add:value=>values.add(value),remove:value=>values.delete(value),contains:value=>values.has(value),toggle:(value,on)=>on?values.add(value):values.delete(value)};
  }
  setAttribute(name,value){this.attributes[name]=value;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  focus(){}
  scrollIntoView(){}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  closest(){return this;}
}

function fixture(){
  const nodes=new Map(['ops-content','ops-title','ops-description','ops-subnav','ops-alert','ops-dialog','ops-form-error'].map(id=>[id,new Element(id)]));
  const refresh=new Element('refresh');const calls=[];
  const document={
    activeElement:null,
    getElementById:id=>nodes.get(id)||null,
    querySelector:selector=>selector==='[data-ops-action=refresh]'?refresh:null,
    querySelectorAll:()=>[]
  };
  const context=vm.createContext({
    window:{},document,console,esc:escape,fmt:String,
    FormData:class {constructor(form){this.values=form.values||{};}[Symbol.iterator](){return Object.entries(this.values)[Symbol.iterator]();}},
    api:async(path,body)=>{calls.push({path,body});return await context.response(path,body);}
  });
  vm.runInContext(source,context,{filename:'web/operations.js'});
  const ui=context.ui;ui.ops.data=freshData();ui.ops.loaded=true;
  context.response=async(path)=>path.endsWith('/bootstrap')?freshData():[];
  const action=(name,extra={})=>Object.assign(new Element(),{dataset:{opsAction:name,...extra}});
  const click=button=>ui.onClick({target:{closest:()=>button}});
  const form=(id,kind,values,dataset={})=>{
    const result=new Element(id),submit=new Element('submit');result.dataset={kind,...dataset};result.values=values;
    result.querySelector=selector=>selector==='[type=submit]'?submit:null;result.submit=submit;
    return result;
  };
  const submit=form=>ui.onSubmit({target:form,preventDefault(){}});
  const failRefresh=(saved)=>{
    context.response=async(path,body)=>{if(body)return saved;if(path.endsWith('/documents'))throw Error('Refresh unavailable');return freshData();};
  };
  return {ui,nodes,refresh,calls,context,action,click,form,submit,failRefresh};
}

function assertSavedWarning(f){
  const message=f.nodes.get('ops-alert').textContent;
  assert.match(message,/Операция сохранена, но не удалось обновить данные/);
  assert.match(message,/Повторно сохранять операцию не нужно/);
  assert.equal(f.nodes.get('ops-alert').attributes.role,'alert');
}

test('load returns false and keeps the whole previous snapshot when the journal fails',async()=>{
  const f=fixture(),oldData=f.ui.ops.data,oldDocs=[documentRecord()],oldJournal=[{id:'old'}];
  Object.assign(f.ui.ops,{tab:'journal',documents:oldDocs,journal:oldJournal});
  f.context.response=async path=>{if(path.endsWith('/journal'))throw Error('Journal unavailable');return path.endsWith('/bootstrap')?{...freshData(),marker:'new'}:[documentRecord({version:2})];};
  assert.equal(await f.ui.load(),false);
  assert.equal(f.ui.ops.data,oldData);assert.equal(f.ui.ops.documents,oldDocs);assert.equal(f.ui.ops.journal,oldJournal);
  assert.equal(f.ui.ops.loading,false);assert.equal(f.refresh.disabled,false);
});

test('load publishes no partial data while the final required request is pending',async()=>{
  const f=fixture(),oldData=f.ui.ops.data,oldDocs=[documentRecord()],newData={...freshData(),marker:'new'},newDocs=[documentRecord({version:2})];
  Object.assign(f.ui.ops,{tab:'journal',documents:oldDocs});let release;
  f.context.response=path=>path.endsWith('/journal')?new Promise(resolve=>release=resolve):path.endsWith('/bootstrap')?newData:newDocs;
  const pending=f.ui.load();await Promise.resolve();await Promise.resolve();
  assert.equal(f.ui.ops.data,oldData);assert.equal(f.ui.ops.documents,oldDocs);
  release([{id:'new-event',created_at:'2026-09-23',action:'update',description:'Saved'}]);
  assert.equal(await pending,true);assert.equal(f.ui.ops.data,newData);assert.equal(f.ui.ops.documents,newDocs);assert.equal(f.ui.ops.journal[0].id,'new-event');
});

test('forced load waits for an in-flight refresh and performs a fresh request',async()=>{
  const f=fixture();let releases=[],bootstrapCalls=0;
  f.context.response=path=>path.endsWith('/bootstrap')?(bootstrapCalls++,new Promise(resolve=>releases.push(resolve))):[];
  const first=f.ui.load(),forced=f.ui.load(true);assert.equal(bootstrapCalls,1);
  releases[0](freshData());assert.equal(await first,true);
  await Promise.resolve();assert.equal(bootstrapCalls,2);
  releases[1](freshData());assert.equal(await forced,true);
});

for(const [id,kind,values,saved] of [
  ['ops-document-form','payment_in',{number:'',date:'2026-09-23',amount:'10.00',description:''},documentRecord()],
  ['ops-entity-form','products',{name:'Lamp',sku:'L1',unit:'шт',price:'10.00'},{id:'p1',name:'Lamp',sku:'L1',unit:'шт',price:'10.00',version:1}]
])test(`${kind}: a successful save followed by a failed refresh cannot submit the same form again`,async()=>{
  const f=fixture(),form=f.form(id,kind,values);f.failRefresh(saved);f.nodes.get('ops-dialog').open=true;
  await f.submit(form);assertSavedWarning(f);
  assert.equal(f.nodes.get('ops-dialog').open,false);assert.equal(form.dataset.saved,'true');assert.equal(form.submit.disabled,true);
  const records=id==='ops-document-form'?f.ui.ops.documents:f.ui.ops.data[kind];assert.equal(records[0].id,saved.id);
  await f.submit(form);assert.equal(f.calls.filter(call=>call.body).length,1);
});

for(const [initialStatus,action,status] of [['draft','post','posted'],['posted','cancel','cancelled']])test(`${action}: keep the confirmed status and close the old action after refresh failure`,async()=>{
  const f=fixture(),saved=documentRecord({status,version:2});f.ui.ops.documents=[documentRecord({status:initialStatus})];
  f.ui.documentDetail('doc');assert.equal(f.nodes.get('ops-dialog').open,true);
  const button=f.action('transition',{id:'doc',transition:action,version:'1'});f.failRefresh(saved);
  await f.click(button);assertSavedWarning(f);
  assert.equal(f.ui.ops.documents[0].status,status);assert.equal(f.ui.ops.documents[0].version,2);
  assert.equal(f.nodes.get('ops-dialog').open,false);assert.equal(button.disabled,true);
  await f.click(button);assert.equal(f.calls.filter(call=>call.body).length,1);
  f.ui.documentDetail('doc');assert.doesNotMatch(f.nodes.get('ops-dialog').innerHTML,/data-transition="post"/);
  if(status==='cancelled')assert.doesNotMatch(f.nodes.get('ops-dialog').innerHTML,/data-transition="cancel"/);
});

test('task save keeps the confirmed task when the following refresh fails',async()=>{
  const f=fixture(),task={id:'task',title:'Call customer',description:'',status:'open',due_date:'',version:1};
  Object.assign(f.ui.ops,{module:'tasks',tab:'tasks'});f.ui.ops.data.tasks=[task];
  const saved={...task,status:'done',version:2};f.failRefresh(saved);
  const button=f.action('toggle-task',{id:'task',version:'1',status:'open'});await f.click(button);
  assertSavedWarning(f);assert.equal(f.ui.ops.data.tasks[0].status,'done');assert.equal(f.ui.ops.data.tasks[0].version,2);
  assert.equal(button.disabled,true);assert.match(f.nodes.get('ops-content').innerHTML,/data-status="done"/);
  await f.click(button);assert.equal(f.calls.filter(call=>call.body).length,1);
});

test('import success followed by failed refresh is reported as saved, without repeat submission',async()=>{
  const f=fixture(),button=f.action('import-report');f.failRefresh({created:4,updated:0,skipped:0});
  await f.click(button);assertSavedWarning(f);assert.equal(button.disabled,true);
  await f.click(button);assert.equal(f.calls.filter(call=>call.body).length,1);
});

test('saved-operation warning survives navigation until a complete refresh succeeds',async()=>{
  const f=fixture();f.failRefresh({created:1,updated:0,skipped:0});
  await f.click(f.action('import-report'));assert.equal(f.ui.ops.stale,true);
  for(const module of ['warehouse','money']){
    await f.click(f.action('',{opsModule:module}));
    assert.equal(f.ui.ops.module,module);assertSavedWarning(f);
    assert.match(f.nodes.get('ops-alert').textContent,/Остатки и денежные итоги могут быть неактуальны/);
  }
  f.context.response=async path=>path.endsWith('/bootstrap')?freshData():[];
  assert.equal(await f.ui.load(),true);assert.equal(f.ui.ops.stale,false);assert.equal(f.nodes.get('ops-alert').textContent,'');
});

test('a rejected document save keeps the form editable and reports the actual error',async()=>{
  const f=fixture(),form=f.form('ops-document-form','payment_in',{date:'2026-09-23',amount:'10.00'});
  f.nodes.get('ops-dialog').open=true;f.context.response=async()=>{throw Error('Validation failed');};
  await f.submit(form);assert.equal(form.submit.disabled,false);assert.equal(form.dataset.saved,undefined);
  assert.equal(f.nodes.get('ops-dialog').open,true);assert.equal(f.nodes.get('ops-form-error').textContent,'Validation failed');
});

test('a successful save and refresh opens the refreshed document detail',async()=>{
  const f=fixture(),saved=documentRecord(),form=f.form('ops-document-form','payment_in',{date:saved.date,amount:saved.amount});
  f.context.response=async(path,body)=>body?saved:path.endsWith('/bootstrap')?freshData():[saved];
  await f.submit(form);assert.equal(f.nodes.get('ops-dialog').open,true);assert.match(f.nodes.get('ops-dialog').innerHTML,/Поступление денег № P-1/);
  assert.equal(f.nodes.get('ops-alert').textContent,'Черновик сохранён. Проведите документ, чтобы применить операцию.');
});

test('entity editing submits the version captured when the form opened',async()=>{
  const f=fixture(),record={id:'p1',name:'Lamp',sku:'L1',unit:'шт',price:'10.00',version:3};f.ui.ops.data.products=[record];
  f.ui.entityEditor('products','p1');const captured=f.nodes.get('ops-dialog').innerHTML.match(/data-version="(\d+)"/)[1];
  f.ui.ops.data.products=[{...record,version:4}];const form=f.form('ops-entity-form','products',{name:'Edited lamp',sku:'L1',unit:'шт',price:'10.00'},{id:'p1',version:captured});
  f.context.response=async()=>{throw Error('Version conflict');};await f.submit(form);
  assert.equal(f.calls[0].body.payload.version,3);assert.equal(form.submit.disabled,false);assert.equal(f.nodes.get('ops-form-error').textContent,'Version conflict');
});

test('task toggle submits the version and state of the displayed task',async()=>{
  const f=fixture(),task={id:'t1',title:'Task',description:'',status:'open',due_date:'',version:5};
  const html=f.ui.taskRow(task),version=html.match(/data-version="(\d+)"/)[1],status=html.match(/data-status="([^\"]+)"/)[1];
  f.ui.ops.data.tasks=[{...task,status:'done',version:6}];f.context.response=async()=>{throw Error('Version conflict');};
  await f.click(f.action('toggle-task',{id:'t1',version,status}));
  assert.equal(f.calls[0].body.payload.version,5);assert.equal(f.calls[0].body.payload.status,'done');
});

for(const kind of ['supplier_return','customer_return','retail_return'])test(`${kind}: editor and detail explain manual returns`,()=>{
  const f=fixture();f.ui.documentEditor(kind);assert.match(f.nodes.get('ops-dialog').innerHTML,/Возврат оформляется вручную/);assert.match(f.nodes.get('ops-dialog').innerHTML,/Сверьте количество, цену и основание/);
  f.ui.ops.documents=[documentRecord({kind})];f.ui.documentDetail('doc');assert.match(f.nodes.get('ops-dialog').innerHTML,/автоматической связи с ним нет/);
});

test('line rounding and production totals remain exact',()=>{
  const f=fixture();assert.equal(f.ui.lineCents({quantity:'0.5',price:'0.01'}),1n);
  assert.equal(f.ui.docCents({kind:'shipment',lines:[{quantity:'0.5',price:'0.01'},{quantity:'0.5',price:'0.01'}]}),2n);
  assert.equal(f.ui.docCents({kind:'production',lines:[{quantity:'3',price:'10',role:'material'},{quantity:'1',price:'50',role:'output'}]}),5000n);
});
