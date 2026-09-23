/* Local business workspace. All accounting changes are made by the server. */
(() => {
  'use strict';
  const ops = {module:'company',tab:'overview',data:null,documents:[],journal:[],query:'',status:'',from:'',to:'',warehouse:'',page:0,loading:false,loaded:false,notice:'',request:0,lastLoadError:'',stale:false};
  const pageSize = 40;
  const docKinds = {
    purchase_order:{label:'Заказы поставщикам',singular:'Заказ поставщику',section:'purchases'},
    purchase_invoice:{label:'Счета поставщиков',singular:'Счёт поставщика',section:'purchases'},
    receipt:{label:'Приёмки',singular:'Приёмка',section:'purchases',stock:true},
    supplier_return:{label:'Возвраты поставщикам',singular:'Возврат поставщику',section:'purchases',stock:true},
    sales_order:{label:'Заказы покупателей',singular:'Заказ покупателя',section:'sales'},
    sales_invoice:{label:'Счета покупателям',singular:'Счёт покупателю',section:'sales'},
    shipment:{label:'Отгрузки',singular:'Отгрузка',section:'sales',stock:true},
    customer_return:{label:'Возвраты покупателей',singular:'Возврат покупателя',section:'sales',stock:true},
    transfer:{label:'Перемещения',singular:'Перемещение',section:'warehouse',stock:true},
    stock_in:{label:'Оприходования',singular:'Оприходование',section:'warehouse',stock:true},
    write_off:{label:'Списания',singular:'Списание',section:'warehouse',stock:true},
    inventory:{label:'Инвентаризации',singular:'Инвентаризация',section:'warehouse',stock:true},
    payment_in:{label:'Поступления',singular:'Поступление денег',section:'money',payment:true},
    payment_out:{label:'Выплаты',singular:'Выплата денег',section:'money',payment:true},
    retail_sale:{label:'Розничные продажи',singular:'Розничная продажа',section:'retail',stock:true},
    retail_return:{label:'Розничные возвраты',singular:'Розничный возврат',section:'retail',stock:true},
    production_order:{label:'Заказы на производство',singular:'Заказ на производство',section:'production',production:true},
    production:{label:'Производственные операции',singular:'Производственная операция',section:'production',production:true,stock:true}
  };
  const modules = {
    company:{label:'Компания',description:'Общий результат и последние события вашего бизнеса.',tabs:[['overview','Обзор'],['documents','Документы'],['journal','Журнал изменений']]},
    purchases:{label:'Закупки',description:'От заказа поставщику до поступления товара на склад.',tabs:[['purchase_order','Заказы поставщикам'],['purchase_invoice','Счета'],['receipt','Приёмки'],['supplier_return','Возвраты']]},
    sales:{label:'Продажи',description:'Заказы покупателей, счета, отгрузки и возвраты.',tabs:[['sales_order','Заказы покупателей'],['sales_invoice','Счета'],['shipment','Отгрузки'],['customer_return','Возвраты'],['sales_report','Отчёт по продажам']]},
    products:{label:'Товары',description:'Единый каталог товаров и цен для всех операций.',tabs:[['products','Товары и цены'],['stock','Остатки']]},
    crm:{label:'Контрагенты',description:'Покупатели, поставщики и связанные с ними операции.',tabs:[['counterparties','Контрагенты'],['counterparty_report','Обороты контрагентов']]},
    warehouse:{label:'Склад',description:'Движение товаров, остатки и сверка фактического наличия.',tabs:[['stock','Остатки'],['transfer','Перемещения'],['stock_in','Оприходования'],['write_off','Списания'],['inventory','Инвентаризации'],['warehouses','Склады']]},
    money:{label:'Деньги',description:'Поступления, выплаты и движение денежных средств.',tabs:[['cashflow','Движение денег'],['payment_in','Поступления'],['payment_out','Выплаты']]},
    retail:{label:'Розница',description:'Учёт розничных продаж и возвратов.',tabs:[['retail_sale','Продажи'],['retail_return','Возвраты'],['retail_report','Показатели'],['retail_setup','Кассы и торговые точки']]},
    online:{label:'Онлайн-продажи',description:'Заказы сайта и готовность к подключению каналов продаж.',tabs:[['online_orders','Заказы'],['channels','Каналы продаж']]},
    production:{label:'Производство',description:'Планирование выпуска, списание материалов и приход продукции.',tabs:[['production_order','Заказы на производство'],['production','Операции'],['production_report','Выпуск продукции']]},
    tasks:{label:'Задачи',description:'Договорённости и ежедневная работа команды.',tabs:[['tasks','Все задачи']]},
    solutions:{label:'Решения',description:'Возможности рабочего пространства и внешние подключения.',tabs:[['solutions','Возможности и подключения']]}
  };
  const icons = {company:'M3 10 12 3l9 7M5 9v12h14V9M9 21v-8h6v8',purchases:'M3 4h2l3 12h10l3-9H6M9 21h.01M18 21h.01M13 3v8m-3-3 3 3 3-3',sales:'M4 17 10 11l4 3 7-10M15 4h6v6M4 5v16h17',products:'m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 9v10M3 8l9 4 9-4M8 5l9 5',crm:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M16 3a4 4 0 0 1 0 8m3 4a4 4 0 0 1 3 4v2',warehouse:'m3 9 9-6 9 6v12H3V9Zm4 12V11h10v10M7 15h10m-10 3h10',money:'M3 5h18v15H3V5Zm0 4h18m-6 4h6v4h-6v-4Zm-9-8V3h12v2',retail:'M3 9h18l-2-6H5l-2 6Zm2 0v12h14V9M9 21v-8h6v8',online:'M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0ZM3 12h18M12 3a17 17 0 0 1 0 18 17 17 0 0 1 0-18Z',production:'M3 21V10l6 4V8l6 4V3h5l1 18H3Zm4-4h1m4 0h1m4 0h1',tasks:'M8 4H4v17h16V4h-4M8 2h8v5H8V2Zm0 11 3 3 6-6',solutions:'M9 3H3v6h6V3Zm12 0h-6v6h6V3ZM9 15H3v6h6v-6Zm9-1v8m-4-4h8'};
  const statusLabels = {draft:'Черновик',posted:'Проведён',cancelled:'Отменён',open:'Открыта',done:'Выполнена'};
  const typeLabels = {customer:'Покупатель',supplier:'Поставщик',both:'Покупатель и поставщик'};
  const opsEsc = value => esc(value);
  const opsFmt = value => value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:6});
  const money = value => Number(value || 0).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
  const today = () => {const date=new Date();return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;};
  const dateText = value => value ? String(value).slice(0,10).split('-').reverse().join('.') : '—';
  const badge = status => `<span class="ops-badge ${opsEsc(status)}">${opsEsc(statusLabels[status] || status)}</span>`;
  const rowsOf = (value,key) => Array.isArray(value) ? value : value?.[key] || [];
  const lookup = (kind,id) => (ops.data?.[kind] || []).find(row=>String(row.id)===String(id));
  const productName = id => lookup('products',id)?.name || 'Товар не найден';
  const importedProduct = product => Array.isArray(product.external_keys) && product.external_keys.length > 0;
  const productOrigin = product => importedProduct(product) ? 'План закупок' : 'Добавлен в учёте';
  const catalogueNote = products => {const count=products.filter(importedProduct).length;return count?`${opsFmt(count)} из плана закупок · карточки без переноса остатков`:'Общий справочник для всех разделов';};
  const journalSource = row => row.source || (row.action==='import'?'План закупок':'Рабочее пространство');
  const partnerName = id => id ? lookup('counterparties',id)?.name || 'Контрагент не найден' : '—';
  const warehouseName = id => id ? lookup('warehouses',id)?.name || 'Склад не найден' : '—';
  const posted = () => ops.documents.filter(doc=>doc.status==='posted');
  const docName = kind => docKinds[kind]?.singular || kind;
  const searchMatch = values => !ops.query || values.some(value=>String(value || '').toLocaleLowerCase('ru-RU').includes(ops.query.toLocaleLowerCase('ru-RU')));
  const stat = (label,value,note='') => `<div class="ops-stat"><div class="ops-stat-label">${opsEsc(label)}</div><div class="ops-stat-value">${opsEsc(value)}</div><div class="ops-stat-note">${opsEsc(note)}</div></div>`;
  const empty = (title,description,action='',label='Создать') => `<div class="ops-empty"><div class="ops-empty-icon" aria-hidden="true">▤</div><h2>${opsEsc(title)}</h2><p>${opsEsc(description)}</p>${action?`<button class="primary" data-ops-action="${opsEsc(action)}">${opsEsc(label)}</button>`:''}</div>`;
  const hint = text => `<div class="ops-context">${opsEsc(text)}</div>`;
  const returnHint = kind => ['supplier_return','customer_return','retail_return'].includes(kind)?hint('Возврат оформляется вручную. Сверьте количество, цену и основание с исходным документом: автоматической связи с ним нет.'):'';
  const table = (head,body) => `<div class="ops-panel"><div class="ops-table-wrap"><table class="ops-table"><thead><tr>${head.map(name=>`<th>${opsEsc(name)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div></div>`;
  const link = (label,action,id='') => `<button class="ops-link" data-ops-action="${opsEsc(action)}" data-id="${opsEsc(id)}">${opsEsc(label)}</button>`;
  const options = (rows,value,label,blank='Не выбран') => `<option value="">${opsEsc(blank)}</option>`+rows.map(row=>`<option value="${opsEsc(row.id)}" ${String(row.id)===String(value)?'selected':''}>${opsEsc(label(row))}</option>`).join('');
  function scaledDecimal(value,places){
    const match=String(value??'0').trim().match(/^([+-]?)(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    if(!match||(!match[2]&&!match[3]))return 0n;
    const digits=(match[2]||'0')+(match[3]||''),shift=places+Number(match[4]||0)-(match[3]||'').length;
    if(Math.abs(shift)>100)return 0n;
    const result=shift>=0?BigInt(digits)*10n**BigInt(shift):BigInt(digits)/10n**BigInt(-shift);
    return match[1]==='-'?-result:result;
  }
  const lineCents = line => (scaledDecimal(line.quantity,6)*scaledDecimal(line.price,2)+500000n)/1000000n;
  const lineAmount = line => Number(lineCents(line))/100;
  const docCents = doc => doc.amount!=null?scaledDecimal(doc.amount,2):(doc.lines||[]).filter(line=>!docKinds[doc.kind]?.production||line.role==='output').reduce((total,line)=>total+lineCents(line),0n);
  const docTotal = doc => Number(docCents(doc))/100;
  const sumDocuments = docs => Number(docs.reduce((total,doc)=>total+docCents(doc),0n))/100;
  let opsLoadPromise=null;

  function mount(){
    if(document.getElementById('operations-view'))return;
    const view=document.createElement('main');view.id='operations-view';view.className='hidden';
    view.innerHTML=`<div class="ops-layout"><aside class="ops-sidebar"><div class="ops-brand">Рабочее пространство</div><nav class="ops-nav" aria-label="Разделы учёта">${Object.entries(modules).map(([key,module])=>`<button data-ops-module="${key}" title="${module.label}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[key]}"></path></svg>${module.label}</button>`).join('')}</nav><div class="ops-sidebar-foot"><button data-ops-action="forecast">План закупок ↗</button><p class="ops-hint">Операции и прогноз закупок доступны в одном рабочем месте.</p></div></aside><section class="ops-main"><div class="ops-heading"><div><p class="ops-eyebrow">Управление бизнесом</p><h1 id="ops-title" tabindex="-1">Компания</h1><p class="muted" id="ops-description"></p></div><div class="actions"><span class="ops-status"><i class="ops-dot"></i>Локальный учёт</span><button data-ops-action="refresh" title="Обновить данные" aria-label="Обновить данные">↻</button></div></div><nav class="ops-subnav" id="ops-subnav" aria-label="Подразделы"></nav><div id="ops-alert" role="status"></div><div id="ops-content"></div></section></div>`;
    document.getElementById('workspace').before(view);
    const dialog=document.createElement('dialog');dialog.id='ops-dialog';dialog.className='ops-dialog';dialog.setAttribute('aria-labelledby','ops-dialog-title');document.body.append(dialog);
    view.addEventListener('click',onClick);dialog.addEventListener('click',onClick);
    view.addEventListener('input',event=>{if(event.target.id==='ops-search'){ops.query=event.target.value;ops.page=0;renderContent(true);}});
    view.addEventListener('change',event=>{const key=event.target.dataset.opsFilter;if(key){ops[key]=event.target.value;ops.page=0;renderContent(true);}});
    dialog.addEventListener('input',event=>{if(event.target.closest('#ops-document-form'))updateLineTotal();});
    dialog.addEventListener('change',event=>{if(event.target.name==='line_product'){const row=event.target.closest('tr'),product=lookup('products',event.target.value);if(product)row.querySelector('[name=line_price]').value=product.price || '0';updateLineTotal();}});
    dialog.addEventListener('submit',onSubmit);
  }

  async function load(force=false){
    if(ops.loading){const result=await opsLoadPromise;if(force)return load();return result;}
    ops.loading=true;const refresh=document.querySelector('[data-ops-action=refresh]');if(refresh)refresh.disabled=true;
    if(!ops.loaded)document.getElementById('ops-content').innerHTML='<div class="ops-skeleton"><span class="loading"></span>Загружаем рабочее пространство…</div>';
    opsLoadPromise=(async()=>{
    try{
      const withJournal=ops.tab==='journal';
      const [data,documents,journal]=await Promise.all([api('/api/operations/bootstrap'),api('/api/operations/documents'),...(withJournal?[api('/api/operations/journal')]:[])]);
      const nextDocuments=rowsOf(documents,'documents'),nextJournal=withJournal?rowsOf(journal,'journal'):ops.journal;
      // Publish one complete snapshot only after every required request succeeds.
      const wasStale=ops.stale;
      ops.data=data;ops.documents=nextDocuments;ops.journal=nextJournal;ops.loaded=true;ops.lastLoadError='';ops.stale=false;
      if(wasStale)alertMessage('');
      render();
      return true;
    }catch(error){ops.lastLoadError=error.message;alertMessage(error.message,true);if(!ops.loaded)document.getElementById('ops-content').innerHTML=empty('Не удалось загрузить данные','Повторите загрузку. Сохранённые операции останутся на сервере.','refresh','Повторить');return false;}
    finally{ops.loading=false;if(refresh)refresh.disabled=false;}
    })();
    return opsLoadPromise;
  }

  window.opsOpen = function(module,tab){
    mount();document.getElementById('operations-view').classList.remove('hidden');
    if(module&&modules[module]){ops.module=module;ops.tab=tab || modules[module].tabs[0][0];resetFilters();}
    if(ops.loaded)render();load();
  };

  function resetFilters(){ops.query='';ops.status='';ops.from='';ops.to='';ops.warehouse='';ops.page=0;}
  async function navigate(module,tab){
    ops.module=module;ops.tab=tab || modules[module].tabs[0][0];resetFilters();ops.notice='';alertMessage('');render();
    if(ops.tab==='journal'){try{ops.journal=rowsOf(await api('/api/operations/journal'),'journal');renderContent();}catch(error){alertMessage(error.message,true);}}
  }
  function render(){
    const module=modules[ops.module];document.getElementById('ops-title').textContent=module.label;document.getElementById('ops-description').textContent=module.description;
    document.querySelectorAll('[data-ops-module]').forEach(button=>{const active=button.dataset.opsModule===ops.module;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');});
    document.getElementById('ops-subnav').innerHTML=module.tabs.map(([key,label])=>`<button data-ops-tab="${key}" class="${ops.tab===key?'active':''}" aria-current="${ops.tab===key?'page':'false'}">${label}</button>`).join('');
    if(ops.loaded)renderContent();
  }
  function alertMessage(message,isError=false){
    if(ops.stale){message='Операция сохранена, но не удалось обновить данные. Остатки и денежные итоги могут быть неактуальны. Нажмите «Обновить данные». Повторно сохранять операцию не нужно.'+(message?' '+message:'');isError=true;}
    const box=document.getElementById('ops-alert');box.className=message?'ops-message'+(isError?' error':''):'';box.textContent=message;box.setAttribute('role',isError?'alert':'status');
  }
  function savedRefreshError(){ops.stale=true;alertMessage('');}
  function rememberRecord(kind,result){
    const record=result?.document||result?.entity||result;
    if(!record?.id)return null;
    const previous=kind==='documents'?ops.documents:(ops.data?.[kind]||[]);
    const exists=previous.some(row=>row.id===record.id);
    const next=exists?previous.map(row=>row.id===record.id?{...row,...record}:row):[record,...previous];
    if(kind==='documents')ops.documents=next;
    else if(ops.data)ops.data={...ops.data,[kind]:next};
    return record;
  }
  function displayedVersion(value){const version=Number(value);if(!Number.isSafeInteger(version)||version<1)throw Error('Версия записи недоступна. Обновите данные и откройте запись заново.');return version;}
  function toolbar({create='',label='Создать',status=false,dates=false,warehouse=false,exportable=true,extra=''}={}){
    return `<div class="ops-toolbar"><div class="ops-filters"><input type="search" id="ops-search" placeholder="Поиск…" value="${opsEsc(ops.query)}" aria-label="Поиск в разделе">${status?`<select data-ops-filter="status" aria-label="Статус"><option value="">Все статусы</option>${(ops.tab==='tasks'?['open','done']:['draft','posted','cancelled']).map(value=>`<option value="${value}" ${ops.status===value?'selected':''}>${statusLabels[value]}</option>`).join('')}</select>`:''}${warehouse?`<select data-ops-filter="warehouse" aria-label="Склад">${options(ops.data.warehouses||[],ops.warehouse,row=>row.name,'Все склады')}</select>`:''}${dates?`<label>С <input type="date" data-ops-filter="from" value="${ops.from}" aria-label="Начало периода"></label><label>По <input type="date" data-ops-filter="to" value="${ops.to}" aria-label="Конец периода"></label>`:''}</div><div class="actions">${extra}${exportable?'<button data-ops-action="export">Выгрузить CSV</button>':''}${create?`<button class="primary" data-ops-action="${opsEsc(create)}">+ ${opsEsc(label)}</button>`:''}</div></div>`;
  }
  function paginate(rows){const pages=Math.max(1,Math.ceil(rows.length/pageSize));ops.page=Math.max(0,Math.min(ops.page,pages-1));return rows.slice(ops.page*pageSize,(ops.page+1)*pageSize);}
  function pagination(total){const pages=Math.max(1,Math.ceil(total/pageSize));return `<div class="ops-pagination"><span>${total?`${ops.page*pageSize+1}–${Math.min((ops.page+1)*pageSize,total)} из ${opsFmt(total)}`:'0 записей'}</span><div class="actions"><button data-ops-action="prev" ${ops.page===0?'disabled':''}>Назад</button><button data-ops-action="next" ${ops.page>=pages-1?'disabled':''}>Далее</button></div></div>`;}
  function filteredDocs(kinds,ignoreSearch=false){return ops.documents.filter(doc=>(!kinds||kinds.includes(doc.kind))&&(!ops.status||doc.status===ops.status)&&(!ops.from||doc.date>=ops.from)&&(!ops.to||doc.date<=ops.to)&&(ignoreSearch||searchMatch([doc.number,doc.description,docName(doc.kind),partnerName(doc.counterparty_id)])));}
  function renderContent(keepFocus=false){
    if(!ops.data)return;
    const search=document.getElementById('ops-search');const selection=keepFocus&&document.activeElement===search?search.selectionStart:null;
    let content='';
    if(docKinds[ops.tab])content=documentsView(ops.tab);
    else if(['products','counterparties','warehouses'].includes(ops.tab))content=entitiesView(ops.tab);
    else if(ops.tab==='overview')content=overviewView();
    else if(ops.tab==='documents')content=documentsView();
    else if(ops.tab==='stock')content=stockView();
    else if(ops.tab==='tasks')content=tasksView();
    else if(ops.tab==='journal')content=journalView();
    else if(ops.tab==='cashflow')content=cashflowView();
    else if(ops.tab==='sales_report'||ops.tab==='retail_report')content=salesReportView(ops.tab==='retail_report');
    else if(ops.tab==='counterparty_report')content=counterpartyReportView();
    else if(ops.tab==='production_report')content=productionReportView();
    else if(ops.tab==='online_orders')content=hint('Заказы из внешних каналов пока не синхронизируются. Здесь показан общий реестр заказов покупателей; источник заказа можно указать в комментарии.')+documentsView('sales_order');
    else content=informationView(ops.tab);
    document.getElementById('ops-content').innerHTML=content;
    if(selection!==null){const replacement=document.getElementById('ops-search');replacement?.focus();replacement?.setSelectionRange(selection,selection);}
  }

  function overviewView(){
    const data=ops.data,o=data.overview||{},financial=data.money||{},latest=ops.documents.slice().sort((a,b)=>String(b.updated_at||b.date).localeCompare(String(a.updated_at||a.date))).slice(0,5),tasks=(data.tasks||[]).filter(task=>task.status!=='done').slice(0,4);
    return `<div class="ops-stat-grid">${stat('Товары в каталоге',opsFmt(o.products??data.products.length),catalogueNote(data.products))}${stat('Проведено документов',opsFmt(o.posted_documents??posted().length),`${opsFmt(o.draft_documents??ops.documents.filter(doc=>doc.status==='draft').length)} черновиков ожидают обработки`)}${stat('Денежный остаток',money(financial.balance),'По проведённым денежным операциям')}${stat('Открытые задачи',opsFmt(o.tasks_open??tasks.length),'Текущие договорённости и поручения')}</div><div class="ops-quick-actions"><button data-ops-action="new-document" data-kind="sales_order"><span>＋</span>Заказ покупателя</button><button data-ops-action="new-document" data-kind="receipt"><span>↓</span>Приёмка товара</button><button data-ops-action="new-document" data-kind="payment_in"><span>↙</span>Поступление денег</button><button data-ops-action="new-entity" data-kind="tasks"><span>✓</span>Новая задача</button></div><div class="ops-grid2"><section class="ops-panel"><div class="ops-panel-head"><h2>Последние документы</h2><button class="ops-link" data-ops-action="all-documents">Все документы →</button></div>${latest.length?`<div class="ops-table-wrap"><table class="ops-table"><tbody>${latest.map(doc=>`<tr><td>${link(`${docName(doc.kind)} ${doc.number}`,'open-document',doc.id)}<span class="ops-secondary">${dateText(doc.date)} · ${opsEsc(partnerName(doc.counterparty_id))}</span></td><td>${badge(doc.status)}</td><td class="num">${money(docTotal(doc))}</td></tr>`).join('')}</tbody></table></div>`:empty('Первый документ — начало учёта','Создайте заказ, приёмку или денежную операцию. Склад и деньги изменятся после проведения.')}</section><section class="ops-panel"><div class="ops-panel-head"><h2>Ближайшие задачи</h2><button class="ops-link" data-ops-action="all-tasks">Все задачи →</button></div>${tasks.length?tasks.map(task=>taskRow(task)).join(''):empty('Ожидание новых задач','Добавьте задачу, когда появится новое поручение.','new-task','Добавить задачу')}</section></div><div class="ops-panel"><div class="ops-panel-head"><h2>Начните с ваших данных</h2><span class="ops-badge info">Учёт на этом сервере</span></div><div class="ops-panel-body"><p>Добавьте товары, контрагентов и склады. Занесите начальный остаток оприходованием или инвентаризацией, затем оформляйте закупки и продажи.</p><div class="actions"><button data-ops-action="go-products">Открыть каталог</button><button data-ops-action="go-stock">Остатки на складах</button><button data-ops-action="import-report">Взять товары из плана закупок</button></div><p class="ops-hint">Импорт переносит названия, артикулы и единицы измерения из загруженного плана закупок. Складские остатки остаются отдельными и формируются проведёнными операциями.</p></div></div>`;
  }

  function documentsView(kind){
    const rows=filteredDocs(kind?[kind]:null),visible=paginate(rows),meta=docKinds[kind];
    let note='';
    if(kind==='purchase_order'||kind==='sales_order')note='Заказ фиксирует договорённость. Остатки, резервы и деньги заказом не изменяются. Движения оформляются приёмкой, отгрузкой и платежом.';
    if(kind==='purchase_invoice'||kind==='sales_invoice')note='Счёт используется для управленческого учёта. Он не формирует движение денег, налоговый счёт-фактуру или юридически оформленный печатный документ.';
    if(kind==='retail_sale'||kind==='retail_return')note='Проведённая розничная операция изменяет остатки и денежный баланс. Кассовое оборудование, фискальный чек и маркировка не подключены.';
    if(kind==='inventory')note='Инвентаризация устанавливает фактическое количество указанных товаров на выбранном складе. Товары, не включённые в документ, остаются без изменения.';
    if(kind==='production'||kind==='production_order')note='Добавьте строки материалов и готовой продукции. Проведённая производственная операция списывает материалы и приходует результат на выбранный склад; заказ только фиксирует план.';
    return `${note?hint(note):''}${toolbar({create:meta?'new-document':'',label:meta?.singular,status:true,dates:true})}${rows.length?table(['Номер / документ','Дата','Контрагент','Склад','Сумма','Статус'],visible.map(doc=>`<tr><td>${link(doc.number||'Без номера','open-document',doc.id)}${!kind?`<span class="ops-secondary">${opsEsc(docName(doc.kind))}</span>`:''}</td><td>${dateText(doc.date)}</td><td>${opsEsc(partnerName(doc.counterparty_id))}</td><td>${opsEsc(warehouseName(doc.warehouse_id))}${doc.target_warehouse_id?`<span class="ops-secondary">→ ${opsEsc(warehouseName(doc.target_warehouse_id))}</span>`:''}</td><td class="num">${money(docTotal(doc))}</td><td>${badge(doc.status)}</td></tr>`).join('')):`<div class="ops-panel">${empty(ops.query||ops.status||ops.from||ops.to?'Документы не найдены':'Документов пока нет',ops.query||ops.status?'Попробуйте изменить поиск или статус.':'Создайте первый документ, добавьте позиции и сохраните черновик.',meta?'new-document':'',meta?`Создать: ${meta.singular.toLowerCase()}`:'')}</div>`}${pagination(rows.length)}`;
  }

  function entitiesView(kind){
    const labels={products:'Товар',counterparties:'Контрагент',warehouses:'Склад'},rows=(ops.data[kind]||[]).filter(row=>searchMatch([row.name,row.sku,row.type?typeLabels[row.type]:'']));
    const headers=kind==='products'?['Артикул','Наименование','Единица','Цена','Источник']:kind==='counterparties'?['Наименование','Тип','Документы']:['Наименование','Товарных позиций'];
    const body=paginate(rows).map(row=>kind==='products'?`<tr><td>${opsEsc(row.sku||'—')}</td><td>${link(row.name,'open-entity',row.id)}</td><td>${opsEsc(row.unit||'—')}</td><td class="num">${money(row.price)}</td><td>${opsEsc(productOrigin(row))}</td></tr>`:kind==='counterparties'?`<tr><td>${link(row.name,'open-entity',row.id)}</td><td>${opsEsc(typeLabels[row.type]||row.type)}</td><td>${opsFmt(ops.documents.filter(doc=>doc.counterparty_id===row.id).length)}</td></tr>`:`<tr><td>${link(row.name,'open-entity',row.id)}</td><td>${opsFmt((ops.data.stock||[]).filter(stock=>stock.warehouse_id===row.id&&Number(stock.quantity)!==0).length)}</td></tr>`).join('');
    return (kind==='products'&&(ops.data.products||[]).some(importedProduct)?hint('Карточки с источником «План закупок» перенесены из загруженных Excel-архивов. Импорт добавляет названия, артикулы и единицы; цены и начальные остатки заполняются отдельно.'): '')+toolbar({create:'new-entity',label:labels[kind],extra:kind==='products'?'<button data-ops-action="import-report">Из плана закупок</button>':''})+(rows.length?table(headers,body):`<div class="ops-panel">${empty(ops.query?'Ничего не найдено':`${kind==='products'?'Товаров':kind==='counterparties'?'Контрагентов':'Складов'} пока нет`,ops.query?'Измените запрос и повторите поиск.':'Добавьте первую запись. Она будет доступна во всех документах.','new-entity',`Добавить ${labels[kind].toLowerCase()}`)}</div>`)+pagination(rows.length);
  }

  function stockRows(){return (ops.data.stock||[]).filter(row=>(!ops.warehouse||row.warehouse_id===ops.warehouse)&&searchMatch([row.product_name,row.sku,row.warehouse_name]));}
  function stockView(){const rows=stockRows();return hint('Остатки рассчитаны по проведённым операциям этого рабочего пространства. Данные прогноза закупок и аккаунта «МойСклад» автоматически сюда не переносятся.')+toolbar({warehouse:true,extra:'<button data-ops-action="new-document" data-kind="stock_in">+ Оприходовать</button>'})+(rows.length?table(['Артикул','Товар','Склад','Количество','Ед.'],paginate(rows).map(row=>`<tr><td>${opsEsc(row.sku||'—')}</td><td>${link(row.product_name||productName(row.product_id),'stock-product',row.product_id)}</td><td>${opsEsc(row.warehouse_name||warehouseName(row.warehouse_id))}</td><td class="num">${opsFmt(row.quantity)}</td><td>${opsEsc(row.unit||'—')}</td></tr>`).join('')):`<div class="ops-panel">${empty('Остатков пока нет','Создайте и проведите оприходование или приёмку. Для сверки фактического наличия используйте инвентаризацию.','initial-stock','Оприходовать товар')}</div>`)+pagination(rows.length);}
  function taskRow(task){return `<div class="ops-task ${opsEsc(task.status)}"><input type="checkbox" data-ops-action="toggle-task" data-id="${opsEsc(task.id)}" data-version="${opsEsc(task.version)}" data-status="${opsEsc(task.status)}" ${task.status==='done'?'checked':''} aria-label="${task.status==='done'?'Открыть заново':'Выполнить'}: ${opsEsc(task.title)}"><div class="ops-task-main">${link(task.title,'open-task',task.id)}${task.description?`<p>${opsEsc(task.description)}</p>`:''}<small class="${task.status!=='done'&&task.due_date&&task.due_date<today()?'ops-overdue':'muted'}">${task.due_date?`Срок: ${dateText(task.due_date)}`:'Без срока'}</small></div>${badge(task.status)}</div>`;}
  function tasksView(){const rows=(ops.data.tasks||[]).filter(task=>(!ops.status||task.status===ops.status)&&searchMatch([task.title,task.description])).sort((a,b)=>(a.status==='done')-(b.status==='done')||String(a.due_date||'9999').localeCompare(String(b.due_date||'9999')));return toolbar({create:'new-task',label:'Задача',status:true})+`<div class="ops-panel">${rows.length?paginate(rows).map(taskRow).join(''):empty(ops.query||ops.status?'Задачи не найдены':'Задач пока нет','Добавьте поручение, описание и срок выполнения.','new-task','Создать задачу')}</div>`+pagination(rows.length);}
  function journalView(){const rows=ops.journal.filter(row=>searchMatch([row.description,row.action,row.entity_type,row.created_at,journalSource(row)]));return hint('Импорт из плана закупок показан одной записью за загрузку. Он добавляет карточки из Excel-архивов; это не ручное создание товаров и не складские операции.')+toolbar({exportable:true})+(rows.length?table(['Дата и время','Действие','Источник','Описание'],paginate(rows).map(row=>`<tr><td>${opsEsc(String(row.created_at||'').replace('T',' ').slice(0,19))}</td><td>${opsEsc(({create:'Создание',update:'Изменение',post:'Проведение',cancel:'Отмена',import:'Импорт'})[row.action]||row.action)}</td><td>${opsEsc(journalSource(row))}</td><td>${opsEsc(row.description||row.entity_type)}</td></tr>`).join('')):`<div class="ops-panel">${empty('Журнал пока пуст','Здесь появятся сохранения, проведения, отмены и импорт товаров.')}</div>`)+pagination(rows.length);}

  function moneyDocs(){return filteredDocs(['payment_in','payment_out','retail_sale','retail_return']).filter(doc=>doc.status==='posted');}
  function cashflowView(){const rows=moneyDocs(),income=sumDocuments(rows.filter(doc=>['payment_in','retail_sale'].includes(doc.kind))),expense=sumDocuments(rows.filter(doc=>['payment_out','retail_return'].includes(doc.kind)));return `<div class="ops-stat-grid">${stat('Поступления за выборку',money(income),'Платежи и розничные продажи')}${stat('Выплаты за выборку',money(expense),'Выплаты и розничные возвраты')}${stat('Изменение за выборку',money(income-expense),'Поступления минус выплаты')}${stat('Текущий денежный остаток',money(ops.data.money?.balance),'Все проведённые операции')}</div>${hint('Денежный учёт ведётся в одной общей сумме. Банковские счета, сверка с банком, НДС и расчёт задолженности по взаимосвязанным документам пока не подключены.')}${toolbar({dates:true,extra:'<button data-ops-action="new-document" data-kind="payment_out">+ Выплата</button><button class="primary" data-ops-action="new-document" data-kind="payment_in">+ Поступление</button>'})}${rows.length?table(['Документ','Дата','Контрагент','Поступление','Выплата'],paginate(rows).map(doc=>{const income=['payment_in','retail_sale'].includes(doc.kind);return `<tr><td>${link(`${docName(doc.kind)} ${doc.number}`,'open-document',doc.id)}</td><td>${dateText(doc.date)}</td><td>${opsEsc(partnerName(doc.counterparty_id))}</td><td class="num ops-ledger-amount in">${income?money(docTotal(doc)):'—'}</td><td class="num ops-ledger-amount out">${!income?money(docTotal(doc)):'—'}</td></tr>`;}).join('')):`<div class="ops-panel">${empty('Движений денег пока нет','Создайте и проведите поступление, выплату или розничную операцию.')}</div>`}${pagination(rows.length)}`;}
  function salesRows(retail=false){const kinds=retail?['retail_sale','retail_return']:['shipment','customer_return','retail_sale','retail_return'],docs=filteredDocs(kinds,true).filter(doc=>doc.status==='posted'),byProduct=new Map();for(const doc of docs){const sign=['customer_return','retail_return'].includes(doc.kind)?-1n:1n;for(const line of doc.lines||[]){const product=lookup('products',line.product_id)||{},row=byProduct.get(line.product_id)||{id:line.product_id,name:product.name||'Товар',sku:product.sku||'',unit:product.unit||'',quantityUnits:0n,amountCents:0n};row.quantityUnits+=sign*scaledDecimal(line.quantity,6);row.amountCents+=sign*lineCents(line);byProduct.set(line.product_id,row);}}return [...byProduct.values()].filter(row=>searchMatch([row.name,row.sku])).map(row=>({...row,quantity:Number(row.quantityUnits)/1000000,amount:Number(row.amountCents)/100})).sort((a,b)=>b.amount-a.amount);}
  function salesReportView(retail){const rows=salesRows(retail),total=Number(rows.reduce((sum,row)=>sum+row.amountCents,0n))/100;return `<div class="ops-stat-grid">${stat('Сумма продаж',money(total),'Отгрузки за вычетом возвратов')}${stat('Товаров с продажами',opsFmt(rows.length),'Уникальные позиции в выборке')}</div>${hint('Отчёт строится по проведённым отгрузкам и возвратам'+(retail?' розницы.':', включая розницу.')+' Сумма продаж не равна поступившей оплате. Себестоимость и прибыль пока не рассчитываются.')}${toolbar({dates:true})}${rows.length?table(['Артикул','Товар','Количество нетто','Ед.','Сумма продаж'],paginate(rows).map(row=>`<tr><td>${opsEsc(row.sku||'—')}</td><td>${opsEsc(row.name)}</td><td class="num">${opsFmt(row.quantity)}</td><td>${opsEsc(row.unit)}</td><td class="num">${money(row.amount)}</td></tr>`).join('')):`<div class="ops-panel">${empty('Нет проведённых продаж','После проведения отгрузки или розничной продажи здесь появятся результаты.')}</div>`}${pagination(rows.length)}`;}
  function counterpartyRows(){return (ops.data.counterparties||[]).filter(row=>searchMatch([row.name,typeLabels[row.type]])).map(partner=>{const docs=ops.documents.filter(doc=>doc.counterparty_id===partner.id&&doc.status==='posted');const sum=kinds=>sumDocuments(docs.filter(doc=>kinds.includes(doc.kind)));return {...partner,sales:sum(['shipment','retail_sale'])-sum(['customer_return','retail_return']),purchases:sum(['receipt'])-sum(['supplier_return']),income:sum(['payment_in','retail_sale']),expense:sum(['payment_out','retail_return'])};});}
  function counterpartyReportView(){const rows=counterpartyRows();return hint('Обороты показывают проведённые отгрузки, приёмки, возвраты и платежи за всё время. Автоматическая взаимосвязь оплат с документами и расчёт задолженности пока отсутствуют.')+toolbar()+ (rows.length?table(['Контрагент','Продажи нетто','Закупки нетто','Получено','Выплачено'],paginate(rows).map(row=>`<tr><td>${opsEsc(row.name)}</td><td class="num">${money(row.sales)}</td><td class="num">${money(row.purchases)}</td><td class="num">${money(row.income)}</td><td class="num">${money(row.expense)}</td></tr>`).join('')):`<div class="ops-panel">${empty('Контрагентов пока нет','Добавьте покупателя или поставщика и оформите операции.')}</div>`)+pagination(rows.length);}
  function productionRows(){return filteredDocs(['production'],true).filter(doc=>doc.status==='posted').flatMap(doc=>(doc.lines||[]).filter(line=>line.role==='output'&&searchMatch([doc.number,productName(line.product_id),lookup('products',line.product_id)?.sku])).map(line=>({...line,document_id:doc.id,number:doc.number,date:doc.date,warehouse_id:doc.warehouse_id})));}
  function productionReportView(){const rows=productionRows();return hint('Отчёт показывает выпуск по проведённым производственным операциям. Технологические карты, этапы, трудозатраты и производственная себестоимость пока не рассчитываются.')+toolbar({dates:true})+(rows.length?table(['Документ','Дата','Готовая продукция','Склад','Выпущено','Ед.'],paginate(rows).map(row=>`<tr><td>${link(row.number,'open-document',row.document_id)}</td><td>${dateText(row.date)}</td><td>${opsEsc(productName(row.product_id))}</td><td>${opsEsc(warehouseName(row.warehouse_id))}</td><td class="num">${opsFmt(row.quantity)}</td><td>${opsEsc(lookup('products',row.product_id)?.unit)}</td></tr>`).join('')):`<div class="ops-panel">${empty('Выпуска пока нет','Создайте производственную операцию с материалами и готовой продукцией, затем проведите её.')}</div>`)+pagination(rows.length);}

  function informationView(tab){
    if(tab==='retail_setup')return `<div class="ops-info-grid"><article class="ops-info-card"><span class="ops-badge posted">Работает локально</span><h2>Продажи и возвраты</h2><p>Оформляйте товарные позиции, проводите продажи и возвраты. Остатки и общий денежный баланс пересчитываются после проведения.</p><button data-ops-action="new-document" data-kind="retail_sale">Создать продажу</button></article><article class="ops-info-card"><span class="ops-badge">Не подключено</span><h2>Кассы и смены</h2><p>Торговые точки, кассовые смены, фискальный регистратор, эквайринг и чеки требуют отдельного подключения и настройки учёта.</p></article><article class="ops-info-card"><span class="ops-badge">Не подключено</span><h2>Маркировка и лояльность</h2><p>Проверка кодов маркировки, бонусные карты, скидочные программы и учёт серийных номеров пока отсутствуют.</p></article></div>`;
    if(tab==='channels')return `<div class="ops-info-grid">${[['Сайт и интернет-магазин','Автоматическое получение заказов, передача остатков и статусов. Сейчас заказ можно внести в общий реестр вручную.'],['Маркетплейсы','Каталог, остатки, цены, заказы и отчёты площадок. Для синхронизации потребуется отдельная интеграция с API площадки.'],['МойСклад','Связь с вашим аккаунтом, перенос справочников и документов. Текущая версия работает с собственной локальной базой.']].map(([title,text])=>`<article class="ops-info-card"><span class="ops-badge">Не подключено</span><h2>${title}</h2><p>${text}</p></article>`).join('')}</div>${hint('Данные между этим сайтом и внешними сервисами не передаются автоматически. Появление раздела не означает, что подключение уже настроено.')}`;
    return `<div class="ops-info-grid"><article class="ops-info-card"><span class="ops-badge posted">Доступно</span><h2>Учёт операций</h2><ul><li>Товары, цены, контрагенты и склады</li><li>Закупки, продажи и возвраты</li><li>Перемещения и инвентаризация</li><li>Поступления, выплаты и розница</li><li>Производственные операции</li><li>Задачи и журнал изменений</li></ul></article><article class="ops-info-card"><span class="ops-badge info">Отдельное рабочее место</span><h2>План закупок</h2><p>Прогноз спроса, рекомендации закупки и объяснения расчётов по каждому товару.</p><button data-ops-action="forecast">Открыть план закупок</button></article><article class="ops-info-card"><span class="ops-badge">Требует интеграции</span><h2>Внешние сервисы</h2><p>Банк, кассовое оборудование, маркетплейсы, электронный документооборот, бухгалтерия и аккаунт «МойСклад» пока не подключены.</p><p>Данные учёта сохраняются на сервере этого сайта.</p></article></div><section class="ops-panel" style="margin-top:18px"><div class="ops-panel-head"><h2>Следующие возможности</h2></div><div class="ops-panel-body"><ul class="ops-feature-list"><li><span>Финансы: счета, взаиморасчёты, налоговые документы и прибыль</span><span class="ops-badge">Не реализовано</span></li><li><span>Розница: точки, смены, фискальные чеки, маркировка</span><span class="ops-badge">Не реализовано</span></li><li><span>Производство: техкарты, этапы, себестоимость</span><span class="ops-badge">Не реализовано</span></li><li><span>Продажи: резервы, комиссии, воронка, доставки</span><span class="ops-badge">Не реализовано</span></li><li><span>Пользователи, роли, права и разделение компаний</span><span class="ops-badge">Не реализовано</span></li></ul></div></section>`;
  }

  function showDialog(html,narrow=false){const dialog=document.getElementById('ops-dialog');dialog.classList.toggle('narrow',narrow);dialog.innerHTML=html;if(!dialog.open)dialog.showModal();}
  function dialogHeader(title,subtitle=''){return `<div class="ops-dialog-header"><div><p>${opsEsc(subtitle)}</p><h2 id="ops-dialog-title">${opsEsc(title)}</h2></div><button class="ops-close" type="button" data-ops-action="close-dialog" aria-label="Закрыть">×</button></div>`;}
  function field(label,name,value='',type='text',extra=''){return `<label class="field">${opsEsc(label)}<input name="${name}" type="${type}" value="${opsEsc(value)}" ${extra}></label>`;}
  function entityEditor(kind,id){
    const row=id?lookup(kind,id):{},labels={products:'товара',counterparties:'контрагента',warehouses:'склада',tasks:'задачи'};if(!row){alertMessage('Запись не найдена. Обновите страницу.',true);return;}
    let fields='';
    if(kind==='tasks')fields=field('Название','title',row.title||'','text','required maxlength="300"')+field('Срок','due_date',row.due_date||'','date')+`<label class="field">Статус<select name="status"><option value="open" ${row.status!=='done'?'selected':''}>Открыта</option><option value="done" ${row.status==='done'?'selected':''}>Выполнена</option></select></label><label class="field full">Описание<textarea name="description" maxlength="4000">${opsEsc(row.description||'')}</textarea></label>`;
    else{fields=field('Наименование','name',row.name||'','text','required maxlength="300"');if(kind==='products')fields+=field('Артикул','sku',row.sku||'','text','maxlength="100"')+field('Единица измерения','unit',row.unit||'шт','text','required maxlength="30"')+field('Цена по умолчанию','price',row.price||'0','number','min="0" step="0.01" required');if(kind==='counterparties')fields+=`<label class="field">Тип<select name="type">${Object.entries(typeLabels).map(([value,label])=>`<option value="${value}" ${row.type===value?'selected':''}>${label}</option>`).join('')}</select></label>`;}
    const extra=kind==='products'&&id?`<h3>Остатки по складам</h3>${(ops.data.stock||[]).filter(stock=>stock.product_id===id).map(stock=>`<p>${opsEsc(warehouseName(stock.warehouse_id))}: <strong>${opsFmt(stock.quantity)} ${opsEsc(row.unit)}</strong></p>`).join('')||'<p class="muted">Движений по товару ещё нет.</p>'}`:'';
    showDialog(`<form id="ops-entity-form" data-kind="${kind}" data-id="${opsEsc(id||'')}" data-version="${opsEsc(row.version??'')}">${dialogHeader(`${id?'Карточка':'Создание'} ${labels[kind]}`)}<div class="ops-dialog-body"><div class="ops-form-grid">${fields}</div>${extra}<div id="ops-form-error" class="ops-form-error hidden" role="alert"></div></div><div class="ops-dialog-footer"><span class="ops-hint">Изменения сохранятся в учёте сайта.</span><div class="actions"><button type="button" data-ops-action="close-dialog">Закрыть</button><button class="primary" type="submit">Сохранить</button></div></div></form>`,true);
  }
  function lineRow(line={},meta){
    return `<tr>${meta.production?`<td><select name="line_role" class="ops-role" aria-label="Роль позиции"><option value="material" ${line.role!=='output'?'selected':''}>Материал</option><option value="output" ${line.role==='output'?'selected':''}>Продукция</option></select></td>`:''}<td><select name="line_product" required aria-label="Товар">${options(ops.data.products||[],line.product_id,product=>`${product.sku?product.sku+' · ':''}${product.name}`,'Выберите товар')}</select></td><td><input name="line_quantity" type="number" min="${meta===docKinds.inventory?'0':'0.000001'}" step="any" value="${opsEsc(line.quantity??'1')}" required aria-label="Количество"></td><td><input name="line_price" type="number" min="0" step="0.01" value="${opsEsc(line.price??'0')}" required aria-label="Цена"></td><td class="num" data-line-total>${money(lineAmount({quantity:line.quantity??1,price:line.price??0}))}</td><td><button type="button" class="ops-line-remove" data-ops-action="remove-line" aria-label="Удалить позицию">×</button></td></tr>`;
  }
  function documentEditor(kind,id,copy=false){
    const original=id?ops.documents.find(doc=>doc.id===id):null;if(id&&!original){alertMessage('Документ не найден.',true);return;}
    const doc=original?{...original}:{};if(copy){delete doc.id;delete doc.number;delete doc.version;doc.date=today();doc.status='draft';}const meta=docKinds[kind];if(!meta)return;
    const hasPartner=!['transfer','stock_in','write_off','inventory','production','production_order'].includes(kind);
    let fields=field('Номер','number',doc.number||'','text','maxlength="80" placeholder="Присвоится при сохранении"')+field('Дата','date',String(doc.date||today()).slice(0,10),'date','required');
    if(hasPartner)fields+=`<label class="field">Контрагент<select name="counterparty_id">${options(ops.data.counterparties||[],doc.counterparty_id,partner=>partner.name,'Не указан')}</select></label>`;
    if(!meta.payment)fields+=`<label class="field">${kind==='transfer'?'Со склада':'Склад'}<select name="warehouse_id" ${meta.stock?'required':''}>${options(ops.data.warehouses||[],doc.warehouse_id||(ops.data.warehouses?.length===1?ops.data.warehouses[0].id:''),warehouse=>warehouse.name,'Выберите склад')}</select></label>`;
    if(kind==='transfer')fields+=`<label class="field">На склад<select name="target_warehouse_id" required>${options(ops.data.warehouses||[],doc.target_warehouse_id,warehouse=>warehouse.name,'Выберите склад назначения')}</select></label>`;
    if(meta.payment)fields+=field('Сумма','amount',doc.amount||'','number','min="0.01" step="0.01" required');
    const lines=doc.lines?.length?doc.lines:meta.production?[{role:'material'},{role:'output'}]:[{}];
    showDialog(`<form id="ops-document-form" data-kind="${kind}" data-id="${opsEsc(doc.id||'')}" data-version="${opsEsc(doc.version??'')}">${dialogHeader(`${meta.singular}${doc.number?' № '+doc.number:''}`,doc.id?'Редактирование черновика':'Новый документ · черновик')}<div class="ops-dialog-body">${returnHint(kind)}<div class="ops-form-grid">${fields}</div>${!meta.payment?`<h3>Позиции документа</h3>${!(ops.data.products||[]).length?hint('Сначала добавьте товары в разделе «Товары».'):''}<div class="ops-lines"><table><thead><tr>${meta.production?'<th>Роль</th>':''}<th>Товар</th><th>${kind==='inventory'?'Факт':'Количество'}</th><th>Цена</th><th>Сумма</th><th></th></tr></thead><tbody id="ops-lines-body">${lines.map(line=>lineRow(line,meta)).join('')}</tbody></table></div><button type="button" data-ops-action="add-line">+ Добавить позицию</button><div class="ops-total"><span>Итого</span><strong id="ops-document-total">0,00</strong></div>`:''}<label class="field">Комментарий<textarea name="description" maxlength="4000" placeholder="Условия, основание или заметка к документу">${opsEsc(doc.description||'')}</textarea></label><div id="ops-form-error" class="ops-form-error hidden" role="alert"></div></div><div class="ops-dialog-footer"><span class="ops-hint">Сначала сохраните черновик, затем проведите документ.</span><div class="actions"><button type="button" data-ops-action="close-dialog">Отмена</button><button class="primary" type="submit">Сохранить черновик</button></div></div></form>`);updateLineTotal();
  }
  function updateLineTotal(){let total=0n;document.querySelectorAll('#ops-lines-body tr').forEach(row=>{const cents=lineCents({quantity:row.querySelector('[name=line_quantity]').value,price:row.querySelector('[name=line_price]').value});row.querySelector('[data-line-total]').textContent=money(Number(cents)/100);const role=row.querySelector('[name=line_role]');if(!role||role.value==='output')total+=cents;});const target=document.getElementById('ops-document-total');if(target){target.textContent=money(Number(total)/100);const form=document.getElementById('ops-document-form');target.previousElementSibling.textContent=docKinds[form?.dataset.kind]?.production?'Сумма выпуска (без расчёта себестоимости)':'Итого';}}
  function documentDetail(id){
    const doc=ops.documents.find(row=>row.id===id);if(!doc)return;const meta=docKinds[doc.kind];
    const detail=(label,value)=>`<dl><dt>${label}</dt><dd>${opsEsc(value)}</dd></dl>`;
    let effect='Заказ или счёт фиксирует сведения; склад и деньги не изменяются.';
    if(meta.payment)effect='При проведении изменяется денежный баланс.';
    if(meta.stock)effect=['retail_sale','retail_return'].includes(doc.kind)?'При проведении изменяются складские остатки и денежный баланс. Фискальный чек не формируется.':'При проведении изменяются складские остатки.';
    showDialog(`${dialogHeader(`${meta.singular} № ${doc.number}`,`${dateText(doc.date)} · ${statusLabels[doc.status]}`)}<div class="ops-dialog-body"><div class="ops-detail-grid">${detail('Статус',statusLabels[doc.status])}${detail('Контрагент',partnerName(doc.counterparty_id))}${detail('Склад',warehouseName(doc.warehouse_id))}${doc.target_warehouse_id?detail('Склад назначения',warehouseName(doc.target_warehouse_id)):''}</div>${doc.lines?.length?table([...(meta.production?['Роль']:[]),'Товар','Количество','Цена','Сумма'],doc.lines.map(line=>`<tr>${meta.production?`<td>${line.role==='output'?'Продукция':'Материал'}</td>`:''}<td>${opsEsc(productName(line.product_id))}</td><td class="num">${opsFmt(line.quantity)} ${opsEsc(lookup('products',line.product_id)?.unit||'')}</td><td class="num">${money(line.price)}</td><td class="num">${money(lineAmount(line))}</td></tr>`).join('')):''}<div class="ops-total"><span>${meta.production?'Сумма выпуска (без расчёта себестоимости)':'Итого'}</span><strong>${money(docTotal(doc))}</strong></div>${doc.description?`<p class="ops-detail-note">${opsEsc(doc.description)}</p>`:''}${hint(effect)}${returnHint(doc.kind)}<div id="ops-form-error" class="ops-form-error hidden" role="alert"></div></div><div class="ops-dialog-footer"><div class="actions"><button data-ops-action="copy-document" data-id="${opsEsc(id)}">Создать копию</button>${doc.status==='draft'?`<button data-ops-action="edit-document" data-id="${opsEsc(id)}">Редактировать</button>`:''}</div><div class="actions">${doc.status==='draft'?`<button class="primary" data-ops-action="transition" data-id="${opsEsc(id)}" data-version="${opsEsc(doc.version)}" data-transition="post">Провести</button>`:''}${doc.status==='posted'?`<button data-ops-action="transition" data-id="${opsEsc(id)}" data-version="${opsEsc(doc.version)}" data-transition="cancel">Отменить проведение</button>`:''}<button data-ops-action="close-dialog">Закрыть</button></div></div>`);
  }
  function formError(message){const box=document.getElementById('ops-form-error');if(box){box.textContent=message;box.classList.remove('hidden');box.scrollIntoView({block:'nearest'});}else alertMessage(message,true);}

  async function onSubmit(event){
    event.preventDefault();const form=event.target;if(!['ops-entity-form','ops-document-form'].includes(form.id))return;
    const button=form.querySelector('[type=submit]');if(button.disabled||form.dataset.saved==='true')return;button.disabled=true;
    const recordKind=form.id==='ops-entity-form'?form.dataset.kind:'documents';let committed=false,result;
    try{
      const values=Object.fromEntries(new FormData(form));
      if(form.id==='ops-entity-form'){
        const kind=form.dataset.kind,previous=form.dataset.id?lookup(kind,form.dataset.id):{};
        result=await api('/api/operations/entity',{kind,payload:{...previous,...values,...(form.dataset.id?{id:form.dataset.id,version:displayedVersion(form.dataset.version)}:{})}});
      }else{
        const meta=docKinds[form.dataset.kind],payload={kind:form.dataset.kind,number:values.number,date:values.date,counterparty_id:values.counterparty_id||'',warehouse_id:values.warehouse_id||'',target_warehouse_id:values.target_warehouse_id||'',description:values.description||''};
        if(form.dataset.id){payload.id=form.dataset.id;payload.version=Number(form.dataset.version);}
        if(meta.payment){payload.amount=values.amount;payload.lines=[];}
        else{payload.lines=[...form.querySelectorAll('#ops-lines-body tr')].map(row=>({product_id:row.querySelector('[name=line_product]').value,quantity:row.querySelector('[name=line_quantity]').value,price:row.querySelector('[name=line_price]').value,...(meta.production?{role:row.querySelector('[name=line_role]').value}:{})}));if(!payload.lines.length)throw Error('Добавьте хотя бы одну позицию.');}
        result=await api('/api/operations/document',payload);
      }
      committed=true;form.dataset.saved='true';rememberRecord(recordKind,result);document.getElementById('ops-dialog').close();
      if(!await load(true)){rememberRecord(recordKind,result);renderContent();savedRefreshError();return;}
      alertMessage(form.id==='ops-entity-form'?'Запись сохранена.':'Черновик сохранён. Проведите документ, чтобы применить операцию.');
      if(form.id==='ops-document-form'){const saved=result?.document||result;if(saved?.id)documentDetail(saved.id);}
    }catch(error){if(committed){document.getElementById('ops-dialog').close();savedRefreshError();}else formError(error.message);}
    finally{button.disabled=committed;}
  }

  async function onClick(event){
    const button=event.target.closest('button,[data-ops-action]');if(!button||button.disabled)return;
    if(button.dataset.opsModule){await navigate(button.dataset.opsModule);return;}
    if(button.dataset.opsTab){await navigate(ops.module,button.dataset.opsTab);return;}
    const action=button.dataset.opsAction,id=button.dataset.id;let mutation=false,committed=false;
    try{
      if(action==='refresh'){alertMessage('');await load();}
      else if(action==='forecast'){showMode('plan');}
      else if(action==='all-documents')await navigate('company','documents');
      else if(action==='all-tasks')await navigate('tasks');
      else if(action==='go-products')await navigate('products');
      else if(action==='go-stock')await navigate('warehouse','stock');
      else if(action==='prev'||action==='next'){ops.page+=action==='next'?1:-1;renderContent();}
      else if(action==='new-document')documentEditor(button.dataset.kind||(ops.tab==='online_orders'?'sales_order':ops.tab));
      else if(action==='initial-stock')documentEditor('stock_in');
      else if(action==='open-document')documentDetail(id);
      else if(action==='edit-document'||action==='copy-document'){const doc=ops.documents.find(row=>row.id===id);documentEditor(doc.kind,id,action==='copy-document');}
      else if(action==='new-entity')entityEditor(button.dataset.kind||ops.tab);
      else if(action==='open-entity')entityEditor(ops.tab,id);
      else if(action==='stock-product')entityEditor('products',id);
      else if(action==='new-task')entityEditor('tasks');
      else if(action==='open-task')entityEditor('tasks',id);
      else if(action==='close-dialog')document.getElementById('ops-dialog').close();
      else if(action==='add-line'){const form=document.getElementById('ops-document-form');document.getElementById('ops-lines-body').insertAdjacentHTML('beforeend',lineRow({},docKinds[form.dataset.kind]));updateLineTotal();}
      else if(action==='remove-line'){button.closest('tr').remove();updateLineTotal();}
      else if(action==='transition'){
        const expectedVersion=Number(button.dataset.version);if(!Number.isSafeInteger(expectedVersion)||expectedVersion<1)throw Error('Версия документа недоступна. Обновите данные и откройте документ заново.');
        mutation=true;button.disabled=true;const result=await api('/api/operations/transition',{id,action:button.dataset.transition,expected_version:expectedVersion});
        committed=true;rememberRecord('documents',result);document.getElementById('ops-dialog').close();
        if(!await load(true)){rememberRecord('documents',result);renderContent();savedRefreshError();return;}
        documentDetail(id);alertMessage(button.dataset.transition==='post'?'Документ проведён. Учёт обновлён.':'Документ отменён. Учёт пересчитан.');
      }else if(action==='toggle-task'){
        const version=displayedVersion(button.dataset.version),task=lookup('tasks',id);
        mutation=true;button.disabled=true;const result=await api('/api/operations/entity',{kind:'tasks',payload:{...task,version,status:button.dataset.status==='done'?'open':'done'}});
        committed=true;rememberRecord('tasks',result);
        if(!await load(true)){rememberRecord('tasks',result);renderContent();savedRefreshError();return;}
        alertMessage('Статус задачи сохранён.');
      }else if(action==='import-report'){
        mutation=true;button.disabled=true;const result=await api('/api/operations/import-report',{});committed=true;
        if(!await load(true)){savedRefreshError();return;}
        alertMessage(`Импорт завершён: добавлено ${opsFmt(result.created||0)}, обновлено ${opsFmt(result.updated||0)}, пропущено ${opsFmt(result.skipped||0)}. Остатки не изменены.`);
      }else if(action==='export')exportCurrent();
    }catch(error){if(committed){document.getElementById('ops-dialog')?.close();savedRefreshError();}else{if(document.getElementById('ops-dialog')?.open)formError(error.message);else alertMessage(error.message,true);if(action==='toggle-task')renderContent();}}
    finally{if(mutation)button.disabled=committed;}
  }

  function exportCurrent(){
    let headers,rows;
    if(docKinds[ops.tab]||['documents','online_orders'].includes(ops.tab)){headers=['Тип','Номер','Дата','Контрагент','Склад','Сумма','Статус'];rows=filteredDocs(docKinds[ops.tab]?[ops.tab]:ops.tab==='online_orders'?['sales_order']:null).map(doc=>[docName(doc.kind),doc.number,doc.date,partnerName(doc.counterparty_id),warehouseName(doc.warehouse_id),docTotal(doc),statusLabels[doc.status]]);}
    else if(ops.tab==='stock'){headers=['Артикул','Товар','Склад','Количество','Единица'];rows=stockRows().map(row=>[row.sku,row.product_name,warehouseName(row.warehouse_id),row.quantity,row.unit]);}
    else if(ops.tab==='products'){headers=['Артикул','Наименование','Единица','Цена','Источник'];rows=ops.data.products.filter(row=>searchMatch([row.name,row.sku])).map(row=>[row.sku,row.name,row.unit,row.price,productOrigin(row)]);}
    else if(ops.tab==='counterparties'){headers=['Наименование','Тип'];rows=ops.data.counterparties.filter(row=>searchMatch([row.name,typeLabels[row.type]])).map(row=>[row.name,typeLabels[row.type]]);}
    else if(ops.tab==='warehouses'){headers=['Название'];rows=ops.data.warehouses.filter(row=>searchMatch([row.name])).map(row=>[row.name]);}
    else if(ops.tab==='tasks'){headers=['Название','Описание','Срок','Статус'];rows=ops.data.tasks.filter(row=>(!ops.status||row.status===ops.status)&&searchMatch([row.title,row.description])).map(row=>[row.title,row.description,row.due_date,statusLabels[row.status]]);}
    else if(ops.tab==='journal'){headers=['Дата','Действие','Тип','Источник','Описание'];rows=ops.journal.filter(row=>searchMatch([row.description,row.action,row.entity_type,row.created_at,journalSource(row)])).map(row=>[row.created_at,row.action,row.entity_type,journalSource(row),row.description]);}
    else if(ops.tab==='cashflow'){headers=['Документ','Номер','Дата','Контрагент','Поступление','Выплата'];rows=moneyDocs().map(doc=>[docName(doc.kind),doc.number,doc.date,partnerName(doc.counterparty_id),['payment_in','retail_sale'].includes(doc.kind)?docTotal(doc):0,['payment_out','retail_return'].includes(doc.kind)?docTotal(doc):0]);}
    else if(ops.tab==='sales_report'||ops.tab==='retail_report'){headers=['Артикул','Товар','Количество нетто','Ед.','Сумма'];rows=salesRows(ops.tab==='retail_report').map(row=>[row.sku,row.name,row.quantity,row.unit,row.amount]);}
    else if(ops.tab==='counterparty_report'){headers=['Контрагент','Продажи','Закупки','Получено','Выплачено'];rows=counterpartyRows().map(row=>[row.name,row.sales,row.purchases,row.income,row.expense]);}
    else if(ops.tab==='production_report'){headers=['Документ','Дата','Продукция','Склад','Количество'];rows=productionRows().map(row=>[row.number,row.date,productName(row.product_id),warehouseName(row.warehouse_id),row.quantity]);}
    else return;
    const csvValue=value=>{let text=String(value??'');if(/^[=+@\-\t\r]/.test(text)&&!/^-[\d.]+$/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
    const csv='\uFEFF'+[headers,...rows].map(row=>row.map(csvValue).join(';')).join('\r\n');
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=`${ops.tab}-${today()}.csv`;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
})();
