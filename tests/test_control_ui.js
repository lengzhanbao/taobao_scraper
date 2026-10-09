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
class Element {
  constructor(tag="div") {
    this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.attributes={};
    this.classList={toggle(){}};this.value="";this._text="";
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
  enabled:target>0,segments:target||3,rooms:target?1:0,complete:target&&completed===target?1:0,
  recorded,remaining:target-completed,target_segments:target,completed_segments:completed,unverified_rooms:0,progress_source:"本次运行"});
const fixture = {token:"synthetic",version:"2.4-local-panel",code_root:"synthetic",preflight:null,
  settings:{study_root:"synthetic",python:"python",edge:"edge",ffmpeg:"ffmpeg",ffprobe:"ffprobe",
    max_minutes:1,cooldown_minutes:120,batch_rooms:1,launch_gap_seconds:0,instances:[]},
  instances:[instance(1,3,1),instance(2,1,1,4),instance(3,0,0),instance(4,0,0),instance(5,0,0)],
  totals:{rooms:2,completed_rooms:1,recorded_segments:5,valid_segments:2,archived_segments:1,remaining_segments:2,target_segments:4,completed_segments:2,unverified_rooms:0,
    digital_markers:{all_true:1,not_all_true:1,missing:0}},
  jobs:[{instance_id:1,alive:true,phase:"paused",pause_supported:true,pause_requested:true,status:{}},
    {instance_id:2,alive:true,phase:"recording",pause_supported:true,pause_requested:false,
      status:{live_id:"123",segment_index:1,elapsed_seconds:45,planned_duration_seconds:60}}]};
fixture.settings.instances=fixture.instances;
const requests=[];
const context=vm.createContext({console,AbortController,URL,
  setTimeout(callback,ms){return setTimeout(callback,(hangState&&ms===10000)||(hangAction&&ms===90000)?5:ms);},clearTimeout,
  setInterval(callback,ms){intervals.push({callback,ms});},window:{confirm:()=>true},document:{
  getElementById(id){if(!elements.has(id)){const element=new Element();element.id=id;}return elements.get(id);},
  createElement(tag){return new Element(tag);},addEventListener(){}
},fetch:async(route,options)=>{
  requests.push({route,options});
  if((route==="/api/state"&&hangState)||(options?.method==="POST"&&hangAction))return new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>{const error=new Error('synthetic abort');error.name='AbortError';reject(error);},{once:true});
  });
  if(route==="/api/state"){if(failState)throw new Error("synthetic offline");return {ok:true,json:async()=>fixture};}
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
  assert.deepEqual(["accepted","pending","applied","failed","superseded","timed_out"].map(status=>
    vm.runInContext(`timelineStatusName("${status}")`,context)),
    ["已接受","执行中","worker 已确认","失败","被取代","超时未确认"]);
  vm.runInContext(`timelineReset("synthetic-run");timelineAdd({event_id:"e1",time_utc:"2026-10-09T00:00:00Z",instance_id:1,operation:"stop",operation_id:"op-stop",command_seq:9,status:"applied",source:"worker",details:{message:"完成当前段后停止"}});timelineRender()`,context);
  assert.match(elements.get("timeline-events").textContent,/worker 已确认/);
  assert.match(elements.get("timeline-events").textContent,/操作 op-stop/);
  assert.match(elements.get("timeline-events").textContent,/命令序号 9/);
  assert.match(elements.get("timeline-events").textContent,/完成当前段后停止/);
  await vm.runInContext("refresh()",context);
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
  console.log("Dashboard checks passed: verified-only progress, disconnected/recovered state, version, pause/resume, URLs.");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
