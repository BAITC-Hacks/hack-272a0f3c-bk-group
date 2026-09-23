// Run: node scripts/test_agent_ui.mjs. No browser, network, or paid API calls.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script=readFileSync(new URL('../web/assistant.js',import.meta.url),'utf8');
const event={preventDefault(){}};
const configured=(ready=true,settings={})=>({providers:[{id:'openai',name:'OpenAI',ready}],settings:{ready,model:'gpt-5.4-mini',default_model:'gpt-5.4-mini',storage_available:false,persistent:false,...settings}});
const proposed=(id='proposal-1',extra={})=>({proposal_id:id,type:'document',status:'pending',title:'Заказ покупателя',details:{kind:'sales_order',date:'2026-09-23',counterparty_name:'Покупатель',warehouse_name:'Основной склад',lines:[{name:'Лампа',sku:'L1',unit:'шт',quantity:'2',price:'10.00',amount:'20.00'}],amount:'20.00'},effect:'Будет сохранён черновик.',warnings:[],expires_at:new Date(Date.now()+600000).toISOString(),...extra});

function fixture(){
  const nodes=new Map(),all=[],htmlWrites=[],timers=new Map();let timerId=0;
  class Element {
    constructor(tag='div'){
      this.tagName=tag.toUpperCase();this.children=[];this.parent=null;this.dataset={};this.attributes={};this.value='';this.disabled=false;this.checked=false;this.open=false;this.listeners=new Map();this._text='';this._class='';all.push(this);
      this.classList={add:name=>this.setClass(name,true),remove:name=>this.setClass(name,false),contains:name=>this._class.split(/\s+/).includes(name),toggle:(name,on)=>this.setClass(name,on??!this.classList.contains(name))};
    }
    setClass(name,on){const values=new Set(this._class.split(/\s+/).filter(Boolean));if(on)values.add(name);else values.delete(name);this._class=[...values].join(' ');}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id||'';}
    set className(value){this._class=value;}get className(){return this._class;}
    set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set innerHTML(value){
      this._html=value;this.children=[];htmlWrites.push(value);
      for(const match of value.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)){
        const attrs=match[2],id=attrs.match(/\bid="([^"]+)"/),prompt=attrs.match(/\bdata-prompt="([^"]+)"/);
        if(!id&&!prompt)continue;const element=new Element(match[1]);if(id)element.id=id[1];if(prompt)element.dataset.prompt=prompt[1];
        element.className=attrs.match(/\bclass="([^"]+)"/)?.[1]||'';element.value=attrs.match(/\bvalue="([^"]*)"/)?.[1]||'';element.type=attrs.match(/\btype="([^"]+)"/)?.[1]||'';element.disabled=/\bdisabled\b/.test(attrs);element.required=/\brequired\b/.test(attrs);this.append(element);
      }
    }
    get innerHTML(){return this._html||'';}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    before(child){child.parent=this.parent;}
    replaceChildren(...children){this.children=[];this._text='';this.append(...children);}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);if(this.id)nodes.delete(this.id);}
    setAttribute(key,value){this.attributes[key]=value;}getAttribute(key){return this.attributes[key];}
    addEventListener(type,callback){if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(callback);}
    dispatch(type){for(const callback of this.listeners.get(type)||[])callback({type,target:this});}
    showModal(){this.open=true;}close(){this.open=false;this.dispatch('close');}
    requestSubmit(){this.lastSubmission=this.onsubmit?.({preventDefault(){}});}
    focus(){}scrollIntoView(){}
    querySelector(selector){return selector==='h1'?new Element('h1'):null;}
  }
  const body=new Element('body'),header=new Element('header'),nav=new Element('nav');nav.className='mode-nav';header.append(nav);body.append(header);
  const workspace=new Element('main');workspace.id='workspace';body.append(workspace);
  const modes=['manual','plan','ai'].map(mode=>{const button=new Element('button');button.dataset.mode=mode;nav.append(button);return button;});
  const document={readyState:'loading',body,createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;},getElementById:id=>nodes.get(id)||null,
    querySelector:selector=>selector.startsWith('#')?nodes.get(selector.slice(1))||null:selector==='.mode-nav'?nav:selector==='header'?header:null,
    querySelectorAll:selector=>selector==='[data-mode]'?modes:selector==='[data-mode="ai"],[data-assistant-entry]'?[modes[2]]:selector==='[data-prompt]'?all.filter(node=>node.dataset.prompt):[]};
  const calls=[],events=new Map();let response=async()=>configured(false);
  const context=vm.createContext({document,crypto:webcrypto,console,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},
    $:selector=>document.querySelector(selector),api:async(path,body,timeout)=>{calls.push({path,body,timeout});return response(path,body,timeout);},
    setTimeout:(callback,delay)=>{timers.set(++timerId,{callback,delay});return timerId;},clearTimeout:id=>timers.delete(id)});
  context.window=context;context.addEventListener=(type,callback)=>{if(!events.has(type))events.set(type,[]);events.get(type).push(callback);};context.dispatchEvent=evt=>{for(const callback of events.get(evt.type)||[])callback(evt);};
  vm.runInContext(script,context,{filename:'web/assistant.js'});
  const read=code=>vm.runInContext(code,context),get=id=>nodes.get(id);
  return {context,read,get,calls,all,htmlWrites,timers,modes,setResponse:fn=>response=fn,ready:settings=>context.applyChatConfiguration(configured(true,settings)),proposal:(value=proposed())=>context.renderAgentProposal(value),entry:id=>read('chatProposals').get(id),submit:()=>get('chat-form').onsubmit(event)};
}

test('manual mode stays available and OpenAI settings are always visible',async()=>{
  const f=fixture();let opened=0;f.context.opsOpen=()=>opened++;
  await f.context.startWorkspace();assert.equal(opened,1);assert.equal(f.read('currentMode'),'manual');assert.equal(f.context.isAssistantReady(),false);
  assert.equal(f.get('assistant-settings-entry').textContent,'Подключить OpenAI');assert.equal(f.get('assistant-settings-entry').classList.contains('hidden'),false);assert.equal(f.modes[2].classList.contains('hidden'),true);
  f.context.applyChatConfiguration({providers:[{id:'nvidia',name:'NVIDIA',ready:true}]});assert.equal(f.context.isAssistantReady(),false);
  f.ready();assert.equal(f.modes[2].classList.contains('hidden'),false);assert.equal(f.get('assistant-settings-entry').textContent,'Настройки AI');
});

test('answers, steps and proposal fields are rendered as text rather than HTML',async()=>{
  const f=fixture();f.ready();const payload='<img src=x onerror="alert(1)">';const before=f.htmlWrites.length;
  f.setResponse(async(path,body)=>({conversation_id:body.conversation_id,answer:payload,steps:[{name:'search',label:payload}],proposals:[proposed('unsafe',{title:payload,details:{description:payload,lines:[{name:payload,sku:payload,quantity:'1',price:'0',amount:'0'}]},warnings:[payload]})]}));
  f.get('chat-question').value='Проверь товар';await f.submit();
  assert.equal(f.htmlWrites.length,before);assert.match(f.get('chat-log').textContent,/<img src=x/);assert.equal(f.all.some(element=>element.tagName==='IMG'),false);
});

test('chat retries retain request and conversation IDs without duplicating user messages',async()=>{
  const f=fixture();f.ready();f.get('chat-question').value='Найди лампу';let attempts=0;
  f.setResponse(async(path,body)=>{assert.equal(path,'/api/agent/chat');if(++attempts===1)throw Error('Network failure');return {conversation_id:body.conversation_id,answer:'Найдена лампа.',proposals:[]};});
  await f.submit();const first=f.calls[0];assert.equal(f.get('chat-question').value,'Найди лампу');assert.equal(f.get('chat-send').disabled,false);
  await f.submit();assert.equal(f.calls[1].body.request_id,first.body.request_id);assert.equal(f.calls[1].body.conversation_id,first.body.conversation_id);assert.equal(first.timeout,150000);
  assert.deepEqual(Object.keys(first.body).sort(),['conversation_id','message','request_id']);assert.equal(f.get('chat-log').children.filter(node=>node.classList.contains('user')).length,1);assert.equal(f.read('chatRetry'),null);
});

test('pending chat blocks duplicate sends and conversation reset',async()=>{
  const f=fixture();f.ready();f.get('chat-question').value='Проверь остатки';let release;f.setResponse((path,body)=>new Promise(resolve=>release=()=>resolve({conversation_id:body.conversation_id,answer:'Готово',proposals:[]})));
  const original=f.read('chatConversationId'),pending=f.submit();await f.submit();assert.equal(f.calls.length,1);assert.equal(f.context.resetChat(),false);assert.equal(f.read('chatConversationId'),original);assert.equal(f.get('chat-clear').disabled,true);
  release();await pending;assert.equal(f.get('chat-clear').disabled,false);
});

test('plain Enter submits through the form and suppresses the newline',async()=>{
  const f=fixture();f.ready();f.get('chat-question').value='Отправить клавишей Enter';let prevented=0;
  f.setResponse(async(path,body)=>({conversation_id:body.conversation_id,answer:'Готово',proposals:[]}));
  f.get('chat-question').onkeydown({key:'Enter',preventDefault(){prevented++;}});
  await f.get('chat-form').lastSubmission;assert.equal(prevented,1);assert.equal(f.calls.length,1);assert.equal(f.calls[0].body.message,'Отправить клавишей Enter');
});

test('Shift+Enter, IME composition and modified Enter keep native keyboard behavior',()=>{
  const f=fixture();f.ready();f.get('chat-question').value='Первая строка';let prevented=0;
  for(const fields of [{shiftKey:true},{isComposing:true},{keyCode:229},{ctrlKey:true},{altKey:true},{metaKey:true},{defaultPrevented:true}]){
    f.get('chat-question').onkeydown({key:'Enter',...fields,preventDefault(){prevented++;}});
  }
  f.get('chat-question').onkeydown({key:'a',preventDefault(){prevented++;}});
  assert.equal(prevented,0);assert.equal(f.calls.length,0);assert.equal(f.get('chat-form').lastSubmission,undefined);
});

test('Enter does not send an empty question or bypass disconnected and busy states',async()=>{
  const f=fixture();let prevented=0;const key={key:'Enter',preventDefault(){prevented++;}};
  f.get('chat-question').value='Нет подключения';f.get('chat-question').onkeydown(key);assert.equal(f.calls.length,0);
  f.ready();f.get('chat-question').value='  \n ';f.get('chat-question').onkeydown(key);assert.equal(f.calls.length,0);assert.equal(prevented,0);
  f.get('chat-question').value='Один запрос';let release;f.setResponse((path,body)=>new Promise(resolve=>release=()=>resolve({conversation_id:body.conversation_id,answer:'Готово',proposals:[]})));
  f.get('chat-question').onkeydown(key);f.get('chat-question').onkeydown(key);assert.equal(f.calls.length,1);assert.equal(prevented,1);assert.equal(f.get('chat-send').disabled,true);
  release();await f.get('chat-form').lastSubmission;
});

test('confirm sends only proposal IDs and one request while saving',async()=>{
  const f=fixture();f.ready();f.proposal();const entry=f.entry('proposal-1');let release;f.setResponse(()=>new Promise(resolve=>release=resolve));
  const pending=entry.confirmButton.onclick();await entry.confirmButton.onclick();assert.equal(f.calls.length,1);assert.equal(entry.confirmButton.disabled,true);assert.equal(entry.declineButton.disabled,true);assert.equal(f.context.resetChat(),false);
  assert.equal(f.calls[0].path,'/api/agent/confirm');assert.deepEqual(Object.keys(f.calls[0].body).sort(),['conversation_id','proposal_id']);
  release({status:'confirmed',type:'document',document:{id:'saved',status:'draft'}});await pending;
  assert.equal(entry.status,'confirmed');assert.equal(entry.confirmButton.disabled,true);assert.match(entry.statusNode.textContent,/Черновик сохранён/);assert(entry.actions.children.some(node=>node.textContent==='Открыть документы ↗'));
});

test('failed confirmation waits for an explicit retry with the same IDs',async()=>{
  const f=fixture();f.ready();f.proposal();const entry=f.entry('proposal-1');f.setResponse(async()=>{throw Error('Connection lost');});
  await entry.confirmButton.onclick();assert.equal(f.calls.length,1);assert.equal(entry.status,'pending');assert.equal(entry.confirmButton.disabled,false);assert.match(entry.errorNode.textContent,/Connection lost/);
  const request=JSON.stringify(f.calls[0].body);f.setResponse(async()=>({status:'confirmed',document:{id:'saved'}}));await entry.confirmButton.onclick();assert.equal(JSON.stringify(f.calls[1].body),request);assert.equal(entry.status,'confirmed');
});

test('decline creates no document and expired proposals cannot be confirmed',async()=>{
  const f=fixture();f.ready();f.proposal();f.setResponse(async()=>({status:'declined'}));const entry=f.entry('proposal-1');await entry.declineButton.onclick();assert.equal(entry.status,'declined');assert.equal(f.calls[0].path,'/api/agent/decline');
  f.proposal(proposed('expired',{expires_at:'2020-01-01T00:00:00Z'}));const expired=f.entry('expired');await expired.confirmButton.onclick();assert.equal(f.calls.length,1);assert.equal(expired.confirmButton.disabled,true);assert.match(expired.statusNode.textContent,/Срок предложения истёк/);
});

test('stale catalog and ended-session errors disable the old approval',async()=>{
  for(const message of ['Справочник изменился после подготовки предложения.','Сервер перезапущен.','Диалог завершён.','Предложение недоступно.']){
    const f=fixture();f.ready();f.proposal();const entry=f.entry('proposal-1');f.setResponse(async()=>{throw Error(message);});
    await entry.confirmButton.onclick();assert.equal(entry.status,'stale');assert.equal(entry.confirmButton.disabled,true);assert.equal(entry.declineButton.disabled,true);assert.equal(entry.errorNode.textContent,message);
    await entry.confirmButton.onclick();assert.equal(f.calls.length,1);
  }
});

test('new conversation removes old approvals and invalidates retained callbacks',async()=>{
  const f=fixture();f.ready();f.proposal();const old=f.entry('proposal-1'),conversation=f.read('chatConversationId');assert.equal(f.context.resetChat(),true);
  assert.notEqual(f.read('chatConversationId'),conversation);assert.equal(f.read('chatProposals').size,0);await old.confirmButton.onclick();assert.equal(f.calls.length,0);assert.equal(f.get('chat-log').children.length,1);
});

for(const success of [true,false])test(`settings key clears after ${success?'successful':'failed'} save without provider verification claims`,async()=>{
  const f=fixture();f.get('assistant-api-key').value='sk-test-ui-only';f.get('assistant-model').value='gpt-5.4-mini';f.get('assistant-persist').checked=true;
  f.setResponse(async(path,body)=>{assert.equal(path,'/api/assistant/configure');assert.equal(body.api_key,'sk-test-ui-only');assert.equal(body.persist,false);if(!success)throw Error('Save failed');return configured(true);});
  await f.get('assistant-settings-form').onsubmit(event);assert.equal(f.get('assistant-api-key').value,'');assert.equal(f.get('assistant-settings-save').disabled,false);assert.equal(f.get('chat-log').textContent.includes('sk-test-ui-only'),false);
  if(success){assert.match(f.get('assistant-settings-status').textContent,/ещё не проверен/);assert.equal(f.context.isAssistantReady(),true);}else assert.match(f.get('assistant-settings-error').textContent,/Save failed/);
});

test('closing settings clears a typed key and hides unavailable persistent storage',async()=>{
  const f=fixture();await f.context.openAssistantSettings();f.get('assistant-api-key').value='sk-test-ui-only';assert.equal(f.get('assistant-persist-field').classList.contains('hidden'),true);
  f.context.closeAssistantSettings();assert.equal(f.get('assistant-api-key').value,'');assert.equal(f.get('assistant-settings').open,false);
});

test('connection probe sends no business data and new settings suppress stale config responses',async()=>{
  const f=fixture();f.ready();f.setResponse(async(path,body)=>{assert.equal(path,'/api/assistant/test');assert.equal(Object.keys(body).length,0);return {ok:true,message:'Model available'};});
  await f.get('assistant-test').onclick();assert.equal(f.get('assistant-settings-status').textContent,'Model available');
  let release;f.setResponse(()=>new Promise(resolve=>release=resolve));const pending=f.context.loadChatConfiguration();f.read('chatConfigurationRequest++');f.ready({model:'changed-model'});release(configured(false,{model:'old-model'}));await pending;
  assert.equal(f.read('chatSettings.model'),'changed-model');assert.equal(f.context.isAssistantReady(),true);
});
