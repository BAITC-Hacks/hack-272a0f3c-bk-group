"""Execute shipped scripts in a small DOM fixture to regress asynchronous state bugs.

This supplements the browser checks: it exercises real handlers with controlled
out-of-order responses, without making any network or AI-provider calls.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
FIXTURE = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
class Element{
  constructor(id=''){this.id=id;this.value='';this.disabled=false;this.dataset={};this.children=[];this.options=[];this.style={};this.textContent='';this.open=false;const values=new Set();this.classList={add:x=>values.add(x),remove:x=>values.delete(x),contains:x=>values.has(x),toggle:(x,on)=>on?values.add(x):values.delete(x)};}
  set innerHTML(value){this._html=value;this.children=[];this.options=[...value.matchAll(/<option(?:\s+value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map(m=>({value:m[1]??m[2]}));if(this.options.length)this.value=this.options[0].value;}
  get innerHTML(){return this._html||'';}
  append(...values){this.children.push(...values);values.forEach(v=>{if(v&&typeof v==='object')v.parent=this;});}
  before(){} focus(){} scrollIntoView(){} setAttribute(){} addEventListener(){} insertAdjacentHTML(){}
  replaceChildren(...values){this.children=[];this.append(...values);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
  querySelector(s){return get('#'+this.id+' '+s);}
  showModal(){this.open=true;}close(){this.open=false;}
}
function get(s){if(!elements.has(s))elements.set(s,new Element(s.replace(/^#/,'')));return elements.get(s);}
const document={querySelector:get,querySelectorAll:s=>s.startsWith('#')?s.split(',').map(get):[],createElement:t=>new Element(t),createTextNode:t=>({textContent:t})};
const context=vm.createContext({document,assert,console,AbortController,setTimeout,clearTimeout,setInterval:()=>{},devicePixelRatio:1,fetch:()=>{throw Error('Unexpected network request')},URL,FormData:class{}});
const html=fs.readFileSync(ROOT+'/web/index.html','utf8');
const main=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('poll();setInterval(poll,2000);','');
vm.runInContext(main,context);
vm.runInContext(fs.readFileSync(ROOT+'/web/assistant.js','utf8'),context);
'''


class FrontendContractTests(unittest.TestCase):
    def run_js(self, script, operations=False):
        if not Path(NODE).exists():
            self.skipTest('Node runtime is required for frontend state tests')
        source = 'const ROOT='+json.dumps(str(ROOT))+';\n'+FIXTURE
        if operations:
            source += r'''
context.window=context;
document.getElementById=id=>get('#'+id);
let operationsScript=fs.readFileSync(ROOT+'/web/operations.js','utf8');
operationsScript=operationsScript.replace(/\}\)\(\);\s*$/, 'globalThis.opsHarness={ops,onClick,documentDetail,setLoad(fn){load=fn;}};\n})();');
vm.runInContext(operationsScript,context);
'''
        source += '\nvm.runInContext('+json.dumps('(async()=>{'+script+'})()')+',context).catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run([NODE, '-e', source], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_new_dataset_clears_old_history_and_resets_period(self):
        self.run_js("""
            report={dataset:'new'};historicalDataset='old';historical={old:true};
            $('#history-result').innerHTML='OLD RESULT';$('#history-start').value='2024-01';$('#history-end').value='2025-12';
            api=async()=>({dataset:'new',result:null,start:'2025-07',end:'2026-08'});
            await loadHistorical();
            assert.equal(historical,null);assert.equal($('#history-start').value,'2025-07');
            assert.equal($('#history-end').value,'2026-08');assert(!$('#history-result').innerHTML.includes('OLD RESULT'));
        """)

    def test_same_dataset_preserves_user_history_dates(self):
        self.run_js("""
            report={dataset:'same'};historicalDataset='same';
            $('#history-start').value='2025-03';$('#history-end').value='2025-11';
            api=async()=>({dataset:'same',result:null,start:'2024-07',end:'2026-08'});
            await loadHistorical();assert.equal($('#history-start').value,'2025-03');assert.equal($('#history-end').value,'2025-11');
        """)

    def test_empty_history_never_renders_nan_year(self):
        self.run_js("historical={metrics:[]};renderHistorical();assert(!$('#history-result').innerHTML.includes('NaN'));assert($('#history-result').innerHTML.includes('нет достаточной истории'));")

    def test_pagination_clamps_and_search_ignores_outer_whitespace(self):
        self.run_js("""
            report={rows:[{key:'a',name:'Lamp',sku:'A',supplier:'S',unit:'шт',status:'blocked',blocks:['missing'],warnings:[]}]};
            page=9;$('#search').value=' lamp ';renderRows();
            assert.equal(page,0);assert.equal($('#page-label').textContent,'1–1 из 1');assert($('#next').disabled);
        """)

    def test_poll_requests_cannot_overlap(self):
        self.run_js("""
            let release,count=0;api=()=>{count++;return new Promise(resolve=>release=resolve);};
            const first=poll();await poll();assert.equal(count,1);release({busy:true,loaded:false,message:'Working'});await first;assert.equal(pollPending,false);
        """)

    def test_double_submit_starts_only_one_job_and_unlocks_after_error(self):
        self.run_js("""
            let reject,count=0;poll=async()=>{};api=()=>{count++;return new Promise((resolve,fail)=>reject=fail);};
            const first=job('/api/calculate',{});await job('/api/calculate',{});
            assert.equal(count,1);assert($('#upload').disabled);reject(Error('Rejected'));await first;
            assert.equal(jobSubmitting,false);assert.equal($('#upload').disabled,false);assert.equal($('#error').textContent,'Rejected');
        """)

    def test_settings_submission_identifies_the_reviewed_report(self):
        self.run_js("""
            report={dataset:'reviewed-data',instance_id:'reviewed-process'};reportRevision=7;let submitted;
            job=(path,body)=>{submitted={path,body};};
            const elements={lead_days:{value:'30'},review_days:{value:'14'},service:{value:'0.9'},max_snapshot_age:{value:'0'},scenario:{value:'full'}};
            for(const k of ['parameters_confirmed','blank_months_zero','constraints_confirmed','incoming_confirmed','reserve_policy_confirmed'])elements[k]={checked:false};
            $('#settings-form').onsubmit({preventDefault(){},target:{elements}});
            assert.equal(submitted.path,'/api/calculate');assert.equal(submitted.body.expected_report_revision,7);assert.equal(submitted.body.expected_dataset,'reviewed-data');assert.equal(submitted.body.expected_instance_id,'reviewed-process');assert.equal(submitted.body.max_snapshot_age,0);
        """)

    def test_closed_dialog_does_not_reopen_from_late_product_response(self):
        self.run_js("""
            let release;api=()=>new Promise(resolve=>release=resolve);
            const pending=detail('old');$('#close-detail').onclick();release({name:'Stale product'});await pending;
            assert.equal($('#detail').open,false);assert.equal(currentKey,null);
        """)

    def test_failed_job_keeps_successful_result_and_reports_error(self):
        self.run_js("""
            report={dataset:'same'};reportRevision=1;historyRevision=1;historicalDataset='same';wasBusy=true;
            let renders=0;render=()=>renders++;api=async path=>{assert.equal(path,'/api/status');return {busy:false,loaded:true,report_revision:1,history_revision:1,message:'Failed',error:'Bad archive'};};
            await poll();assert.equal(renders,0);assert.equal($('#error').textContent,'Bad archive');assert($('#status').innerHTML.includes('последний успешный'));
        """)

    def test_other_tab_report_revision_refreshes_current_page(self):
        self.run_js("""
            report={dataset:'same'};reportRevision=1;historyRevision=1;historicalDataset='same';
            let renders=0;render=()=>renders++;renderSimulation=()=>{};
            api=async path=>path==='/api/status'?{busy:false,loaded:true,report_revision:2,history_revision:1,message:'Ready'}:{dataset:'same',report_revision:2};
            await poll();assert.equal(renders,1);assert.equal(reportRevision,2);
        """)

    def test_connection_failure_invalidates_cached_revisions_before_recovery(self):
        self.run_js("""
            report={dataset:'same'};reportRevision=1;historyRevision=1;
            api=async()=>{throw Error('Server restarting');};await poll();
            assert.equal(reportRevision,null);assert.equal(historyRevision,null);assert.equal(pollPending,false);
        """)

    def test_recovered_connection_clears_automatic_error(self):
        self.run_js("""
            api=async()=>{throw Error('Server disconnected');};await poll();
            assert.equal(errorSource,'connection');assert.equal($('#error').classList.contains('hidden'),false);
            api=async()=>({busy:false,loaded:false,message:'Connected'});await poll();
            assert.equal(errorSource,null);assert.equal($('#error').textContent,'');assert($('#error').classList.contains('hidden'));
        """)

    def test_restarted_server_clears_old_failed_job_message(self):
        self.run_js("""
            api=async()=>({busy:false,loaded:false,instance_id:'failed-process',message:'Failed',error:'Broken archive'});await poll();
            assert.equal(errorSource,'server');assert.equal($('#error').textContent,'Broken archive');
            api=async()=>({busy:false,loaded:false,instance_id:'new-process',message:'Ready',error:null});await poll();
            assert.equal(serverInstance,'new-process');assert.equal(errorSource,null);assert($('#error').classList.contains('hidden'));
        """)

    def test_poll_recovery_preserves_current_validation_or_stale_mutation_error(self):
        self.run_js("""
            for(const message of ['Select an archive','The reviewed calculation changed']){
                error(message);api=async()=>{throw Error('Temporary connection failure');};await poll();
                assert.equal(errorSource,'user');assert.equal($('#error').textContent,message);
                api=async()=>({busy:false,loaded:false,message:'Ready',error:null});await poll();
                assert.equal(errorSource,'user');assert.equal($('#error').textContent,message);assert.equal($('#error').classList.contains('hidden'),false);
            }
        """)

    def test_fast_server_restart_refreshes_even_when_revision_number_repeats(self):
        self.run_js("""
            report={dataset:'same',instance_id:'old-process'};reportRevision=1;historyRevision=1;historicalDataset='same';serverInstance='old-process';
            let renders=0,loads=0;render=()=>renders++;renderSimulation=()=>{};loadHistorical=async()=>loads++;
            api=async path=>path==='/api/status'?{busy:false,loaded:true,report_revision:1,history_revision:1,instance_id:'new-process',message:'Ready'}:{dataset:'same',report_revision:1,instance_id:'new-process'};
            await poll();assert.equal(renders,1);assert.equal(loads,1);assert.equal(serverInstance,'new-process');assert.equal(report.instance_id,'new-process');
        """)

    def test_history_completion_does_not_overwrite_unsaved_settings(self):
        self.run_js("""
            report={dataset:'same'};reportRevision=1;historyRevision=1;historicalDataset='same';wasBusy=true;
            let renders=0,historyLoads=0;render=()=>renders++;loadHistorical=async()=>historyLoads++;
            api=async path=>({busy:false,loaded:true,report_revision:1,history_revision:2,message:'Ready'});
            await poll();assert.equal(renders,0);assert.equal(historyLoads,1);
        """)

    def test_chat_discards_response_if_report_changed_in_flight(self):
        self.run_js("""
            report={dataset:'data',report_revision:1,rows:[]};syncChatProducts();
            chatProviders=[{id:'openai',ready:true}];$('#chat-provider').value='openai';updateChatAvailability();$('#chat-question').value='Question';
            let release;api=()=>new Promise(resolve=>release=resolve);
            const pending=$('#chat-form').onsubmit({preventDefault(){}});
            report={dataset:'data',report_revision:2,rows:[]};syncChatProducts();release({answer:'OBSOLETE ANSWER'});await pending;
            assert.equal(chatHistory.length,0);assert.equal(chatPending,false);assert.equal($('#chat-question').value,'Question');assert($('#chat-error').textContent.includes('данные изменились'));
        """)

    def test_late_configuration_cannot_switch_pending_chat_provider(self):
        self.run_js("""
            $('#chat-provider').value='nvidia';let release;api=()=>new Promise(resolve=>release=resolve);
            const pending=loadChatConfiguration();chatPending=true;release({providers:[{id:'openai',ready:true}]});await pending;
            assert.equal($('#chat-provider').value,'nvidia');
        """)

    def test_failed_chat_restores_controls_and_retains_question_for_retry(self):
        self.run_js("""
            chatProviders=[{id:'openai',ready:true}];$('#chat-provider').value='openai';updateChatAvailability();$('#chat-question').value='Retry question';
            api=async()=>{throw Error('Provider timeout');};await $('#chat-form').onsubmit({preventDefault(){}});
            assert.equal(chatPending,false);assert.equal($('#chat-send').disabled,false);assert.equal($('#chat-question').disabled,false);
            assert.equal($('#chat-question').value,'Retry question');assert.equal(chatHistory.length,0);assert.equal($('#chat-error').textContent,'Provider timeout');
        """)

    def test_malformed_json_has_readable_retry_error(self):
        self.run_js("fetch=async()=>({ok:false,json:async()=>{throw new SyntaxError('HTML');}});await assert.rejects(()=>api('/test'),/некорректный ответ/);")

    def test_fetch_timeout_recovers_instead_of_hanging(self):
        self.run_js("fetch=(_,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));await assert.rejects(()=>api('/test',undefined,5),/Время ожидания/);")

    def test_zero_is_distinct_from_missing_and_html_is_escaped(self):
        self.run_js("assert.equal(fmt(null),'—');assert.equal(fmt(0),'0');assert.equal(esc('<img src=x onerror=alert(1)>'),'&lt;img src=x onerror=alert(1)&gt;');")

    def test_fractional_quantities_remain_visible_instead_of_rounding_to_zero(self):
        self.run_js("""
            assert.equal(fmt(0.0004),'0,0004');assert.equal(fmt(0.001),'0,001');assert.equal(fmt(0.000001),'0,000001');
            assert.equal(fmt(1.234567),'1,234567');assert.equal(fmt(-0.0004),'-0,0004');
            for(const value of [1e-7,4e-10,-2e-9,Number.MIN_VALUE]){
                const display=fmt(value);assert(/[eE]/.test(display),display);assert(!/^[-+]?0(?:[,.]0*)?(?:[eE].*)?$/.test(display));
            }
            assert.equal(fmt(0),'0');assert.equal(fmt(-0),'0');assert.equal(fmt(null),'—');assert.equal(fmt(undefined),'—');
            assert.equal(fmt(29.912345,2),'29,91');assert.equal(fmt(29.912345,1),'29,9');
        """)

    def test_product_card_allows_micro_pack_and_displays_small_order(self):
        self.run_js("""
            report={as_of:'2026-01-01',dataset:'tiny'};chart=()=>{};
            api=async()=>({supplier:'S',sku:'tiny',name:'Fractional product',unit:'кг',status:'order',blocks:[],warnings:[],
                examples:[],shipments:[],sources:{},history:[],pack_multiple:0.000001,quantity:0.0004});
            await detail('tiny');const html=$('#detail-content').innerHTML;
            assert(html.includes('name="pack_multiple" type="number" min="0.000001"'));
            assert(html.includes('<b>0,0004</b>'));assert(html.includes('<dd>0,000001</dd>'));
        """)

    def test_document_transition_sends_displayed_version_even_after_background_refresh(self):
        self.run_js("""
            opsHarness.ops.data={products:[],counterparties:[],warehouses:[]};
            for(const [status,action] of [['draft','post'],['posted','cancel']]){
                opsHarness.ops.documents=[{id:'doc',kind:'receipt',number:'R1',date:'2026-01-01',status,version:7,lines:[]}];
                opsHarness.documentDetail('doc');const html=$('#ops-dialog').innerHTML;
                const captured=html.match(new RegExp('data-version="([0-9]+)" data-transition="'+action+'"'));
                assert(captured,'The visible action must carry its document version');
                opsHarness.ops.documents[0].version=8;let request;
                api=async(path,body)=>{request={path,body};throw Error('Version conflict');};
                const button={disabled:false,dataset:{opsAction:'transition',id:'doc',transition:action,version:captured[1]}};
                await opsHarness.onClick({target:{closest:()=>button}});
                assert.equal(request.path,'/api/operations/transition');assert.equal(request.body.expected_version,7);
                assert.equal(button.disabled,false);assert.equal($('#ops-form-error').textContent,'Version conflict');
            }
        """, operations=True)

    def test_document_transition_prevents_repeat_click_until_request_finishes(self):
        self.run_js("""
            let reject,calls=0;api=()=>{calls++;return new Promise((resolve,fail)=>reject=fail);};
            const button={disabled:false,dataset:{opsAction:'transition',id:'doc',transition:'post',version:'1'}};
            const event={target:{closest:()=>button}};const first=opsHarness.onClick(event);await opsHarness.onClick(event);
            assert.equal(calls,1);assert.equal(button.disabled,true);reject(Error('Not enough stock'));await first;
            assert.equal(button.disabled,false);assert.equal($('#ops-alert').textContent,'Not enough stock');
        """, operations=True)

    def test_document_transition_rejects_missing_version_without_mutating(self):
        self.run_js("""
            let calls=0;api=async()=>calls++;
            for(const version of [undefined,'','1.5','NaN']){
                const button={disabled:false,dataset:{opsAction:'transition',id:'doc',transition:'post',version}};
                await opsHarness.onClick({target:{closest:()=>button}});assert.equal(button.disabled,false);
            }
            assert.equal(calls,0);assert($('#ops-alert').textContent.includes('Версия документа недоступна'));
        """, operations=True)


if __name__ == '__main__':
    unittest.main()
