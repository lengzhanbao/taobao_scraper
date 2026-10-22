"use strict";
// Exercise the real dashboard against a synthetic DOM and API, with no network.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const elements = new Map();
const intervals=[];
let failState=false;
let hangState=false;
let hangAction=false;
let urlStatsCounts={1:1,2:1};
let urlRowOverride=null;
let holdNextUrlStats=false;
let releaseHeldUrlStats=null;
let activeElement=null;
class Element {
  constructor(tag="div") {
    this.tag=tag;this.tagName=tag.toUpperCase();this.isConnected=true;this.disabled=false;this.children=[];this.dataset={};this.listeners={};this.attributes={};
    this.classList={toggle(name,value){this[name]=value===undefined?!this[name]:Boolean(value);},contains(name){return Boolean(this[name]);}};this.value="";this._text="";
  }
  set id(value) { this._id=value;elements.set(value,this); }
  get id() { return this._id; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text="";this.children=children; }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(key,listener) { this.listeners[key]=listener; }
  querySelector(selector) { return this.children.find(child=>String(child.dataset.id)===selector.match(/data-id="(\d+)"/)[1]); }
  closest() { return this.tag==="button"?this:null; }
}
const instance = (id,target,completed,recorded=completed)=>({id,port:9222+id,urls_file:`urls_${id}.txt`,
  enabled:target>0,segments:target||3,max_minutes:null,cooldown_minutes:null,room_gap_seconds:null,page_refresh_seconds:null,rooms:target?1:0,complete:target&&completed===target?1:0,
  recorded,remaining:target-completed,target_segments:target,completed_segments:completed,unverified_rooms:0,progress_source:"本次运行"});
const fixture = {token:"synthetic",version:"2.7.0-local-panel",code_root:"synthetic",preflight:null,
  settings:{study_root:"synthetic",python:"python",edge:"edge",ffmpeg:"ffmpeg",ffprobe:"ffprobe",
    max_minutes:1,cooldown_minutes:120,batch_rooms:1,launch_gap_seconds:0,room_gap_seconds:0,page_refresh_seconds:300,status_refresh_seconds:5,
    checkpoint_seconds:30,listen_timeout_seconds:2,scan_timeout_seconds:30,connect_timeout_seconds:30,captcha_timeout_seconds:180,
    stall_timeout_seconds:60,max_record_attempts:3,failure_cooldown_minutes:120,max_room_failures:3,max_collected:800,instances:[]},
  instances:[instance(1,3,1),instance(2,1,1,4),instance(3,0,0),instance(4,0,0),instance(5,0,0)],
  totals:{rooms:2,completed_rooms:1,recorded_segments:5,valid_segments:2,archived_segments:1,remaining_segments:2,target_segments:4,completed_segments:2,unverified_rooms:0,
    digital_markers:{all_true:1,not_all_true:1,missing:0}},
  jobs:[{instance_id:1,alive:true,phase:"paused",pause_supported:true,pause_requested:true,status:{}},
    {instance_id:2,alive:true,phase:"recording",pause_supported:true,pause_requested:false,
      status:{live_id:"123",segment_index:1,elapsed_seconds:45,planned_duration_seconds:60,
        latest_response_t:1760000000,last_persisted_t:1760000002,last_persisted_sequence:42,
        unattributed_response_count:3,journal_error:true,last_scan_result:"non_digital",
        pending_rooms:2,pending_archive_reason:"自然结束尾批归档失败，保留原始数据"}}]};
fixture.settings.instances=fixture.instances;
fixture.default_settings=JSON.parse(JSON.stringify(fixture.settings));
fixture.runtime_fields=Object.fromEntries(Object.entries({room_gap_seconds:[0,0,3600],page_refresh_seconds:[300,15,3600],status_refresh_seconds:[5,2,60],checkpoint_seconds:[30,5,600],listen_timeout_seconds:[2,1,30],scan_timeout_seconds:[30,5,180],connect_timeout_seconds:[30,5,180],captcha_timeout_seconds:[180,10,1800],stall_timeout_seconds:[60,10,600],max_record_attempts:[3,1,10],failure_cooldown_minutes:[120,1,1440],max_room_failures:[3,1,20],max_collected:[800,10,2000]}).map(([key,[value,min,max]])=>[key,{default:value,min,max,env:`LIVE_${key.toUpperCase()}`} ]));
const requests=[];
const context=vm.createContext({console,AbortController,URL,
  setTimeout(callback,ms){return setTimeout(callback,(hangState&&ms===10000)||(hangAction&&ms===90000)?5:ms);},clearTimeout,
  setInterval(callback,ms){intervals.push({callback,ms});},window:{confirm:()=>true},document:{
  get activeElement(){return activeElement;},
  getElementById(id){if(!elements.has(id)){const element=new Element();element.id=id;}return elements.get(id);},
  createElement(tag){return new Element(tag);},addEventListener(){}
},fetch:async(route,options)=>{
  requests.push({route,options});
  if((route==="/api/state"&&hangState)||(options?.method==="POST"&&hangAction))return new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>{const error=new Error('synthetic abort');error.name='AbortError';reject(error);},{once:true});
  });
  if(route==="/api/state"){if(failState)throw new Error("synthetic offline");return {ok:true,json:async()=>fixture};}
  if(route==="/api/settings/validate")return {ok:true,json:async()=>JSON.parse(options.body)};
  if(route.startsWith('/api/urls')) {
    const url=new URL(route,'http://localhost'),instanceId=Number(url.searchParams.get('instance')||1);
    const makeDocument=id=>({instance_id:id,path:`synthetic/urls_${id}.txt`,revision:id===1?'abc':'def',
      active_text:`https://tbzb.taobao.com/live?liveId=${id===1?'123':'222'}`,
      rows:[urlRowOverride?{...urlRowOverride,live_id:id===1?'123':'222'}:
        {live_id:id===1?'123':'222',count:urlStatsCounts[id]||1,target:3,valid_count:null,archived_count:0,validation_state:'未核验'}],
      message:'saved',correction_id:'synthetic-correction'});
    if(!options?.method&&holdNextUrlStats) {
      holdNextUrlStats=false;
      return new Promise(resolve=>{releaseHeldUrlStats=()=>resolve({ok:true,json:async()=>makeDocument(instanceId)});});
    }
    return {ok:true,json:async()=>makeDocument(instanceId)};
  }
  return {ok:true,json:async()=>({message:"synthetic action accepted"})};
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/control/web/app.js"),"utf8"),context);
for(const id of ["correct-live-id","correct-count","correct-reason","correct-confirmed","correct-submit",
                 "connection-state","last-sync","verified-note"]) context.document.getElementById(id);
async function main() {
  const feedbackButton=new Element("button");feedbackButton.textContent="保存操作";feedbackButton.disabled=true;
  activeElement=feedbackButton;
  const successFeedback=vm.runInContext('action(async()=>{await Promise.resolve()})',context);
  assert.equal(feedbackButton.textContent,"处理中…");assert.equal(feedbackButton.disabled,true);
  await successFeedback;
  assert.equal(feedbackButton.textContent,"保存操作");assert.equal(feedbackButton.disabled,true);
  feedbackButton.disabled=false;
  const failureFeedback=vm.runInContext('action(async()=>{throw new Error("synthetic failure")})',context);
  assert.equal(feedbackButton.textContent,"处理中…");assert.equal(feedbackButton.disabled,true);
  await failureFeedback;
  assert.equal(feedbackButton.textContent,"保存操作");assert.equal(feedbackButton.disabled,false);
  activeElement=null;
  assert.deepEqual(["accepted","pending","applied","failed","superseded","timed_out"].map(status=>
    vm.runInContext(`timelineStatusName("${status}")`,context)),
    ["已接受","执行中","worker 已确认","失败","被取代","超时未确认"]);
  vm.runInContext(`timelineReset("synthetic-run");timelineAdd({event_id:"e1",time_utc:"2026-10-09T00:00:00Z",instance_id:1,operation:"stop",operation_id:"op-stop",command_seq:9,status:"applied",source:"worker",details:{message:"完成当前段后停止"}});timelineRender()`,context);
  assert.match(elements.get("timeline-events").textContent,/worker 已确认/);
  assert.match(elements.get("timeline-events").textContent,/操作 op-stop/);
  assert.match(elements.get("timeline-events").textContent,/命令序号 9/);
  assert.match(elements.get("timeline-events").textContent,/完成当前段后停止/);
  await vm.runInContext("refresh()",context);
  assert.equal(elements.get("setting-room_gap_seconds").value,0);
  assert.equal(elements.get("setting-room_gap_seconds").min,0);
  assert.equal(elements.get("instance-1-room_gap_seconds").value,"");
  elements.get("setting-room_gap_seconds").value="21";
  elements.get("instance-1-room_gap_seconds").value="9";
  const editedSettings=vm.runInContext("settingsFromForm()",context);
  assert.equal(editedSettings.room_gap_seconds,21);
  assert.equal(editedSettings.instances[0].room_gap_seconds,9);
  assert.match(elements.get("run-summary").textContent,/下次启动/);
  const statusDetail=elements.get("instance-status-detail-2");
  assert.match(statusDetail.textContent,/最近响应：/);
  assert.match(statusDetail.textContent,/最近落盘：.*序号 42/);
  assert.match(statusDetail.textContent,/未归属响应：3 条/);
  assert.match(statusDetail.textContent,/原始响应写入错误/);
  assert.match(statusDetail.textContent,/最近扫描：非数字人/);
  assert.match(statusDetail.textContent,/待归档房间：2 间/);
  assert.match(statusDetail.textContent,/自然结束尾批归档失败/);
  const knownJobStatus=fixture.jobs[1].status;
  fixture.jobs[1].status={};
  await vm.runInContext("refresh()",context);
  assert.equal(statusDetail.hidden,true);
  assert.doesNotMatch(statusDetail.textContent,/0 条|0 间|未知时间/);
  fixture.jobs[1].status=knownJobStatus;
  await vm.runInContext("refresh()",context);
  const currentSettings=fixture.settings;
  fixture.version="2.6.1-local-panel";
  const legacySettings=JSON.parse(JSON.stringify(currentSettings));
  for(const key of Object.keys(fixture.runtime_fields))delete legacySettings[key];
  for(const row of legacySettings.instances)for(const key of ["max_minutes","cooldown_minutes","room_gap_seconds","page_refresh_seconds"])delete row[key];
  fixture.settings=legacySettings;
  await vm.runInContext("refresh()",context);
  assert.equal(elements.get("settings-version-note").hidden,false);
  assert.equal(elements.get("common-runtime-fields").hidden,true);
  assert.equal(elements.get("config-tools").hidden,true);
  const beforeLegacySave=requests.length;
  await elements.get("settings-form").listeners.submit({preventDefault(){}});
  const legacySave=requests.slice(beforeLegacySave).filter(request=>request.route==="/api/settings").at(-1);
  assert.ok(legacySave);
  assert.equal(requests.slice(beforeLegacySave).some(request=>request.route==="/api/settings/validate"),false);
  const legacyPayload=JSON.parse(legacySave.options.body);
  assert.equal(Object.keys(fixture.runtime_fields).some(key=>Object.hasOwn(legacyPayload,key)),false);
  assert.equal(Object.hasOwn(legacyPayload.instances[0],"room_gap_seconds"),false);
  fixture.settings=currentSettings;fixture.version="2.7.0-local-panel";
  await vm.runInContext("refresh()",context);
  const importedConfig=JSON.parse(JSON.stringify(fixture.settings));
  importedConfig.instances[0].enabled=false;importedConfig.instances[0].segments=7;
  const importInput=elements.get("config-import");
  importInput.files=[{size:JSON.stringify(importedConfig).length,text:async()=>JSON.stringify(importedConfig)}];
  await importInput.listeners.change({target:importInput});
  assert.doesNotMatch(elements.get("config-preview").textContent,/目标 — 段/);
  assert.match(elements.get("config-preview").textContent,/实例 2（目标 1 段/);
  assert.equal(elements.get("enabled-1").checked,true);
  assert.equal(elements.get("segments-1").value,3);
  elements.get("config-apply-import").listeners.click();
  assert.equal(elements.get("enabled-1").checked,false);
  assert.equal(elements.get("segments-1").value,7);
  const beforeImportedSave=requests.length;
  await elements.get("settings-form").listeners.submit({preventDefault(){}});
  const importedSave=requests.slice(beforeImportedSave).find(request=>request.route==="/api/settings");
  const importedPayload=JSON.parse(importedSave.options.body);
  assert.equal(importedPayload.instances[0].enabled,false);
  assert.equal(importedPayload.instances[0].segments,7);
  elements.get("config-defaults").listeners.click();
  assert.equal(elements.get("enabled-1").checked,true);
  assert.equal(elements.get("segments-1").value,3);
  const defaultPayload=vm.runInContext("settingsFromForm()",context);
  assert.equal(defaultPayload.instances[0].enabled,true);
  assert.equal(defaultPayload.instances[0].segments,3);
  const beforeDefaultSave=requests.length;
  await elements.get("settings-form").listeners.submit({preventDefault(){}});
  const defaultSave=requests.slice(beforeDefaultSave).find(request=>request.route==="/api/settings");
  const savedDefaults=JSON.parse(defaultSave.options.body);
  assert.equal(savedDefaults.instances[0].enabled,true);
  assert.equal(savedDefaults.instances[0].segments,3);
  assert.equal(elements.get('correct-submit').disabled,true);
  assert.equal(elements.get("total-progress").value,50);
  assert.equal(elements.get("verified").textContent,"2");
  assert.equal(elements.get("archived").textContent,"1");
  assert.equal(elements.get("all-true-segments").textContent,"1");
  assert.equal(elements.get("not-all-true-segments").textContent,"1");
  assert.match(elements.get("total-progress-text").textContent,/2 \/ 4 有效段/);
  const rows=elements.get("instance-rows").children;
  assert.match(rows[1].children[4].textContent,/1 \/ 1 有效段 · 100.0%/);
  assert.equal(rows[1].children[6].children[0].children[1].value,75);
  assert.match(rows[1].children[6].textContent,/0:45 \/ 上限 1:00/);
  assert.equal(rows[0].children[7].children[0].dataset.operation,"resume");
  assert.equal(rows[1].children[7].children[0].dataset.operation,"pause");
  for(const index of [1,0]) {
    const button=rows[index].children[7].children[0];
    elements.get("instance-rows").listeners.click({target:button});
    await new Promise(resolve=>setImmediate(resolve));
  }
  const controls=requests.filter(request=>["/api/pause","/api/resume"].includes(request.route));
  assert.deepEqual(controls.map(request=>request.route),["/api/pause","/api/resume"]);
  assert.deepEqual(controls.map(request=>JSON.parse(request.options.body).instance_id),[2,1]);
  assert.equal(controls[0].options.headers["X-Control-Token"],"synthetic");
  fixture.jobs[1].pause_supported=false;
  await vm.runInContext("refresh()",context);
  assert.equal(rows[1].children[7].children[0].disabled,true);
  fixture.totals={rooms:0,completed_rooms:0,recorded_segments:0,remaining_segments:0,target_segments:0,completed_segments:0};
  await vm.runInContext("refresh()",context);
  assert.equal(elements.get("total-progress").value,0);
  assert.equal(vm.runInContext("percent(7,3)",context),100);
  fixture.version='2.4-local-panel';
  await vm.runInContext("refresh()",context);
  elements.get('url-instance').value='1';
  elements.get('url-load').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(elements.get('url-text').value,'https://tbzb.taobao.com/live?liveId=123');
  assert.equal(elements.get('url-rows').children.length,1);
  urlStatsCounts[1]=2;
  intervals.find(item=>item.ms===5000).callback();
  await Promise.all([vm.runInContext('stateRefreshPromise',context),vm.runInContext('urlStatsPromise',context)]);
  assert.equal(elements.get('url-rows').children[0].children[1].textContent,'2 / 3');
  assert.equal(elements.get('url-count').textContent,'1 间');
  elements.get('url-text').value='未保存草稿';
  elements.get('url-text').listeners.input();
  urlStatsCounts[1]=3;
  await vm.runInContext('refreshUrlStats()',context);
  assert.equal(elements.get('url-rows').children[0].children[1].textContent,'3 / 3');
  assert.equal(elements.get('url-text').value,'未保存草稿');
  assert.equal(vm.runInContext('urlDocument.revision',context),'abc');
  elements.get('url-text').value='123\n456';
  elements.get('url-text').listeners.input();
  elements.get('url-save').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  const save=requests.find(request=>request.route==='/api/urls'&&request.options.method==='POST');
  assert.equal(JSON.parse(save.options.body).revision,'abc');
  assert.equal(JSON.parse(save.options.body).text,'123\n456');
  fixture.version='2.5-local-panel';
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('correct-submit').disabled,false);
  elements.get('correct-live-id').value='https://tbzb.taobao.com/live?liveId=123';
  elements.get('correct-count').value='0';
  elements.get('correct-reason').value='逐段核验后修正';
  elements.get('correct-confirmed').checked=true;
  elements.get('correct-submit').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  const correction=requests.find(request=>request.route==='/api/urls/correct-count');
  assert.equal(JSON.parse(correction.options.body).live_id,'123');
  assert.equal(JSON.parse(correction.options.body).count,0);
  assert.equal(JSON.parse(correction.options.body).confirmed,true);
  assert.equal(JSON.parse(correction.options.body).revision,'abc');
  elements.get('url-text').listeners.input();
  elements.get('url-instance').value='2';
  elements.get('url-instance').listeners.change();
  assert.equal(elements.get('url-instance').value,'1');
  assert.match(elements.get('message').textContent,/保存当前网址草稿/);
  elements.get('preset-short').listeners.click();
  assert.equal(elements.get('max-minutes').value,1);
  assert.equal(elements.get('batch-rooms').value,1);
  fixture.version='2.1-local-panel';
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('url-save').disabled,true);
  assert.match(fs.readFileSync(path.join(__dirname,"../src/control/web/index.html"),"utf8"),/id="version">正在连接</);
  fixture.version='2.5-local-panel';
  fixture.totals.valid_segments=null;fixture.totals.completed_segments=null;fixture.totals.unverified_rooms=2;
  fixture.instances[0].completed_segments=null;fixture.instances[0].unverified_rooms=1;
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get("verified").textContent,"未核验");
  assert.match(elements.get("total-progress-text").textContent,/未核验/);
  assert.match(elements.get("instance-rows").children[0].children[4].textContent,/未核验/);
  failState=true;
  await vm.runInContext('refresh()',context).catch(()=>{});
  assert.match(elements.get("connection-state").textContent,/正在重试/);
  await vm.runInContext('refresh()',context).catch(()=>{});
  assert.match(elements.get("connection-state").textContent,/连接中断/);
  assert.equal(elements.get("run-status").textContent,"后台连接中断");
  assert.match(elements.get("run-detail").textContent,/可能仍在运行/);
  assert.equal(elements.get("stop").disabled,true);
  assert.equal(elements.get("instance-rows").children[0].children[7].children[0].disabled,true);
  failState=false;
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get("connection-state").textContent,"控制台已连接");
  assert.match(elements.get("last-sync").textContent,/最后同步/);
  assert.equal(elements.get("correct-live-id").disabled,false);
  hangState=true;
  const before=requests.filter(request=>request.route==='/api/state').length;
  const first=vm.runInContext('refresh()',context);
  const concurrent=vm.runInContext('refresh()',context);
  assert.equal(first,concurrent);
  await assert.rejects(first,/请求超时/);
  assert.equal(requests.filter(request=>request.route==='/api/state').length,before+1);
  await assert.rejects(vm.runInContext('refresh()',context),/请求超时/);
  assert.equal(elements.get("run-status").textContent,"后台连接中断");
  assert.match(elements.get("instance-rows").children[1].children[6].textContent,/未同步/);
  assert.match(elements.get("version").textContent,/上次版本/);
  hangState=false;
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get("connection-state").textContent,"控制台已连接");
  hangAction=true;
  const mutationsBefore=requests.filter(request=>request.options?.method==='POST').length;
  await assert.rejects(vm.runInContext('api("/api/urls",{instance_id:1})',context),/后台可能已完成/);
  assert.equal(requests.filter(request=>request.options?.method==='POST').length,mutationsBefore+1);
  hangAction=false;
  fixture.totals={rooms:0,completed_rooms:0,recorded_segments:0,valid_segments:0,remaining_segments:0,
    target_segments:0,completed_segments:0,digital_markers:{all_true:0,not_all_true:0}};
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('archived').textContent,'未核验');
  assert.equal(elements.get('all-true-segments').textContent,'未核验');
  assert.equal(elements.get('not-all-true-segments').textContent,'未核验');
  fixture.totals.archived_segments=0;
  fixture.totals.digital_markers={all_true:0,not_all_true:0,missing:0};
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get('archived').textContent,'0');
  assert.equal(elements.get('all-true-segments').textContent,'0');
  vm.runInContext('dirty=false',context);
  elements.get('url-reload').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  holdNextUrlStats=true;
  const stale=vm.runInContext('refreshUrlStats()',context);
  elements.get('url-instance').value='2';
  elements.get('url-instance').listeners.change();
  assert.equal(elements.get('url-rows').children.length,0);
  await elements.get('url-load').listeners.click();
  releaseHeldUrlStats();
  await stale;
  assert.equal(elements.get('url-rows').children[0].children[0].textContent,'222');
  assert.equal(elements.get('url-text').value,'https://tbzb.taobao.com/live?liveId=222');
  urlRowOverride={count:1,target:3,valid_count:1,digital_markers:{all_true:1,not_all_true:0}};
  await vm.runInContext('refreshUrlStats()',context);
  assert.equal(elements.get('url-rows').children[0].children[2].textContent,'1');
  assert.equal(elements.get('url-rows').children[0].children[3].textContent,'未核验');
  assert.equal(elements.get('url-rows').children[0].children[4].textContent,'未核验');
  const savedJobs=fixture.jobs;
  fixture.jobs=[{instance_id:3,alive:false,phase:"finished",status:{pending_rooms:2,
    pending_archive_rooms:["room-a","room-b"],pending_archive_reason:"自然结束尾批归档失败"}}];
  await vm.runInContext('refresh()',context);
  assert.match(elements.get("run-status").textContent,/自然完成 · 2 间待归档/);
  assert.match(elements.get("run-detail").textContent,/自然结束尾批归档失败/);
  assert.equal(elements.get("run-dot").classList.contains("green"),false);
  fixture.jobs=[{instance_id:3,alive:false,phase:"finished",status:{}}];
  await vm.runInContext('refresh()',context);
  assert.equal(elements.get("run-status").textContent,"采集未运行");
  assert.doesNotMatch(elements.get("run-detail").textContent,/0 间待归档/);
  assert.equal(elements.get("instance-status-detail-3").hidden,true);
  fixture.jobs=savedJobs;
  console.log("Dashboard checks passed: verified-only progress, disconnected/recovered state, version, pause/resume, URLs.");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
