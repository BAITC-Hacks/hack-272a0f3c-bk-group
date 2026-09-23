/* OpenAI agent UI. Business changes require a separately confirmed proposal. */
const assistantView=document.createElement('main');
assistantView.id='assistant-view';assistantView.className='hidden';
assistantView.innerHTML=`<div class="topline"><div><span class="agent-eyebrow">Работа с AI</span><h1 tabindex="-1">Помощник продавца</h1><p class="muted">Находит данные, объясняет решения и готовит действия на проверку.</p></div><div class="actions"><button id="chat-settings" type="button">Настройки AI</button><button id="chat-clear" type="button">Новый диалог</button></div></div>
<section class="panel agent-panel"><div class="agent-toolbar"><span class="agent-provider">OpenAI <span id="chat-model" class="muted"></span></span><button id="chat-data" class="agent-text-button" type="button">Открыть учёт ↗</button></div><div id="chat-connection" class="agent-connection" role="status">Проверяем настройки подключения…</div>
<div id="chat-log" class="chat-log" role="log" aria-label="Диалог с помощником" aria-live="polite"></div>
<div class="chat-hints"><button type="button" data-prompt="Проверь остатки и объясни, какие товары требуют внимания.">Проверить остатки</button><button type="button" data-prompt="Помоги подготовить заказ покупателя. Сначала уточни нужные данные.">Подготовить заказ</button><button type="button" data-prompt="Помоги создать задачу по работе с клиентом. Уточни название и срок.">Создать задачу</button></div>
<form id="chat-form" class="chat-compose"><label for="chat-question">Что нужно сделать?</label><textarea id="chat-question" maxlength="4000" aria-describedby="chat-keyboard-hint" required placeholder="Например: найди товар, проверь остаток и подготовь черновик заказа"></textarea><p id="chat-keyboard-hint" class="agent-help">Enter — отправить · Shift + Enter — новая строка</p><div class="agent-compose-footer"><p class="agent-privacy">При отправке вопроса текст диалога и нужные для ответа данные магазина передаются OpenAI. Документ или задача сохраняются только после вашего подтверждения.</p><button class="primary" id="chat-send" disabled>Отправить</button></div><div id="chat-progress" class="agent-progress" role="status" aria-live="polite"></div><div id="chat-error" class="notice error hidden" role="alert"></div></form></section>`;
document.querySelector('#workspace').before(assistantView);
const assistantSettings=document.createElement('dialog');
assistantSettings.id='assistant-settings';assistantSettings.className='agent-settings';assistantSettings.setAttribute('aria-labelledby','assistant-settings-title');
assistantSettings.innerHTML=`<form id="assistant-settings-form"><div class="agent-settings-header"><div><span class="agent-eyebrow">Подключение</span><h2 id="assistant-settings-title">OpenAI</h2></div><button type="button" id="assistant-settings-close" class="agent-close" aria-label="Закрыть настройки">×</button></div><div class="agent-settings-body"><p class="muted">Настройте модель для помощника. Сохранение настроек не отправляет данные магазина в OpenAI.</p><label class="field" for="assistant-api-key">API-ключ<input id="assistant-api-key" name="api_key" type="password" autocomplete="off" spellcheck="false" autocapitalize="none" placeholder="Введите ключ OpenAI"></label><p id="assistant-key-hint" class="agent-help"></p><label class="field" for="assistant-model">Модель<input id="assistant-model" name="model" type="text" required maxlength="100" autocomplete="off" value="gpt-5.4-mini"></label><label id="assistant-persist-field" class="check hidden"><input id="assistant-persist" type="checkbox"><span>Сохранить на этом компьютере (защита Windows)</span></label><p id="assistant-storage-warning" class="agent-help"></p><div id="assistant-settings-status" class="agent-connection" role="status"></div><div id="assistant-settings-error" class="notice error hidden" role="alert"></div></div><div class="agent-settings-footer"><button type="button" id="assistant-test" disabled>Проверить подключение</button><button class="primary" id="assistant-settings-save" type="submit">Сохранить настройки</button><button type="button" id="assistant-settings-open" class="hidden">Открыть помощника</button></div><p class="agent-help agent-probe-help">Проверка использует сохранённые настройки и отправляет тестовый запрос без данных магазина. API оплачивается отдельно по тарифам OpenAI.</p></form>`;
document.body.append(assistantSettings);
const assistantSettingsEntry=document.createElement('button');assistantSettingsEntry.id='assistant-settings-entry';assistantSettingsEntry.type='button';assistantSettingsEntry.textContent='Подключить OpenAI';assistantSettingsEntry.setAttribute('aria-haspopup','dialog');
(document.querySelector('.mode-nav')||document.querySelector('header')).append(assistantSettingsEntry);
let chatPending=false,chatProviders=[],chatGeneration=0,chatConfigurationRequest=0,currentMode='manual';
let chatSettings={ready:false,model:'',default_model:'gpt-5.4-mini',storage_available:false,persistent:false};
let chatConversationId=agentUuid(),chatRetry=null,settingsPending=false,settingsSession=0,proposalActionsPending=0;
const chatProposals=new Map();
window.assistantReady=false;
window.isAssistantReady=()=>chatProviders.some(provider=>provider.id==='openai'&&provider.ready===true);
function agentUuid(){return crypto.randomUUID();}
function agentElement(tag,className,text){const element=document.createElement(tag);if(className)element.className=className;if(text!==undefined)element.textContent=String(text??'');return element;}
function agentBusy(){return chatPending||proposalActionsPending>0;}
function updateAssistantEntrypoints(){
  const ready=window.isAssistantReady(),changed=window.assistantReady!==ready;window.assistantReady=ready;
  document.querySelectorAll('[data-mode="ai"],[data-assistant-entry]').forEach(element=>{element.classList.toggle('hidden',!ready);element.disabled=!ready;});
  assistantSettingsEntry.textContent=ready?'Настройки AI':'Подключить OpenAI';assistantSettingsEntry.disabled=settingsPending||agentBusy();
  if(!ready&&currentMode==='ai')showMode('manual');
  if(changed)window.dispatchEvent(new CustomEvent('assistant-readiness',{detail:{ready}}));
}
function showMode(mode){
  if(!['manual','plan','ai'].includes(mode)||mode==='ai'&&!window.isAssistantReady())mode='manual';
  if(mode==='manual'&&typeof window.opsOpen!=='function')mode='plan';currentMode=mode;
  for(const [id,value] of [['workspace','plan'],['assistant-view','ai'],['operations-view','manual']])document.getElementById(id)?.classList.toggle('hidden',mode!==value);
  document.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));
  if(mode==='manual'){window.opsOpen();return;}
  const heading=document.getElementById(mode==='ai'?'assistant-view':'workspace')?.querySelector('h1');heading?.setAttribute('tabindex','-1');heading?.focus();
  if(mode==='ai'){syncChatProducts();loadChatConfiguration();}
}
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>showMode(button.dataset.mode));
function addChatMessage(role,text){
  $('#chat-empty')?.remove();
  const item=agentElement('article','chat-message '+role),label=agentElement('strong','',role==='user'?'Вы':'Помощник');
  item.append(label,document.createTextNode(text));$('#chat-log').append(item);item.scrollIntoView({block:'nearest'});return item;
}
function resetChat(){
  if(agentBusy())return false;
  chatGeneration++;chatConversationId=agentUuid();chatRetry=null;
  for(const entry of chatProposals.values())if(entry.expiryTimer)clearTimeout(entry.expiryTimer);
  chatProposals.clear();$('#chat-log').replaceChildren();$('#chat-question').value='';$('#chat-error').classList.add('hidden');$('#chat-progress').replaceChildren();
  const empty=agentElement('div','agent-empty');empty.id='chat-empty';empty.append(agentElement('span','agent-empty-symbol','AI'),agentElement('h2','','Чем помочь в работе?'),agentElement('p','','Помощник сам найдёт товары, остатки и документы. Перед сохранением покажет подробности и объяснит результат.'));
  $('#chat-log').append(empty);return true;
}
// Kept for forecast integration: the agent retrieves current context on the server.
function syncChatProducts(){updateChatAvailability();}
function updateChatAvailability(){
  const ready=window.isAssistantReady(),pending=agentBusy();
  $('#chat-send').disabled=pending||settingsPending||!ready;$('#chat-clear').disabled=pending;$('#chat-question').disabled=pending;$('#chat-settings').disabled=pending||settingsPending;
  document.querySelectorAll('[data-prompt]').forEach(button=>button.disabled=pending||!ready);
  $('#chat-model').textContent=chatSettings.model?'· '+chatSettings.model:'';
  $('#chat-connection').textContent=ready?'Подключение настроено. Доступ к модели проверяется при отправке запроса.':'Подключите OpenAI в настройках, чтобы начать диалог. Работа вручную доступна.';
  $('#assistant-test').disabled=settingsPending||pending||!ready;$('#assistant-settings-save').disabled=settingsPending||pending;
  $('#assistant-api-key').disabled=settingsPending;$('#assistant-model').disabled=settingsPending;$('#assistant-persist').disabled=settingsPending||chatSettings.storage_available!==true;
  $('#assistant-settings-open').classList.toggle('hidden',!ready);$('#assistant-settings-open').disabled=settingsPending||pending;
  for(const entry of chatProposals.values())updateProposalButtons(entry);
  updateAssistantEntrypoints();
}
function applyChatConfiguration(data){
  if(!data||!Array.isArray(data.providers))throw Error('Сервер вернул некорректные настройки подключения.');
  chatProviders=data.providers.filter(provider=>provider&&provider.id==='openai'&&typeof provider.name==='string').map(provider=>({...provider,ready:provider.ready===true}));
  chatSettings={...chatSettings,...(data.settings||{}),ready:chatProviders.some(provider=>provider.ready)};
  updateChatAvailability();return data;
}
async function loadChatConfiguration(){
  if(chatPending||settingsPending)return null;const request=++chatConfigurationRequest;
  try{const data=await api('/api/assistant/config');if(request!==chatConfigurationRequest||settingsPending)return null;return applyChatConfiguration(data);}
  catch(error){if(request!==chatConfigurationRequest||settingsPending)return null;chatProviders=[];chatSettings.ready=false;updateChatAvailability();if(assistantSettings.open)settingsError(error.message);return null;}
}
function settingsError(message){const box=$('#assistant-settings-error');box.textContent=message;box.classList.toggle('hidden',!message);}
function clearSettingsKey(){$('#assistant-api-key').value='';}
function fillSettings(){
  $('#assistant-model').value=chatSettings.model||chatSettings.default_model||'gpt-5.4-mini';$('#assistant-api-key').required=!window.isAssistantReady();
  $('#assistant-api-key').placeholder=window.isAssistantReady()?'Оставьте пустым, чтобы сохранить текущий ключ':'Введите ключ OpenAI';
  $('#assistant-key-hint').textContent=window.isAssistantReady()?'Ключ уже настроен. Для замены введите новый; текущий ключ не отображается.':'Ключ передаётся вашему серверу. Не вставляйте его в сообщения помощнику.';
  const available=chatSettings.storage_available===true;$('#assistant-persist-field').classList.toggle('hidden',!available);$('#assistant-persist').checked=available&&chatSettings.persistent===true;
  $('#assistant-storage-warning').textContent=chatSettings.warning||(!available?'Постоянное хранение недоступно. Настройка ключа действует до перезапуска сервера.':'');
  $('#assistant-settings-status').textContent=window.isAssistantReady()?'Подключение настроено'+(chatSettings.persistent?' и сохранено на сервере.':'.')+' Доступ к модели пока не проверен.':'';
  updateChatAvailability();
}
async function openAssistantSettings(){
  if(settingsPending||agentBusy())return;const session=++settingsSession;clearSettingsKey();settingsError('');fillSettings();assistantSettings.showModal();
  const data=await loadChatConfiguration();if(data&&assistantSettings.open&&session===settingsSession)fillSettings();
}
function closeAssistantSettings(){settingsSession++;clearSettingsKey();assistantSettings.close();}
assistantSettingsEntry.onclick=openAssistantSettings;$('#chat-settings').onclick=openAssistantSettings;$('#assistant-settings-close').onclick=closeAssistantSettings;
assistantSettings.addEventListener('close',clearSettingsKey);assistantSettings.addEventListener('cancel',clearSettingsKey);
$('#assistant-settings-open').onclick=()=>{closeAssistantSettings();showMode('ai');};
$('#assistant-settings-form').onsubmit=async event=>{
  event.preventDefault();if(settingsPending||agentBusy())return;
  const model=$('#assistant-model').value.trim(),apiKey=$('#assistant-api-key').value.trim();
  if(!model){settingsError('Укажите модель OpenAI.');return;}if(!apiKey&&!window.isAssistantReady()){settingsError('Введите API-ключ OpenAI.');return;}
  settingsPending=true;++chatConfigurationRequest;settingsError('');$('#assistant-settings-status').textContent='Сохраняем настройки…';updateChatAvailability();
  try{const data=await api('/api/assistant/configure',{api_key:apiKey,model,persist:chatSettings.storage_available===true&&$('#assistant-persist').checked});applyChatConfiguration(data);fillSettings();$('#assistant-settings-status').textContent='Настройки сохранены. Проверьте подключение или откройте помощника. Доступ к модели ещё не проверен.';}
  catch(error){settingsError(error.message);$('#assistant-settings-status').textContent='Настройки не подтверждены сервером. Проверьте подключение и попробуйте ещё раз.';}
  finally{clearSettingsKey();settingsPending=false;updateChatAvailability();}
};
$('#assistant-test').onclick=async()=>{
  if(settingsPending||agentBusy()||!window.isAssistantReady())return;settingsPending=true;settingsError('');$('#assistant-settings-status').textContent='Проверяем доступ к модели без данных магазина…';updateChatAvailability();
  try{const result=await api('/api/assistant/test',{},60000);if(result?.ok!==true)throw Error(result?.message||'Проверка подключения не прошла.');$('#assistant-settings-status').textContent=result.message||'Тестовый запрос выполнен. Модель доступна.';}
  catch(error){settingsError(error.message);$('#assistant-settings-status').textContent='Не удалось подтвердить доступ к модели.';}
  finally{clearSettingsKey();settingsPending=false;updateChatAvailability();}
};
const agentDocumentLabels={purchase_order:'Заказ поставщику',purchase_invoice:'Счёт поставщика',receipt:'Приёмка',supplier_return:'Возврат поставщику',sales_order:'Заказ покупателя',sales_invoice:'Счёт покупателю',shipment:'Отгрузка',customer_return:'Возврат покупателя',transfer:'Перемещение',stock_in:'Оприходование',write_off:'Списание',inventory:'Инвентаризация',payment_in:'Поступление денег',payment_out:'Выплата денег',retail_sale:'Розничная продажа',retail_return:'Розничный возврат',production_order:'Заказ на производство',production:'Производственная операция'};
function proposalExpired(entry){const time=Date.parse(entry.data.expires_at||'');return Number.isFinite(time)&&time<=Date.now();}
function updateProposalButtons(entry){
  if(entry.status==='pending'&&proposalExpired(entry)){entry.status='expired';entry.statusNode.textContent='Срок предложения истёк. Попросите помощника подготовить новое.';}
  const disabled=entry.status!=='pending'||entry.pending||agentBusy()||settingsPending||entry.conversationId!==chatConversationId;
  entry.confirmButton.disabled=disabled;entry.declineButton.disabled=disabled;
}
function proposalField(list,label,value){if(value===undefined||value===null||value==='')return;const pair=agentElement('div','agent-proposal-field');pair.append(agentElement('dt','',label),agentElement('dd','',value));list.append(pair);}
function renderAgentProposal(proposal){
  if(!proposal||typeof proposal.proposal_id!=='string'||!['document','task'].includes(proposal.type)||chatProposals.has(proposal.proposal_id))return;
  const details=proposal.details||{},card=agentElement('section','agent-proposal'),heading=agentElement('div','agent-proposal-heading');
  heading.append(agentElement('span','agent-proposal-type',proposal.type==='task'?'Задача на проверку':'Документ на проверку'),agentElement('h3','',proposal.title||'Предложение помощника'));card.append(heading);
  const list=agentElement('dl','agent-proposal-details');
  if(proposal.type==='document'){proposalField(list,'Документ',agentDocumentLabels[details.kind]||details.kind);proposalField(list,'Дата',details.date);proposalField(list,'Контрагент',details.counterparty_name);proposalField(list,'Склад',details.warehouse_name);proposalField(list,'На склад',details.target_warehouse_name);}
  else{proposalField(list,'Название',details.title);proposalField(list,'Срок',details.due_date);}
  if(list.children.length)card.append(list);
  if(Array.isArray(details.lines)&&details.lines.length){
    const tableWrap=agentElement('div','agent-proposal-table'),table=agentElement('table'),head=agentElement('thead'),headRow=agentElement('tr');
    for(const label of ['Товар','Количество','Цена','Сумма'])headRow.append(agentElement('th','',label));head.append(headRow);table.append(head);const body=agentElement('tbody');
    for(const line of details.lines){const row=agentElement('tr'),name=agentElement('td');name.append(agentElement('strong','',line.name||'Товар'));if(line.sku)name.append(agentElement('small','muted',line.sku));if(line.role)name.append(agentElement('small','muted',line.role==='output'?'Готовая продукция':'Материал'));row.append(name,agentElement('td','',String(line.quantity??'')+(line.unit?' '+line.unit:'')),agentElement('td','',line.price??'—'),agentElement('td','',line.amount??'—'));body.append(row);}
    table.append(body);tableWrap.append(table);card.append(tableWrap);
  }
  if(details.amount!==undefined){const total=agentElement('div','agent-proposal-total');total.append(agentElement('span','','Сумма'),agentElement('strong','',details.amount));card.append(total);}
  if(details.description)card.append(agentElement('p','agent-proposal-description',details.description));
  if(proposal.effect)card.append(agentElement('p','agent-proposal-effect',proposal.effect));
  if(Array.isArray(proposal.warnings)&&proposal.warnings.length){const warnings=agentElement('ul','agent-proposal-warnings');for(const warning of proposal.warnings)warnings.append(agentElement('li','',warning));card.append(warnings);}
  const expiry=Date.parse(proposal.expires_at||'');if(Number.isFinite(expiry))card.append(agentElement('p','agent-help','Действительно до '+new Date(expiry).toLocaleString('ru-RU')));
  const actions=agentElement('div','agent-proposal-actions'),confirmButton=agentElement('button','primary',proposal.type==='task'?'Создать задачу':'Сохранить черновик'),declineButton=agentElement('button','','Отклонить');confirmButton.type=declineButton.type='button';actions.append(confirmButton,declineButton);
  const statusNode=agentElement('p','agent-proposal-status'),errorNode=agentElement('p','agent-proposal-error hidden');statusNode.setAttribute('role','status');errorNode.setAttribute('role','alert');card.append(actions,statusNode,errorNode);
  const entry={data:proposal,element:card,confirmButton,declineButton,statusNode,errorNode,actions,conversationId:chatConversationId,status:proposal.status||'pending',pending:false,expiryTimer:null};chatProposals.set(proposal.proposal_id,entry);
  confirmButton.onclick=()=>handleAgentProposal(proposal.proposal_id,'confirm');declineButton.onclick=()=>handleAgentProposal(proposal.proposal_id,'decline');
  if(entry.status!=='pending')statusNode.textContent=entry.status==='confirmed'?'Предложение уже сохранено.':entry.status==='declined'?'Предложение отклонено.':'Предложение недоступно для подтверждения.';
  if(Number.isFinite(expiry)&&expiry>Date.now())entry.expiryTimer=setTimeout(()=>updateProposalButtons(entry),Math.min(expiry-Date.now()+20,2147483647));
  updateProposalButtons(entry);$('#chat-log').append(card);card.scrollIntoView({block:'nearest'});
}
async function handleAgentProposal(id,action){
  const entry=chatProposals.get(id);if(!entry||entry.pending||entry.status!=='pending'||entry.conversationId!==chatConversationId||agentBusy()||settingsPending)return;
  if(proposalExpired(entry)){updateProposalButtons(entry);return;}
  entry.pending=true;proposalActionsPending++;entry.errorNode.textContent='';entry.errorNode.classList.add('hidden');entry.statusNode.textContent=action==='confirm'?'Сохраняем предложенное действие…':'Отклоняем предложение…';updateChatAvailability();
  try{
    const result=await api('/api/agent/'+action,{conversation_id:entry.conversationId,proposal_id:id});
    const expected=action==='confirm'?'confirmed':'declined';if(result?.status!==expected)throw Error('Сервер не подтвердил результат. Проверьте документы перед повторным действием.');
    entry.status=expected;entry.statusNode.textContent=expected==='declined'?'Предложение отклонено. Ничего не создано.':entry.data.type==='task'?'Задача создана.':'Черновик сохранён. Проведение доступно в рабочем пространстве.';
    if(expected==='confirmed'){const open=agentElement('button','agent-text-button',entry.data.type==='task'?'Открыть задачи ↗':'Открыть документы ↗');open.type='button';open.onclick=()=>{showMode('manual');if(typeof window.opsOpen==='function')window.opsOpen(entry.data.type==='task'?'tasks':'company',entry.data.type==='task'?'tasks':'documents');};entry.actions.append(open);}
  }catch(error){
    entry.errorNode.textContent=error.message;entry.errorNode.classList.remove('hidden');
    if(/ист[её]к|просроч|устарел|изменился|перезапущен|заверш[её]н|недоступно|expired|stale/i.test(error.message)){entry.status='stale';entry.statusNode.textContent='Предложение больше не актуально. Попросите помощника подготовить новое.';}
    else entry.statusNode.textContent='Результат не подтверждён. Проверьте учёт перед повтором. Повторное нажатие использует то же предложение.';
  }finally{entry.pending=false;proposalActionsPending--;updateChatAvailability();}
}
function renderAgentSteps(steps){
  if(!Array.isArray(steps)||!steps.length)return;const box=agentElement('div','agent-steps');box.append(agentElement('span','agent-steps-label','Действия помощника:'));
  for(const step of steps)if(step&&typeof step.label==='string')box.append(agentElement('span','agent-step',step.label));
  if(box.children.length>1)$('#chat-log').append(box);
}
$('#chat-form').onsubmit=async event=>{
  event.preventDefault();if(agentBusy()||$('#chat-send').disabled)return;
  const question=$('#chat-question').value.trim();if(!question)return;if(question.length>4000){$('#chat-error').textContent='Сократите вопрос до 4000 символов.';$('#chat-error').classList.remove('hidden');return;}
  const generation=chatGeneration,conversationId=chatConversationId;
  if(!chatRetry||chatRetry.message!==question||chatRetry.conversationId!==conversationId)chatRetry={message:question,requestId:agentUuid(),conversationId,bubble:addChatMessage('user',question)};
  const attempt=chatRetry;attempt.bubble.classList.remove('chat-failed');chatPending=true;updateChatAvailability();$('#chat-error').classList.add('hidden');$('#chat-progress').replaceChildren(agentElement('span','loading'),document.createTextNode('Помощник изучает данные и готовит ответ…'));
  try{
    const result=await api('/api/agent/chat',{conversation_id:conversationId,message:question,request_id:attempt.requestId},150000);
    if(generation!==chatGeneration||conversationId!==chatConversationId)return;
    if(typeof result?.answer!=='string'||!result.answer.trim())throw Error('Сервер не вернул ответ помощника. Повторите тот же вопрос.');
    if(result.conversation_id&&result.conversation_id!==conversationId)throw Error('Ответ относится к другому диалогу. Начните новый диалог.');
    addChatMessage('assistant',result.answer);renderAgentSteps(result.steps);for(const proposal of result.proposals||[])renderAgentProposal(proposal);
    if(result.model)$('#chat-model').textContent='· '+String(result.model);$('#chat-question').value='';chatRetry=null;
  }catch(error){if(generation===chatGeneration){attempt.bubble.classList.add('chat-failed');$('#chat-error').textContent=error.message;$('#chat-error').classList.remove('hidden');}}
  finally{chatPending=false;$('#chat-progress').replaceChildren();updateChatAvailability();}
};
$('#chat-question').onkeydown=event=>{
  if(event.key!=='Enter'||event.shiftKey||event.ctrlKey||event.altKey||event.metaKey||event.isComposing||event.keyCode===229||event.defaultPrevented)return;
  if(agentBusy()||settingsPending||!window.isAssistantReady()||$('#chat-send').disabled||!$('#chat-question').value.trim())return;
  event.preventDefault();$('#chat-form').requestSubmit();
};
$('#chat-clear').onclick=resetChat;$('#chat-data').onclick=()=>showMode('manual');
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('#chat-question').value=button.dataset.prompt;$('#chat-question').focus();});
resetChat();updateChatAvailability();
function startWorkspace(){showMode('manual');return loadChatConfiguration();}
if(document.readyState==='loading')window.addEventListener('DOMContentLoaded',startWorkspace,{once:true});else startWorkspace();


