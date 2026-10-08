"use strict";
let state = null;
let token = "";
let dirty = false;
let busy = false;
let legacyNoticeShown = false;
const $ = id => document.getElementById(id);
const phaseNames = {starting:"等待启动",ready:"已就绪",waiting:"等待房间 / 冷却",scanning:"检查直播页面",connecting:"连接视频流",recording:"录制中",validating:"视频时长校验",segment_completed:"本段已完成",segment_failed:"本段未通过校验",paused:"已暂停",archiving:"归档校验",login_required:"等待 Edge 登录",login_timeout:"登录超时",stopped:"已停止",finished:"已完成",exited:"进程已退出",failed:"失败"};
function message(text, error=false) { $("message").hidden=false; $("message").textContent=text; $("message").classList.toggle("error",error); }
async function api(path, data) {
  const response = await fetch(path, data===undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Control-Token":token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}
function settingsFromForm() {
  const config=JSON.parse(JSON.stringify(state.settings));
  for(const [key,id] of [["study_root","study-root"],["python","python"],["edge","edge"],["ffmpeg","ffmpeg"],["ffprobe","ffprobe"]]) config[key]=$(id).value.trim();
  for(const [key,id] of [["max_minutes","max-minutes"],["cooldown_minutes","cooldown"],["batch_rooms","batch-rooms"],["launch_gap_seconds","launch-gap"]]) config[key]=Number($(id).value);
  for(const instance of config.instances) { instance.enabled=$("enabled-"+instance.id).checked; instance.segments=Number($("segments-"+instance.id).value); }
  return config;
}
function fillSettings() {
  for(const [key,id] of [["study_root","study-root"],["python","python"],["edge","edge"],["ffmpeg","ffmpeg"],["ffprobe","ffprobe"],["max_minutes","max-minutes"],["cooldown_minutes","cooldown"],["batch_rooms","batch-rooms"],["launch_gap_seconds","launch-gap"]]) $(id).value=state.settings[key];
}
function cell(text,small) { const td=document.createElement("td"); td.textContent=text; if(small) { const el=document.createElement("small"); el.textContent=small; td.append(el); } return td; }
function percent(completed,total) { return total>0 ? Math.max(0,Math.min(100,completed*100/total)) : 0; }
function duration(seconds) { const n=Math.max(0,Math.floor(seconds||0));return Math.floor(n/60)+":"+String(n%60).padStart(2,"0"); }
function progressBlock(completed,total,label) {
  const box=document.createElement("div");box.className="progress-block";
  const text=document.createElement("small");text.textContent=label;
  const bar=document.createElement("progress");bar.max=100;bar.value=percent(completed,total);bar.setAttribute("aria-label",label);
  box.append(text,bar);return box;
}
function renderInstances() {
  const tbody=$("instance-rows");
  if(!tbody.children.length) for(const i of state.instances) {
    const tr=document.createElement("tr"); tr.dataset.id=i.id;
    const enabled=cell(""); const checkbox=document.createElement("input"); checkbox.type="checkbox"; checkbox.className="toggle"; checkbox.id="enabled-"+i.id; checkbox.setAttribute("aria-label","启用实例 "+i.id); enabled.append(checkbox); tr.append(enabled);
    tr.append(cell("Edge "+i.id,"端口 "+i.port+" · "+i.urls_file));
    const segments=cell("");const number=document.createElement("input");number.type="number";number.min=1;number.max=20;number.className="segment-input";number.id="segments-"+i.id;number.setAttribute("aria-label","实例 "+i.id+" 每房间段数");segments.append(number);tr.append(segments);
    for(let n=0;n<5;n++) tr.append(cell("")); tbody.append(tr);
  }
  for(const i of state.instances) {
    const tr=tbody.querySelector('[data-id="'+i.id+'"]');
    if(!dirty) { $("enabled-"+i.id).checked=i.enabled; $("segments-"+i.id).value=i.segments; }
    tr.children[3].textContent=i.complete+" / "+i.rooms;
    const target=i.target_segments??(i.recorded+i.remaining);
    const completed=i.completed_segments??Math.min(i.recorded,target);
    tr.children[4].replaceChildren(progressBlock(completed,target,completed+" / "+target+" 段 · "+percent(completed,target).toFixed(1)+"%"));
    if(i.progress_source) {const note=document.createElement("small");note.textContent=i.progress_source;tr.children[4].append(note);}
    const job=state.jobs.find(j=>j.instance_id===i.id);
    const label=document.createElement("span");label.className="job-phase"+(job?.alive&&job.phase!=="paused"?" running":"");label.textContent=job?(phaseNames[job.phase]||job.phase):"未启动";
    if(job?.stop_requested&&job.alive)label.textContent+=" · 停止已请求";
    else if(job?.pause_requested&&job.alive&&job.phase!=="paused")label.textContent+=" · 暂停已请求";
    tr.children[5].replaceChildren(label);
    if(job?.alive&&job.phase==="waiting"&&job.status?.reason) {const reason=document.createElement("small");reason.textContent=job.status.reason;tr.children[5].append(reason);}
    const s=job?.status;const current=job?.alive&&["scanning","connecting","recording","validating"].includes(job.phase);
    tr.children[6].textContent=current&&s?.live_id?"第 "+(s.segment_index||"—")+" 段 · "+s.live_id:"—";
    if(job?.alive&&job.phase==="recording") {
      const limit=s.planned_duration_seconds??state.settings.max_minutes*60;
      tr.children[6].append(progressBlock(s.elapsed_seconds||0,limit,"已运行 "+duration(s.elapsed_seconds)+" / 上限 "+duration(limit)));
    }
    const control=document.createElement("button");control.className="instance-control";control.dataset.instance=i.id;
    control.dataset.operation=job?.pause_requested?"resume":"pause";control.textContent=job?.pause_requested?"▶ 继续":"Ⅱ 暂停";
    control.disabled=busy||!job?.alive||!job.pause_supported||job.stop_requested;
    tr.children[7].replaceChildren(control);
    if(job?.alive&&!job.pause_supported) {const note=document.createElement("small");note.textContent="下次启动支持暂停";tr.children[7].append(note);}
  }
  $("enabled-count").textContent=state.instances.filter(i=>i.enabled).length+" 个实例已启用";
}
function renderChecks(result) {
  if(!result) return;
  $("check-time").textContent="检查于 "+new Date(result.checked_t*1000).toLocaleTimeString();
  $("checks").className="check-grid";$("checks").replaceChildren();
  for(const item of result.checks) {const row=document.createElement("div");row.className="check-item";const mark=document.createElement("span");mark.className="check-mark"+(!item.ok?" bad":"");mark.textContent=item.ok?"✓":item.required?"!":"○";const content=document.createElement("div");content.textContent=item.name;const detail=document.createElement("small");detail.textContent=item.detail;content.append(detail);row.append(mark,content);$("checks").append(row);}
}
async function refresh() {
  state=await api("/api/state");token=state.token;
  if(!dirty) fillSettings();
  renderInstances();renderChecks(state.preflight);
  $("rooms").textContent=state.totals.rooms;$("completed").textContent=state.totals.completed_rooms;$("recorded").textContent=state.totals.recorded_segments;$("remaining").textContent=state.totals.remaining_segments;
  const target=state.totals.target_segments??(state.totals.recorded_segments+state.totals.remaining_segments);
  const completed=state.totals.completed_segments??Math.min(state.totals.recorded_segments,target);
  const totalPercent=percent(completed,target);
  $("total-progress").value=totalPercent;
  $("total-progress-text").textContent=completed+" / "+target+" 段 · "+totalPercent.toFixed(1)+"%";
  const alive=state.jobs.filter(j=>j.alive).length;
  const paused=state.jobs.filter(j=>j.alive&&j.phase==="paused").length;
  $("run-status").textContent=alive?alive+" 个实例在线"+(paused?" · "+paused+" 个已暂停":""):"采集未运行";
  $("run-dot").classList.toggle("green",Boolean(alive));$("stop").disabled=!alive||busy;$("start").disabled=Boolean(alive)||busy;
  $("run-detail").textContent=alive?"修改设置将在下次启动时生效":"保存设置后即可启动";
  $("code-root").textContent="版本目录："+state.code_root;
  $("version").textContent="v"+state.version.split("-")[0];
  if(state.version==="2.1-local-panel"&&!legacyNoticeShown) {legacyNoticeShown=true;message("更新已写入。新归档阈值和暂停功能需要重启控制台，并在下一次启动采集时生效。");}
}
async function logRefresh() { const result=await api("/api/log?instance="+$("log-instance").value);$("log-content").textContent=result.text; }
function changed() { dirty=true;$("save-note").textContent="有未保存修改"; }
document.addEventListener("input",event=>{if(event.target.matches("input"))changed();});
document.addEventListener("change",event=>{if(event.target.matches("input"))changed();});
async function action(fn) { if(busy)return;busy=true;$("start").disabled=true;try{await fn();}catch(error){message(error.message,true);}finally{busy=false;try{await refresh();}catch{}} }
$("settings-form").addEventListener("submit",event=>{event.preventDefault();action(async()=>{await api("/api/settings",settingsFromForm());dirty=false;$("save-note").textContent="已保存";message("设置已保存。运行中的实例沿用本次启动快照。");});});
$("check").addEventListener("click",()=>action(async()=>{if(dirty)throw new Error("先保存当前设置，再检查环境。");const result=await api("/api/check",{});renderChecks(result);message(result.ok?"环境检查通过，可以启动采集。":"有启动条件未满足，请查看环境检查。",!result.ok);}));
$("start").addEventListener("click",()=>action(async()=>{if(dirty)throw new Error("请先保存当前设置。");await api("/api/start",{});message("采集已启动。无需保持页面或命令行窗口打开。");}));
$("stop").addEventListener("click",()=>action(async()=>{const result=await api("/api/stop",{});message(result.message+"；不会强制中断当前视频。");}));
$("instance-rows").addEventListener("click",event=>{const button=event.target.closest("button.instance-control");if(!button||button.disabled)return;const instanceId=Number(button.dataset.instance);const operation=button.dataset.operation;action(async()=>{const result=await api("/api/"+operation,{instance_id:instanceId});message(result.message);});});
$("refresh-log").addEventListener("click",()=>logRefresh().catch(e=>message(e.message,true)));
$("log-instance").addEventListener("change",()=>logRefresh().catch(e=>message(e.message,true)));
refresh().catch(e=>message("无法连接控制台："+e.message,true));
setInterval(()=>{if(!busy)refresh().catch(()=>{});},5000);
setInterval(()=>{if(!busy)logRefresh().catch(()=>{});},7000);
