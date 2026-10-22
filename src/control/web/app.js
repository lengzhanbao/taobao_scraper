"use strict";
let state = null;
let token = "";
let dirty = false;
let busy = false;
let legacyNoticeShown = false;
let urlDocument = null;
let urlDirty = false;
let refreshFailures = 0;
let connectionLost = false;
let stateRefreshPromise = null;
let urlStatsGeneration = 0;
let urlStatsController = null;
let urlStatsPromise = null;
let urlStatsQueued = false;
let timelineCursor = null;
let timelineRunId = "";
let timelineHasMore = false;
let timelineBusy = false;
let importedSettings = null;
let settingsV27Supported = false;
let statePollTimer = null;
let statePollPeriod = 0;
const timelineItems = new Map();
const fieldLabels = {room_gap_seconds:["切换房间等待","秒","进入不同房间前等待；下次启动生效"],page_refresh_seconds:["页面重载间隔","秒","主动重载页面以触发新数据请求；下次启动生效"],status_refresh_seconds:["面板状态刷新","秒","只影响页面显示，不改变采集节奏"],checkpoint_seconds:["中途保存间隔","秒","定期保存状态用于中断恢复；下次启动生效"],listen_timeout_seconds:["监听等待超时","秒","单次监听最长等待，不代表固定采样频率"],scan_timeout_seconds:["页面扫描超时","秒","扫描步骤等待上限"],connect_timeout_seconds:["视频连接超时","秒","连接视频流等待上限"],captcha_timeout_seconds:["验证码等待上限","秒","等待人工完成验证码的最长时间"],stall_timeout_seconds:["断流检测时限","秒","判定录制无进展的等待上限"],max_record_attempts:["录制尝试次数","次","单段失败后的有限尝试上限"],failure_cooldown_minutes:["失败冷却","分钟","失败房间再次尝试前等待"],max_room_failures:["房间失败上限","次","达到后停止重复尝试"],max_collected:["内存预览上限","条","限制内存预览；完整数据仍写入文件"]};
const $ = id => document.getElementById(id);
const phaseNames = {starting:"等待启动",ready:"已就绪",waiting:"等待房间 / 冷却",scanning:"检查直播页面",captcha_required:"等待人工完成验证码",connecting:"连接视频流",recording:"录制中",validating:"视频时长校验",segment_completed:"本段已完成",segment_failed:"本段未通过校验",paused:"已暂停",archiving:"归档校验",login_required:"等待 Edge 登录",login_timeout:"登录超时",stopped:"已停止",finished:"已完成",exited:"进程已退出",failed:"失败"};
function message(text, error=false) { $("message").hidden=false; $("message").textContent=text; $("message").classList.toggle("error",error); }
function newOperationId() {
  if(typeof crypto!="undefined"&&crypto.randomUUID)return crypto.randomUUID();
  return "op-"+Date.now()+"-"+Math.random().toString(36).slice(2);
}
async function api(path, data, requestController=null) {
  const controller = requestController || new AbortController();
  const timeout = setTimeout(()=>controller.abort(),data===undefined?10000:90000);
  try {
    const options = data===undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Control-Token":token},body:JSON.stringify(data)};
    options.signal=controller.signal;
    const response = await fetch(path,options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "请求失败");
    return result;
  } catch(error) {
    if(controller.signal.aborted) throw new Error(data===undefined?"后台请求超时（10秒）":
      "操作请求超时，后台可能已完成；请刷新核对后再操作。");
    throw error;
  } finally { clearTimeout(timeout); }
}
function settingsFromForm() {
  const config=JSON.parse(JSON.stringify(state.settings));
  for(const [key,id] of [["study_root","study-root"],["python","python"],["edge","edge"],["ffmpeg","ffmpeg"],["ffprobe","ffprobe"]]) config[key]=$(id).value.trim();
  for(const [key,id] of [["max_minutes","max-minutes"],["cooldown_minutes","cooldown"],["batch_rooms","batch-rooms"],["launch_gap_seconds","launch-gap"]]) config[key]=Number($(id).value);
  if(settingsV27Supported) for(const key of Object.keys(fieldLabels)) config[key]=Number($("setting-"+key).value);
  for(const instance of config.instances) { instance.enabled=$("enabled-"+instance.id).checked; instance.segments=Number($("segments-"+instance.id).value); if(settingsV27Supported) for(const key of ["max_minutes","cooldown_minutes","room_gap_seconds","page_refresh_seconds"]) {const input=$("instance-"+instance.id+"-"+key);instance[key]=input.value===""?null:Number(input.value);} }
  return config;
}
function renderRuntimeFields() {
  if(!settingsV27Supported)return;
  if(!$('common-runtime-fields').children.length) {
    for(const key of Object.keys(fieldLabels)) {const [name,unit,help]=fieldLabels[key];const label=document.createElement('label');label.textContent=name;const wrap=document.createElement('div');wrap.className='input-unit';const input=document.createElement('input');input.id='setting-'+key;input.type='number';input.step='1';input.setAttribute('aria-label',name);const spec=state.runtime_fields?.[key];if(spec){input.min=spec.min;input.max=spec.max;}const unitNode=document.createElement('span');unitNode.textContent=unit;wrap.append(input,unitNode);const note=document.createElement('small');note.textContent=help+' · 默认 '+(state.default_settings?.[key]??'');label.append(wrap,note);document.getElementById(['room_gap_seconds','page_refresh_seconds','status_refresh_seconds'].includes(key)?'common-runtime-fields':'advanced-runtime-fields').append(label);}
    for(const instance of state.settings.instances){for(const key of ['max_minutes','cooldown_minutes','room_gap_seconds','page_refresh_seconds']){const label=document.createElement('label');label.textContent='实例 '+instance.id+' · '+({max_minutes:'段时长上限（分钟）',cooldown_minutes:'同房间冷却（分钟）',room_gap_seconds:'切换房间等待（秒）',page_refresh_seconds:'页面重载间隔（秒）'}[key]);const input=document.createElement('input');input.type='number';input.step='1';input.id='instance-'+instance.id+'-'+key;input.placeholder='继承全局';label.append(input);$('instance-overrides').append(label);}}
  }
}
function fillSettings() {
  if(settingsV27Supported)renderRuntimeFields();
  for(const [key,id] of [["study_root","study-root"],["python","python"],["edge","edge"],["ffmpeg","ffmpeg"],["ffprobe","ffprobe"],["max_minutes","max-minutes"],["cooldown_minutes","cooldown"],["batch_rooms","batch-rooms"],["launch_gap_seconds","launch-gap"]]) $(id).value=state.settings[key];
  if(settingsV27Supported) {for(const key of Object.keys(fieldLabels)) if($('setting-'+key)) $('setting-'+key).value=state.settings[key];
  for(const instance of state.settings.instances) for(const key of ['max_minutes','cooldown_minutes','room_gap_seconds','page_refresh_seconds']) $('instance-'+instance.id+'-'+key).value=instance[key]??'';}
}
function cell(text,small) { const td=document.createElement("td"); td.textContent=text; if(small) { const el=document.createElement("small"); el.textContent=small; td.append(el); } return td; }
function percent(completed,total) { return total>0 ? Math.max(0,Math.min(100,completed*100/total)) : 0; }
function canonicalLiveId(value) {
  const text=String(value??"").trim();
  if(/^\d{1,30}$/.test(text))return text;
  const match=text.match(/https?:\/\/[^\s，,]+/i);
  if(match) {
    try {
      const url=new URL(match[0]);
      const host=url.hostname.toLowerCase(),liveId=url.searchParams.get("liveId")||"";
      if((host==="taobao.com"||host.endsWith(".taobao.com"))&&/^\d{1,30}$/.test(liveId))return liveId;
    } catch {}
  }
  throw new Error("请输入纯数字直播 ID 或包含有效 liveId 的淘宝直播 URL。");
}
function duration(seconds) { const n=Math.max(0,Math.floor(seconds||0));return Math.floor(n/60)+":"+String(n%60).padStart(2,"0"); }
function statusClock(value) {const timestamp=typeof value==="number"?value*1000:Date.parse(value);return Number.isFinite(timestamp)?new Date(timestamp).toLocaleTimeString():"未知时间";}
function pendingRoomCount(status) {const count=Number.isFinite(status?.pending_rooms)?Math.max(0,status.pending_rooms):null;const listed=Array.isArray(status?.pending_archive_rooms)?status.pending_archive_rooms.length:null;return count===null?listed:listed===null?count:Math.max(count,listed);}
function scanResultName(value) {return ({in_progress:"扫描中",success_title_rule:"标题规则命中",success_platform_flag:"平台标记通过",non_digital:"非数字人",missing_digital_flag:"缺少数字人标记",network_or_navigation_error:"网络或页面导航错误",captcha_timeout_or_stop:"验证码等待超时或已停止",journal_write_error:"响应写入失败",scan_failed:"扫描失败"})[value]||String(value||"");}
function statusDetails(status, waitingReason="") {
  if(!status||typeof status!=="object")return "";
  const lines=[];
  if(status.latest_response_t!==undefined&&status.latest_response_t!==null)lines.push("最近响应："+statusClock(status.latest_response_t));
  if(status.last_persisted_t!==undefined&&status.last_persisted_t!==null){let line="最近落盘："+statusClock(status.last_persisted_t);if(Number.isFinite(status.last_persisted_sequence))line+=" · 序号 "+status.last_persisted_sequence;lines.push(line);}
  else if(Number.isFinite(status.last_persisted_sequence))lines.push("最近落盘序号："+status.last_persisted_sequence);
  if(Number.isFinite(status.unattributed_response_count))lines.push("未归属响应："+status.unattributed_response_count+" 条");
  if(status.journal_error===true)lines.push("原始响应写入错误");
  else if(status.journal_error===false)lines.push("原始响应写入正常");
  if(typeof status.last_scan_result==="string"&&status.last_scan_result)lines.push("最近扫描："+scanResultName(status.last_scan_result));
  const pending=pendingRoomCount(status);if(pending!==null)lines.push("待归档房间："+pending+" 间");
  if(typeof status.pending_archive_reason==="string"&&status.pending_archive_reason)lines.push("归档说明："+status.pending_archive_reason);
  if(waitingReason)lines.push("等待原因："+waitingReason);
  return lines.join(" · ");
}
function progressBlock(completed,total,label) {
  const box=document.createElement("div");box.className="progress-block";
  const text=document.createElement("small");text.textContent=label;
  const bar=document.createElement("progress");bar.max=100;bar.value=completed==null?0:percent(completed,total);bar.setAttribute("aria-label",label);
  box.append(text,bar);return box;
}
function renderInstances() {
  const tbody=$("instance-rows");
  if(!tbody.children.length) for(const i of state.instances) {
    const tr=document.createElement("tr"); tr.dataset.id=i.id;
    const enabled=cell(""); const checkbox=document.createElement("input"); checkbox.type="checkbox"; checkbox.className="toggle"; checkbox.id="enabled-"+i.id; checkbox.setAttribute("aria-label","启用实例 "+i.id); enabled.append(checkbox); tr.append(enabled);
    tr.append(cell("Edge "+i.id,"端口 "+i.port+" · "+i.urls_file));
    const segments=cell("");const number=document.createElement("input");number.type="number";number.min=1;number.max=20;number.className="segment-input";number.id="segments-"+i.id;number.setAttribute("aria-label","实例 "+i.id+" 每房间段数");segments.append(number);tr.append(segments);
    tr.append(cell(""));tr.append(cell(""));const status=cell("");const phase=document.createElement("span");phase.className="job-phase";const note=document.createElement("small");note.className="instance-status-details";note.hidden=true;note.id="instance-status-detail-"+i.id;status.append(phase,note);tr.append(status);tr.append(cell(""));tr.append(cell(""));tbody.append(tr);
  }
  for(const i of state.instances) {
    const tr=tbody.querySelector('[data-id="'+i.id+'"]');
    if(!dirty) {const configured=state.settings.instances.find(row=>row.id===i.id)||i;$("enabled-"+i.id).checked=configured.enabled;$("segments-"+i.id).value=configured.segments;}
    tr.children[3].textContent=i.complete+" / "+i.rooms;
    const target=i.target_segments??(i.recorded+i.remaining);
    const completed=i.completed_segments;
    const unverified=i.unverified_rooms??0;
    const progressLabel=completed==null?"未核验 · "+unverified+" 间房缺少凭据":
      (unverified?"至少 "+completed:""+completed)+" / "+target+" 有效段 · "+percent(completed,target).toFixed(1)+"%"+
      (unverified?" · "+unverified+" 间未核验":"");
    tr.children[4].replaceChildren(progressBlock(completed==null?null:completed,target,progressLabel));
    if(i.progress_source) {const note=document.createElement("small");note.textContent=i.progress_source;tr.children[4].append(note);}
    const job=state.jobs.find(j=>j.instance_id===i.id);
    const label=tr.children[5].children[0];label.className="job-phase"+(job?.alive&&job.phase!=="paused"&&!connectionLost?" running":"");label.textContent=connectionLost?"连接中断 · 状态未知":job?(phaseNames[job.phase]||job.phase):"未启动";
    if(job?.stop_requested&&job.alive)label.textContent+=" · 停止已请求";
    else if(job?.pause_requested&&job.alive&&job.phase!=="paused")label.textContent+=" · 暂停已请求";
    const statusNote=tr.children[5].children[1];statusNote.textContent=connectionLost?"":statusDetails(job?.status,job?.alive&&job.phase==="waiting"?job.status?.reason||"":"");statusNote.hidden=!statusNote.textContent;
    const s=job?.status;const current=job?.alive&&["scanning","connecting","recording","validating"].includes(job.phase);
    tr.children[6].textContent=connectionLost?"连接中断 · 当前录制未同步":current&&s?.live_id?"第 "+(s.segment_index||"—")+" 段 · "+s.live_id:"—";
    if(!connectionLost&&job?.alive&&job.phase==="recording") {
      const limit=s.planned_duration_seconds??state.settings.max_minutes*60;
      tr.children[6].append(progressBlock(s.elapsed_seconds||0,limit,"已运行 "+duration(s.elapsed_seconds)+" / 上限 "+duration(limit)));
    }
    const control=document.createElement("button");control.className="instance-control";control.dataset.instance=i.id;
    control.dataset.operation=job?.pause_requested?"resume":"pause";control.textContent=job?.pause_requested?"▶ 继续":"Ⅱ 暂停";
    control.disabled=busy||connectionLost||!job?.alive||!job.pause_supported||job.stop_requested;
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
function refresh() {
  if(stateRefreshPromise)return stateRefreshPromise;
  stateRefreshPromise=refreshState().finally(()=>{stateRefreshPromise=null;});
  return stateRefreshPromise;
}
async function refreshState() {
  let latest;
  try {
    latest=await api("/api/state");
  } catch(error) {
    refreshFailures+=1;
    if(refreshFailures===1) {$("connection-state").textContent="连接失败，正在重试";$("connection-state").className="connection-warning";}
    if(refreshFailures>=2) {
      connectionLost=true;
      $("connection-state").textContent="控制台连接中断";$("connection-state").className="connection-error";
      $("run-status").textContent="后台连接中断";
      $("run-detail").textContent="采集子进程可能仍在运行，面板暂时无法确认状态。";
      $("run-dot").classList.toggle("green",false);
      $("version").textContent=state?.version?"上次版本："+state.version:"版本未获取";
      $("start").disabled=true;$("stop").disabled=true;$("check").disabled=true;
      for(const id of ["url-instance","url-text","url-add-text","url-import","url-load","url-reload",
                       "url-save","url-append","correct-live-id","correct-count","correct-reason",
                       "correct-confirmed","correct-submit"]) $(id).disabled=true;
      if(state) renderInstances();
    }
    throw error;
  }
  state=latest;token=state.token;refreshFailures=0;connectionLost=false;
  settingsV27Supported=/^2\.7\./.test(state.version||"")&&Object.keys(fieldLabels).every(key=>
    state.runtime_fields?.[key]&&Object.prototype.hasOwnProperty.call(state.settings||{},key))&&
    state.settings.instances?.every(instance=>["max_minutes","cooldown_minutes","room_gap_seconds","page_refresh_seconds"].every(key=>Object.prototype.hasOwnProperty.call(instance,key)));
  $("settings-version-note").hidden=settingsV27Supported;
  for(const id of ["common-runtime-fields","advanced-settings","advanced-runtime-fields","instance-overrides-details","instance-overrides","config-tools","config-preview"]) $(id).hidden=!settingsV27Supported;
  $("config-apply-import").disabled=!settingsV27Supported||dirty||!importedSettings;
  $("run-summary").hidden=!settingsV27Supported;
  scheduleStatePoll();
  if(!dirty) fillSettings();
  renderInstances();renderChecks(state.preflight);
  $("rooms").textContent=state.totals.rooms;$("completed").textContent=state.totals.completed_rooms;$("recorded").textContent=state.totals.recorded_segments;$("remaining").textContent=state.totals.remaining_segments;
  $("verified").textContent=Number.isFinite(state.totals.valid_segments)?state.totals.valid_segments:"未核验";
  $("archived").textContent=Number.isFinite(state.totals.archived_segments)?state.totals.archived_segments:"未核验";
  $("verified-note").textContent=state.totals.unverified_rooms?"至少为已核验数量；"+state.totals.unverified_rooms+" 间房缺少凭据":"依据视频与 final JSON 凭据复核";
  const markers=state.totals.digital_markers;
  const markerCountsKnown=markers&&["all_true","not_all_true","missing"].every(key=>Number.isFinite(markers[key]));
  const markerKnown=markerCountsKnown&&Number.isFinite(state.totals.valid_segments);
  $("all-true-segments").textContent=markerKnown?markers.all_true:"未核验";
  $("not-all-true-segments").textContent=markerKnown?markers.not_all_true:"未核验";
  $("flag-note").textContent=markerKnown?"另有 "+markers.missing+" 段未捕获可识别标记；仅记录，不新增样本分类":"先核验录制凭据，再显示已捕获标记";
  const target=state.totals.target_segments??(state.totals.recorded_segments+state.totals.remaining_segments);
  const completed=state.totals.completed_segments;
  const unverified=state.totals.unverified_rooms??0;
  const totalPercent=completed==null?0:percent(completed,target);
  $("total-progress").value=totalPercent;
  $("total-progress-text").textContent=completed==null?"未核验 · "+unverified+" 间房缺少凭据":
    (unverified?"至少 "+completed:""+completed)+" / "+target+" 有效段 · "+totalPercent.toFixed(1)+"%"+
    (unverified?" · "+unverified+" 间未核验":"");
  const alive=state.jobs.filter(j=>j.alive).length;
  const paused=state.jobs.filter(j=>j.alive&&j.phase==="paused").length;
  const pendingStatuses=state.jobs.map(job=>({job,count:pendingRoomCount(job.status)})).filter(item=>item.count!==null&&item.count>0);
  const pendingTotal=pendingStatuses.reduce((total,item)=>total+item.count,0);
  const pendingReason=[...new Set(pendingStatuses.map(item=>item.job.status?.pending_archive_reason).filter(reason=>typeof reason==="string"&&reason))].join("；");
  const naturallyFinishedPending=pendingStatuses.length>0&&pendingStatuses.every(item=>!item.job.alive&&item.job.phase==="finished");
  $("run-status").textContent=alive?alive+" 个实例在线"+(paused?" · "+paused+" 个已暂停":"")+(pendingTotal?" · "+pendingTotal+" 间待归档":""):
    pendingTotal?(naturallyFinishedPending?"自然完成 · ":"采集结束 · ")+pendingTotal+(naturallyFinishedPending?" 间待归档":" 间待处理"):"采集未运行";
  $("run-dot").classList.toggle("green",Boolean(alive));$("stop").disabled=!alive||busy;$("start").disabled=Boolean(alive)||busy;
  $("check").disabled=busy;
  $("run-detail").textContent=alive?"当前运行沿用启动快照；修改只在下次启动生效"+(pendingTotal?"。仍有 "+pendingTotal+" 间待归档":""):
    pendingTotal?"仍有 "+pendingTotal+" 间满段房间待归档。"+(pendingReason||"保留待处理状态"):
    "保存设置后即可启动";
  const activeJobs=state.jobs.filter(job=>job.alive&&job.actual_settings);
  const legacyActiveJobs=state.jobs.filter(job=>job.alive&&!job.actual_settings);
  const nextConfig=dirty?settingsFromForm():state.settings;
  if(settingsV27Supported) $("run-summary").textContent=(activeJobs.length?"当前运行："+activeJobs.map(job=>"实例 "+job.instance_id+"（"+configSummary(job.actual_settings)+"）").join("；"):legacyActiveJobs.length?"当前运行："+legacyActiveJobs.map(job=>"实例 "+job.instance_id+"（旧版启动快照，新参数未知）").join("；"):"当前运行：无")+"。下次启动："+nextRunSummary(nextConfig)+"。估时不含直播可用性及失败重试影响。";
  $("code-root").textContent="版本目录："+state.code_root;
  $("version").textContent=state.version?"v"+state.version.split("-")[0]:"版本未知";
  const versionParts=(state.version||"").match(/^(\d+)\.(\d+)/);
  const major=versionParts?Number(versionParts[1]):0;
  const minor=versionParts?Number(versionParts[2]):0;
  const urlSupported=major>2||(major===2&&minor>=3);
  const correctionSupported=major>2||(major===2&&minor>=5);
  for(const id of ["url-instance","url-text","url-add-text","url-import"]) $(id).disabled=busy;
  $("url-load").disabled=!urlSupported||busy;
  $("url-reload").disabled=!urlSupported||busy;
  $("url-save").disabled=!urlSupported||busy||!urlDocument;
  $("url-append").disabled=!urlSupported||busy||!urlDocument;
  $("correct-submit").disabled=!correctionSupported||busy||!urlDocument;
  for(const id of ["correct-live-id","correct-count","correct-reason","correct-confirmed"])
    $(id).disabled=!correctionSupported||busy;
  if(!urlSupported) $("url-note").textContent="运行中的控制台是旧版，重启控制台后即可在此管理网址。";
  else if(!correctionSupported) $("url-note").textContent="历史计数纠错需要 2.5 或更新版本的后台。";
  if(state.version==="2.1-local-panel"&&!legacyNoticeShown) {legacyNoticeShown=true;message("更新已写入。新归档阈值和暂停功能需要重启控制台，并在下一次启动采集时生效。");}
  $("connection-state").textContent="控制台已连接";$("connection-state").className="connection-ok";
  $("last-sync").textContent="最后同步："+new Date().toLocaleTimeString();
}
async function logRefresh() { const result=await api("/api/log?instance="+$("log-instance").value);$("log-content").textContent=result.text; }
function changed() { dirty=true;$("save-note").textContent="有未保存修改"; }
function settingsInput(event) {if(event.target.matches("input")&&(event.target.closest("#settings-form")||/^(enabled|segments|instance-)/.test(event.target.id))) {changed();$("config-apply-import").disabled=dirty||!importedSettings;}}
document.addEventListener("input",settingsInput);
document.addEventListener("change",settingsInput);
function configSummary(config) {return `目标 ${config.segments??"—"} 段 · 时长 ${config.max_minutes} 分钟 · 冷却 ${config.cooldown_minutes} 分钟 · 切房间等待 ${config.room_gap_seconds} 秒 · 页面重载 ${config.page_refresh_seconds} 秒 · 归档阈值 ${config.batch_rooms} 间 / 实例 · 实例启动间隔 ${config.launch_gap_seconds} 秒 · 目录 ${config.study_root}`;}
function nextRunSummary(config) {const enabled=config.instances.filter(instance=>instance.enabled);return `${enabled.length} 个实例：`+enabled.map(instance=>`实例 ${instance.id}（${configSummary({...config,...Object.fromEntries(['max_minutes','cooldown_minutes','room_gap_seconds','page_refresh_seconds'].map(key=>[key,instance[key]??config[key]])),segments:instance.segments})}）`).join('；');}
$("config-export").addEventListener("click",()=>{if(!settingsV27Supported){message("当前后台不支持 2.7 配置导出，请安全重启控制台。",true);return;}const config=settingsFromForm();const blob=new Blob([JSON.stringify(config,null,2)],{type:"application/json;charset=utf-8"});const link=document.createElement("a");const objectUrl=URL.createObjectURL(blob);link.href=objectUrl;link.download="taobao-live-settings-2.7.json";link.click();URL.revokeObjectURL(objectUrl);});
$("config-defaults").addEventListener("click",()=>{if(!settingsV27Supported){message("当前后台不支持 2.7 配置恢复，请安全重启控制台。",true);return;}if(!state||!window.confirm("将表单草稿替换为默认设置？需再点击保存才会写入设置。"))return;state.settings=JSON.parse(JSON.stringify(state.default_settings));dirty=false;fillSettings();renderInstances();dirty=true;changed();message("默认设置已填入草稿，实例开关、段数和覆盖均已重置；点击保存后才写入。");});
$("config-import").addEventListener("change",event=>{if(!settingsV27Supported){message("当前后台不支持 2.7 配置导入，请安全重启控制台。",true);event.target.value="";return;}const file=event.target.files[0];return action(async()=>{try{if(!file)return;if(file.size>64000)throw new Error("配置 JSON 最大 64 KB。");const imported=JSON.parse(await file.text());if(!imported||typeof imported!=="object"||Array.isArray(imported)||Object.keys(imported).some(key=>/token|cookie|auth|password/i.test(key)))throw new Error("配置必须是设置对象，且不得包含认证字段。");const checked=await api("/api/settings/validate",imported);importedSettings=checked;$("config-preview").textContent="已验证预览："+nextRunSummary(checked)+"。导入不会自动保存；仅在当前草稿干净时可显式应用。";$("config-apply-import").disabled=dirty;message("配置验证通过。预览未改动当前设置草稿。");}finally{event.target.value="";}});});
$("config-apply-import").addEventListener("click",()=>{if(!settingsV27Supported){message("当前后台不支持 2.7 配置导入，请安全重启控制台。",true);return;}if(!importedSettings||dirty){message("请先保存或放弃现有草稿，再应用预览配置。",true);return;}state.settings=JSON.parse(JSON.stringify(importedSettings));fillSettings();renderInstances();dirty=true;changed();$("config-apply-import").disabled=true;message("预览配置已载入草稿（含实例开关与段数）；点击保存后才写入设置。");});
async function action(fn) {
  if(busy)return;busy=true;$("start").disabled=true;
  const active=document.activeElement;
  const activeText=active?.tagName==="BUTTON"?active.textContent:null;
  const activeDisabled=activeText!==null?active.disabled:null;
  if(activeText!==null){active.disabled=true;active.textContent="处理中…";}
  const controls=["url-instance","url-text","url-add-text","url-import","url-load","url-save","url-append","url-reload","correct-submit"];
  for(const id of controls)$(id).disabled=true;
  try{await fn();}catch(error){message(error.message,true);}finally{
    if(activeText!==null&&active.isConnected){active.textContent=activeText;active.disabled=activeDisabled;}
    busy=false;for(const id of controls)$(id).disabled=connectionLost;
    $("url-save").disabled=connectionLost||!urlDocument;$("url-append").disabled=connectionLost||!urlDocument;
    $("correct-submit").disabled=connectionLost||!urlDocument;
    try{await refresh();}catch{}
  }
}
$("settings-form").addEventListener("submit",event=>{event.preventDefault();return action(async()=>{const config=settingsFromForm();if(settingsV27Supported)await api("/api/settings/validate",config);await api("/api/settings",config);state.settings=config;dirty=false;importedSettings=null;$("config-apply-import").disabled=true;$("save-note").textContent="已保存";message("设置已保存。运行中的实例沿用本次启动快照。");});});
$("check").addEventListener("click",()=>action(async()=>{if(dirty)throw new Error("先保存当前设置，再检查环境。");const result=await api("/api/check",{});renderChecks(result);message(result.ok?"环境检查通过，可以启动采集。":"有启动条件未满足，请查看环境检查。",!result.ok);}));
$("start").addEventListener("click",()=>action(async()=>{if(dirty)throw new Error("请先保存当前设置。");await api("/api/start",{});message("采集已启动。无需保持页面或命令行窗口打开。");}));
$("stop").addEventListener("click",()=>action(async()=>{const result=await api("/api/stop",{operation_id:newOperationId()});message(result.message+"；worker 确认和停止完成状态见事件时间线。");}));
$("instance-rows").addEventListener("click",event=>{const button=event.target.closest("button.instance-control");if(!button||button.disabled)return;const instanceId=Number(button.dataset.instance);const operation=button.dataset.operation;action(async()=>{const result=await api("/api/"+operation,{instance_id:instanceId,operation_id:newOperationId()});message(result.message);});});
$("refresh-log").addEventListener("click",()=>logRefresh().catch(e=>message(e.message,true)));
$("log-instance").addEventListener("change",()=>logRefresh().catch(e=>message(e.message,true)));
refresh().catch(e=>message("无法连接控制台："+e.message,true));
function scheduleStatePoll() {const period=Math.max(2,Number(state?.settings?.status_refresh_seconds)||5);if(statePollTimer&&period===statePollPeriod)return;if(statePollTimer)clearInterval(statePollTimer);statePollPeriod=period;statePollTimer=setInterval(()=>{if(!busy){refresh().catch(()=>{});refreshUrlStats();timelineRefresh();}},period*1000);}
scheduleStatePoll();
setInterval(()=>{if(!busy)logRefresh().catch(()=>{});},7000);

function digitalMarkerText(markers, validCount) {
  if(!Number.isFinite(validCount)||!markers||!["all_true","not_all_true","missing"].every(key=>Number.isFinite(markers[key])))return "未核验";
  return markers.all_true+" / "+markers.not_all_true+" / "+markers.missing;
}
function renderUrlRows(rows) {
  $("url-count").textContent=rows.length+" 间";
  $("url-rows").replaceChildren();
  for(const row of rows) {
    const tr=document.createElement("tr");
    tr.append(cell(row.live_id),cell(row.count+" / "+row.target),
      cell(Number.isFinite(row.valid_count)?row.valid_count:"未核验"),
      cell(digitalMarkerText(row.digital_markers,row.valid_count)),
      cell(Number.isFinite(row.archived_count)?row.archived_count:"未核验"),
      cell(row.validation_state||(row.count>=row.target?"历史计数已满":"待录制"),
        row.archive_issues?.length?"归档待核查："+row.archive_issues[0]:null));
    $("url-rows").append(tr);
  }
}
function invalidateUrlStats() {
  urlStatsGeneration++;
  urlStatsQueued=false;
  if(urlStatsController){urlStatsController.abort();urlStatsController=null;}
}
function refreshUrlStats() {
  if(!urlDocument)return Promise.resolve();
  if(urlStatsPromise){urlStatsQueued=true;return urlStatsPromise;}
  const generation=++urlStatsGeneration;
  const instanceId=String(urlDocument.instance_id);
  const controller=new AbortController();
  urlStatsController=controller;
  const task=(async()=>{
    try {
      const result=await api("/api/urls?instance="+encodeURIComponent(instanceId),undefined,controller);
      if(generation!==urlStatsGeneration||controller.signal.aborted||!urlDocument||
         String(urlDocument.instance_id)!==instanceId||String($("url-instance").value)!==instanceId||
         String(result.instance_id)!==instanceId)return;
      if(!urlDirty) {
        urlDocument=result;
        if($("url-text").value!==result.active_text)$("url-text").value=result.active_text;
      }
      renderUrlRows(result.rows);
    } catch(error) {
      if(!controller.signal.aborted) return;
    } finally {
      if(urlStatsController===controller)urlStatsController=null;
      if(urlStatsPromise===task)urlStatsPromise=null;
      const queued=urlStatsQueued;urlStatsQueued=false;
      if(queued)refreshUrlStats();
    }
  })();
  urlStatsPromise=task;
  return task;
}
function showUrlDocument(result) {
  invalidateUrlStats();
  urlDocument=result;urlDirty=false;
  $("url-text").value=result.active_text;
  $("url-path").textContent=result.path;
  $("url-note").textContent="已加载 · 保存会保留原文件备份";
  $("url-count").textContent=result.rows.length+" 间";
  renderUrlRows(result.rows);
  const elsewhere=new Map();
  // Cross-instance duplicates are also checked authoritatively before starting.
  for(const i of state.instances) if(i.id!==result.instance_id&&i.rooms)elsewhere.set(i.id,i.rooms);
  if(elsewhere.size) $("url-note").textContent+="；跨实例重复链接会在启动检查中提示";
}
$("url-text").addEventListener("input",()=>{urlDirty=true;$("url-note").textContent="网址草稿未保存";});
$("url-instance").addEventListener("change",()=>{
  invalidateUrlStats();
  if((urlDirty||$("url-add-text").value.trim())&&urlDocument) {$("url-instance").value=String(urlDocument.instance_id);message("先保存当前网址草稿或添加追加区网址，再切换实例。",true);return;}
  urlDocument=null;$("url-text").value="";$("url-path").textContent="点击加载清单";$("url-rows").replaceChildren();$("url-count").textContent="未加载";$("url-save").disabled=true;$("url-append").disabled=true;
});
$("url-load").addEventListener("click",()=>action(async()=>{
  if(dirty)throw new Error("先保存录制设置，再加载对应数据目录的网址。");
  if(urlDirty)throw new Error("请先保存网址草稿；如需放弃草稿，点击“恢复已保存清单”。");
  showUrlDocument(await api("/api/urls?instance="+$("url-instance").value));
}));
$("url-reload").addEventListener("click",()=>{
  if(urlDirty&&!window.confirm("放弃页面未保存的网址草稿，并重新加载？已保存的文件不会修改。"))return;
  action(async()=>{if(dirty)throw new Error("先保存录制设置，再加载网址。");showUrlDocument(await api("/api/urls?instance="+$("url-instance").value));});
});
async function saveUrls(mode) {
  if(dirty)throw new Error("先保存录制设置，再修改网址清单。");
  if(!urlDocument)throw new Error("请先加载这个实例的清单。");
  if(mode==="append"&&urlDirty)throw new Error("请先保存编辑区的草稿，再追加新网址。");
  const text=$(mode==="append"?"url-add-text":"url-text").value;
  if(mode==="append"&&!text.trim())throw new Error("请先粘贴要添加的网址。");
  const result=await api("/api/urls",{instance_id:urlDocument.instance_id,revision:urlDocument.revision,text,mode});
  showUrlDocument(result);
  if(mode==="append")$("url-add-text").value="";
  message(result.message+(result.backup?" 备份："+result.backup:""));
}
$("url-save").addEventListener("click",()=>action(()=>saveUrls("replace")));
$("url-append").addEventListener("click",()=>action(()=>saveUrls("append")));
$("correct-submit").addEventListener("click",()=>action(async()=>{
  if(dirty)throw new Error("先保存录制设置，再更正历史计数。");
  if(!urlDocument)throw new Error("请先加载要更正的实例清单。");
  const liveId=canonicalLiveId($("correct-live-id").value);
  const countText=$("correct-count").value;
  const count=Number(countText);
  const reason=$("correct-reason").value.trim();
  const row=urlDocument.rows.find(item=>item.live_id===liveId);
  if(!row)throw new Error("直播 ID 不在当前已加载清单中。");
  if(!countText.trim()||!Number.isInteger(count)||count<0||count>10000)throw new Error("更正段数必须是 0—10000 的整数。");
  if(!reason||reason.length>200)throw new Error("请填写 1—200 字的更正原因。");
  if($("correct-confirmed").checked!==true)throw new Error("请先勾选凭据核对确认。");
  if(count<row.count&&!window.confirm("你要把历史计数从 "+row.count+" 下调到 "+count+"。确认已核对有效视频和 final JSON？"))return;
  const result=await api("/api/urls/correct-count",{instance_id:urlDocument.instance_id,live_id:liveId,count,
    reason,confirmed:true,revision:urlDocument.revision});
  showUrlDocument(result);$("correct-live-id").value="";$("correct-count").value="";
  $("correct-reason").value="";$("correct-confirmed").checked=false;
  message(result.message+" 审计编号："+result.correction_id+(result.backup?"；备份："+result.backup:""));
}));
$("url-import").addEventListener("change",event=>{
  const file=event.target.files[0];
  action(async()=>{try{if(!urlDocument)throw new Error("请先加载一个实例的清单，再导入 TXT。");if(!file)return;if(file.size>48000)throw new Error("TXT 最多 48 KB，请分批导入。");const text=await file.text();$("url-add-text").value=text.replace(/^\uFEFF/,"");message("已导入到追加区。点击“添加到这个实例”后保存，不会覆盖编辑区草稿。");}finally{event.target.value="";}});
});
$("url-export").addEventListener("click",()=>{
  const blob=new Blob([$("url-text").value],{type:"text/plain;charset=utf-8"});const link=document.createElement("a");const objectUrl=URL.createObjectURL(blob);link.href=objectUrl;link.download="urls_"+$("url-instance").value+"_draft.txt";link.click();URL.revokeObjectURL(objectUrl);
});
function preset(short) {
  $("max-minutes").value=short?1:20;$("cooldown").value=120;$("batch-rooms").value=short?1:6;$("launch-gap").value=short?0:45;
  for(const key of Object.keys(fieldLabels)) if($("setting-"+key)) $("setting-"+key).value=state.default_settings?.[key]??0;
  for(const i of state.instances){$("segments-"+i.id).value=short?1:(i.id===4?4:3);if(settingsV27Supported)for(const key of ["max_minutes","cooldown_minutes","room_gap_seconds","page_refresh_seconds"])$("instance-"+i.id+"-"+key).value="";}
  changed();message("配置已填入，检查实例开关后保存；不会自动启动采集。");
}
$("preset-study").addEventListener("click",()=>{if(state&&!busy)preset(false);});
$("preset-short").addEventListener("click",()=>{if(state&&!busy)preset(true);});

const operationStatusNames={accepted:"已接受",pending:"执行中",applied:"worker 已确认",
  failed:"失败",superseded:"被取代",timed_out:"超时未确认"};
function timelineStatusName(status) { return operationStatusNames[status]||phaseNames[status]||status||"事件"; }
function timelineReset(runId) {
  timelineRunId=runId;timelineCursor=null;timelineHasMore=false;timelineItems.clear();
  $("timeline-events").replaceChildren();$("timeline-more").hidden=true;
}
function timelineAdd(item) {
  const id=String(item.event_id||"");
  if(!id||timelineItems.has(id))return;
  if(item.operation_id&&operationStatusNames[item.status]) {
    const synthetic=`operation:${item.instance_id}:${item.operation_id}:${item.status}`;
    timelineItems.delete(synthetic);
  }
  timelineItems.set(id,item);
}
function timelineRender() {
  const list=$("timeline-events");list.replaceChildren();
  const rows=[...timelineItems.values()].sort((a,b)=>String(a.time_utc||a.created_at||"").localeCompare(String(b.time_utc||b.created_at||""))||String(a.event_id).localeCompare(String(b.event_id)));
  if(!rows.length) {const empty=document.createElement("li");empty.className="timeline-empty";empty.textContent="此运行暂无事件或操作请求。";list.append(empty);return;}
  for(const item of rows) {
    const li=document.createElement("li");
    const time=document.createElement("time");time.className="timeline-time";time.textContent=item.time_utc||item.created_at||"时间未知";
    const status=document.createElement("strong");status.className="timeline-status "+(item.status||"");status.textContent=timelineStatusName(item.status);
    const details=document.createElement("span");details.className="timeline-details";
    const parts=[];
    if(item.instance_id)parts.push("Edge "+item.instance_id);
    if(item.operation)parts.push(item.operation);
    if(item.operation_id)parts.push("操作 "+item.operation_id);
    if(Number.isInteger(item.command_seq))parts.push("命令序号 "+item.command_seq);
    if(item.live_id)parts.push("直播 "+item.live_id);
    if(item.segment_index)parts.push("第 "+item.segment_index+" 段");
    if(item.recording_id)parts.push("录制 "+item.recording_id);
    if(item.details&&Object.keys(item.details).length)parts.push(JSON.stringify(item.details));
    if(item.detail)parts.push(item.detail);
    if(item.source)parts.push("来源："+item.source);
    details.textContent=parts.join(" · ");li.append(time,status,details);list.append(li);
  }
}
async function timelineRunsRefresh() {
  const result=await api("/api/runs");
  const runs=Array.isArray(result.runs)?result.runs:[];
  const select=$("timeline-run"), old=select.value;
  select.replaceChildren();
  for(const run of runs) {
    const option=document.createElement("option");option.value=run.run_id;
    option.textContent=run.run_id+(run.current?" · 当前":"");select.append(option);
  }
  const selected=runs.some(run=>run.run_id===old)?old:(runs[0]?.run_id||"");
  select.value=selected;
  if(selected!==timelineRunId)timelineReset(selected);
  return selected;
}
async function timelineRefresh(loadMore=false) {
  if(timelineBusy)return;
  timelineBusy=true;
  try {
    const runId=await timelineRunsRefresh();
    if(!runId) {timelineRender();return;}
    const instance=$("timeline-instance").value;
    const query=new URLSearchParams({run_id:runId,limit:"100"});
    if(timelineCursor&&!loadMore)query.set("cursor",timelineCursor);
    if(loadMore&&timelineCursor)query.set("cursor",timelineCursor);
    if(instance)query.set("instance",instance);
    const page=await api("/api/events?"+query.toString());
    for(const item of (page.events||[]))timelineAdd(item);
    timelineCursor=page.cursor||timelineCursor;timelineHasMore=Boolean(page.has_more);
    $("timeline-more").hidden=!timelineHasMore;
    const operations=await api("/api/operations?run_id="+encodeURIComponent(runId));
    const filterInstance=instance?Number(instance):null;
    for(const operation of (operations.operations||[])) {
      if(filterInstance&&operation.instance_id!==filterInstance)continue;
      const history=Array.isArray(operation.status_history)?operation.status_history:[];
      for(let index=0;index<history.length;index++) {
        const step=history[index];
        const key=`operation:${operation.instance_id}:${operation.operation_id}:${step.status}`;
        const eventExists=[...timelineItems.values()].some(item=>item.operation_id===operation.operation_id&&item.instance_id===operation.instance_id&&item.status===step.status);
        if(!eventExists)timelineAdd({...operation,event_id:key,status:step.status,time_utc:step.time_utc,source:"持久回执"});
      }
      if(operation.status&&(!history.length||history.at(-1)?.status!==operation.status)) {
        const key=`operation:${operation.instance_id}:${operation.operation_id}:${operation.status}`;
        const eventExists=[...timelineItems.values()].some(item=>item.operation_id===operation.operation_id&&item.instance_id===operation.instance_id&&item.status===operation.status);
        if(!eventExists)timelineAdd({...operation,event_id:key,status:operation.status,
          time_utc:operation.worker_ack_at||operation.updated_at,source:"持久回执"});
      }
    }
    timelineRender();
    $("timeline-gap").textContent=(state?.telemetry_gap||state?.jobs?.some(job=>job.status?.telemetry_gap))?"事件遥测有写入缺口；操作回执与当前状态仍可单独核对。":"";
  } catch(error) {
    $("timeline-gap").textContent="时间线读取失败："+error.message;
  } finally { timelineBusy=false; }
}
$("timeline-run").addEventListener("change",()=>{timelineReset($("timeline-run").value);timelineRefresh().catch(()=>{});});
$("timeline-instance").addEventListener("change",()=>{timelineReset($("timeline-run").value);timelineRefresh().catch(()=>{});});
$("timeline-refresh").addEventListener("click",()=>timelineRefresh().catch(()=>{}));
$("timeline-more").addEventListener("click",()=>timelineRefresh(true));
timelineRefresh().catch(()=>{});
