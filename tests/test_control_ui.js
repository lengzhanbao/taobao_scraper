"use strict";
// Exercise the real dashboard against a synthetic DOM and API, with no network.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const elements = new Map();
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
  recorded,remaining:target-completed,target_segments:target,completed_segments:completed,progress_source:"本次运行"});
const fixture = {token:"synthetic",version:"2.2-local-panel",code_root:"synthetic",preflight:null,
  settings:{study_root:"synthetic",python:"python",edge:"edge",ffmpeg:"ffmpeg",ffprobe:"ffprobe",
    max_minutes:1,cooldown_minutes:120,batch_rooms:1,launch_gap_seconds:0,instances:[]},
  instances:[instance(1,3,1),instance(2,1,1,4),instance(3,0,0),instance(4,0,0),instance(5,0,0)],
  totals:{rooms:2,completed_rooms:1,recorded_segments:5,remaining_segments:2,target_segments:4,completed_segments:2},
  jobs:[{instance_id:1,alive:true,phase:"paused",pause_supported:true,pause_requested:true,status:{}},
    {instance_id:2,alive:true,phase:"recording",pause_supported:true,pause_requested:false,
      status:{live_id:"123",segment_index:1,elapsed_seconds:45,planned_duration_seconds:60}}]};
fixture.settings.instances=fixture.instances;
const requests=[];
const context=vm.createContext({console,setInterval(){},document:{
  getElementById(id){if(!elements.has(id)){const element=new Element();element.id=id;}return elements.get(id);},
  createElement(tag){return new Element(tag);},addEventListener(){}
},fetch:async(route,options)=>{
  requests.push({route,options});
  if(route==="/api/state")return {ok:true,json:async()=>fixture};
  return {ok:true,json:async()=>({message:"synthetic action accepted"})};
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../src/control/web/app.js"),"utf8"),context);
async function main() {
  await vm.runInContext("refresh()",context);
  assert.equal(elements.get("total-progress").value,50);
  assert.match(elements.get("total-progress-text").textContent,/2 \/ 4 段/);
  const rows=elements.get("instance-rows").children;
  assert.match(rows[1].children[4].textContent,/1 \/ 1 段 · 100.0%/);
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
  console.log("Dashboard checks passed: total/instance/time progress, targeted pause/resume, legacy task, empty plan.");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
