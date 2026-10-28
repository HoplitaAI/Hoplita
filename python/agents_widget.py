#!/usr/bin/env python3

import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView


_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  :root{
    --bg:#0b1220; --surf1:#1b2434; --surf2:#131a29; --line:#2a3547;
    --txt:#e6edf7; --muted:#93a1b5;
    --pending:#64748b; --running:#f5b800; --pilot:#3fb950; --done:#22c55e; --failed:#ef4444;
    --local:#3b82f6; --out:#22c55e; --in:#ef4444; --model:#c4b5fd;
  }
  html,body{
    margin:0; padding:18px; box-sizing:border-box; color:var(--txt);
    font-family:-apple-system,'Segoe UI',Roboto,Inter,sans-serif;
    background:
      radial-gradient(1200px 500px at 15% -10%, #16233b 0%, rgba(11,18,32,0) 60%),
      radial-gradient(900px 500px at 110% 0%, #1a1533 0%, rgba(11,18,32,0) 55%),
      var(--bg);
    min-height:100vh;
  }
  *{ box-sizing:border-box; }
  ::-webkit-scrollbar{ height:10px; width:10px; }
  ::-webkit-scrollbar-thumb{ background:#28324a; border-radius:6px; }
  ::-webkit-scrollbar-thumb:hover{ background:#374357; }

  .head{ display:flex; align-items:center; gap:10px; margin:0 0 4px; }
  h2{ color:#f4f7fc; margin:0; font-size:17px; font-weight:700; letter-spacing:.2px; }
  .msum{ font-size:12px; color:var(--model); margin:6px 0 2px 2px; }
  .msum b{ color:#e9d5ff; }
  .legend{ font-size:12px; color:var(--muted); margin:2px 0 14px; display:flex;
    align-items:center; gap:4px; flex-wrap:wrap; }
  .legend .dot{ width:9px; height:9px; border-radius:50%; margin:0 3px 0 10px;
    display:inline-block; }

  .layout{ display:flex; gap:22px; align-items:flex-start; flex-wrap:wrap; }
  .cards{ flex:1 1 300px; min-width:280px; display:flex; flex-direction:column; gap:11px; }

  .card{
    position:relative; overflow:hidden; display:flex; align-items:center; gap:12px;
    padding:13px 15px; border-radius:14px;
    background:linear-gradient(150deg,var(--surf1),var(--surf2));
    border:1px solid var(--line); border-left:4px solid var(--local);
    box-shadow:0 6px 18px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.03);
    animation:slidein .32s ease; transition:border-color .3s, box-shadow .3s, transform .15s;
  }
  .card:hover{ transform:translateY(-1px); }
  @keyframes slidein{ from{opacity:0; transform:translateY(10px)} to{opacity:1; transform:none} }
  .card.o-local{ border-left-color:var(--local); }
  .card.o-out{ border-left-color:var(--out); }
  .card.o-in{ border-left-color:var(--in); }
  .card.running{ border-color:var(--running); box-shadow:0 0 22px rgba(245,184,0,.22), 0 6px 18px rgba(0,0,0,.35); }
  .card.done{ border-color:rgba(34,197,94,.55); }
  .card.failed{ border-color:rgba(239,68,68,.6); }
  .card.cancelled{ border-color:rgba(124,92,255,.45); opacity:.55; }
  .card.cancelled .goal{ text-decoration:line-through; }
  .card.running::after{
    content:''; position:absolute; left:0; right:0; bottom:0; height:2px;
    background:linear-gradient(90deg,transparent,var(--running),transparent);
    background-size:220% 100%; animation:shimmer 1.4s linear infinite;
  }
  @keyframes shimmer{ from{background-position:120% 0} to{background-position:-120% 0} }

  .led{ width:14px; height:14px; border-radius:50%; flex-shrink:0; background:var(--pending); }
  .led.running{ background:var(--pilot); animation:pilot 1.4s ease-out infinite; }
  .led.done{ background:var(--done); box-shadow:0 0 10px #22c55eaa; }
  .led.failed{ background:var(--failed); box-shadow:0 0 10px #ef4444aa; }
  .led.cancelled{ background:#7c5cff; box-shadow:none; opacity:.7; }
  @keyframes pilot{ 0%{ box-shadow:0 0 0 0 rgba(63,185,80,.75); }
    70%{ box-shadow:0 0 0 9px rgba(63,185,80,0); } 100%{ box-shadow:0 0 0 0 rgba(63,185,80,0); } }

  .info{ flex:1; min-width:0; }
  .goal{ font-size:13px; color:var(--txt); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta{ font-size:12px; color:var(--muted); margin-top:3px; }
  .role-badge,.model-badge,.origin-badge{
    display:inline-block; font-size:12px; padding:1px 7px; border-radius:7px;
    margin-right:6px; font-weight:600; vertical-align:middle;
  }
  .role-badge{ background:#2a3346; color:#cbd5e1; font-weight:500; }
  .model-badge{ background:#31184d; color:#d8b4fe; }
  .origin-badge.o-local{ background:#16305e; color:#93c5fd; }
  .origin-badge.o-out{ background:#0f3d24; color:#86efac; }
  .origin-badge.o-in{ background:#5e1616; color:#fca5a5; }
  .spinner{ width:14px; height:14px; border:2px solid #2a3346; border-top-color:var(--running);
    border-radius:50%; animation:spin .8s linear infinite; flex-shrink:0; }
  @keyframes spin{ to{ transform:rotate(360deg); } }
  .empty{ color:#64748b; font-size:13px; padding:22px; text-align:center; }

  .intentband{ margin:0 0 16px; padding:12px 14px; border-radius:15px;
    background:linear-gradient(150deg,#151b30,#0f1524);
    border:1px solid #2b3560; animation:slidein .32s ease; }
  .ihead{ font-size:12px; font-weight:700; color:#a5b4fc; letter-spacing:.4px;
    margin:0 0 9px; display:flex; align-items:center; gap:8px; }
  .icards{ display:flex; flex-direction:column; gap:7px; }
  .icard{ position:relative; overflow:hidden; display:flex; align-items:center;
    gap:10px; padding:8px 11px; border-radius:11px;
    background:linear-gradient(150deg,var(--surf1),var(--surf2));
    border:1px solid var(--line); border-left:4px solid #6366f1;
    animation:slidein .32s ease; transition:border-color .3s, opacity .3s; }
  .icard.doing{ border-color:var(--running); }
  .icard.doing::after{
    content:''; position:absolute; left:0; right:0; bottom:0; height:2px;
    background:linear-gradient(90deg,transparent,var(--running),transparent);
    background-size:220% 100%; animation:shimmer 1.4s linear infinite; }
  .icard.done{ border-color:rgba(34,197,94,.45); opacity:.72; }
  .icard.failed{ border-color:rgba(239,68,68,.5); }
  .icard.unfinished{ border-color:rgba(124,92,255,.4); opacity:.5; }
  .icard.unfinished .igoal{ text-decoration:line-through; }
  .igoal{ font-size:12.5px; color:var(--txt); flex:1; min-width:0;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .icard.done .igoal{ text-decoration:line-through;
    text-decoration-color:rgba(34,197,94,.6); text-decoration-thickness:2px;
    color:var(--muted); }
  .icard.failed .igoal{ text-decoration:line-through;
    text-decoration-color:rgba(239,68,68,.6); text-decoration-thickness:2px;
    color:var(--muted); }

  .steps{ margin-top:9px; }
  .rail{ display:flex; gap:3px; height:3px; }
  .seg{ flex:1; border-radius:2px; background:#263247; transition:background .35s; }
  .seg.done{ background:var(--done); }
  .seg.failed{ background:var(--failed); }
  .seg.unfinished{ background:#7c5cff; opacity:.45; }
  .seg.doing{ background:linear-gradient(90deg,#7a5c00,var(--running),#7a5c00);
    background-size:220% 100%; animation:shimmer 1.4s linear infinite; }
  .now{ display:flex; align-items:center; gap:7px; margin-top:6px;
    font-size:12px; color:#cbd5e1; }
  .now .ntxt{ flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .now .spinner{ width:10px; height:10px; border-width:1.5px; }
  .now .nok{ color:var(--done); font-size:12px; }
  .now .ncount{ color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
  .card.has-steps{ cursor:pointer; align-items:flex-start; }
  .card.has-steps .led{ margin-top:2px; }
  .card.has-steps > div:last-child{ margin-top:1px; }
  .slist{ margin-top:8px; padding-top:7px; border-top:1px solid #222c3e;
    display:flex; flex-direction:column; gap:5px; }
  .sitem{ display:flex; align-items:center; gap:8px; font-size:12px; color:#cbd5e1; }
  .sitem .sdot{ width:7px; height:7px; border-radius:50%; flex-shrink:0; background:#3a465c; }
  .sitem.doing .sdot{ background:var(--pilot); animation:pilot 1.4s ease-out infinite; }
  .sitem.done .sdot{ background:var(--done); }
  .sitem.failed .sdot{ background:var(--failed); }
  .sitem.done .stxt, .sitem.failed .stxt, .sitem.unfinished .stxt{
    color:var(--muted); text-decoration:line-through; text-decoration-thickness:1.5px; }
  .sitem.done .stxt{ text-decoration-color:rgba(34,197,94,.55); }
  .sitem.failed .stxt{ text-decoration-color:rgba(239,68,68,.55); }
  .sitem .stxt{ flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ginfo .rail{ height:2px; gap:2px; margin-top:6px; }

  .graph-wrap{ flex:1 1 340px; min-width:300px; overflow:auto; border-radius:16px;
    border:1px solid #1f293c; padding:2px;
    background:
      radial-gradient(circle at 1px 1px, rgba(148,163,184,.10) 1px, transparent 0) 0 0/22px 22px,
      linear-gradient(180deg,#0e1626,#0b1220);
  }
  .graph-inner{ position:relative; }
  .stage{
    position:absolute; font-size:12px; font-weight:700; letter-spacing:1px;
    color:#7c8aa3; background:#141d2e; border:1px solid #24304a;
    padding:2px 8px; border-radius:20px; white-space:nowrap;
  }
  .conn{ fill:none; stroke-width:2.6; opacity:.72; stroke-linecap:round; }
  .conn.flow{ opacity:1; filter:drop-shadow(0 0 3px currentColor); }
  .packet{ filter:url(#glow); }

  .gnode{
    position:absolute; display:flex; align-items:center; gap:9px; overflow:hidden;
    padding:9px 11px; border-radius:13px;
    background:linear-gradient(150deg,var(--surf1),#141c2b);
    border:1px solid var(--line); border-left:4px solid var(--local);
    box-shadow:0 6px 16px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.03);
    animation:slidein .32s ease; transition:border-color .3s, box-shadow .3s, transform .15s;
  }
  .gnode:hover{ transform:translateY(-2px); box-shadow:0 10px 22px rgba(0,0,0,.5); }
  .gnode.o-local{ border-left-color:var(--local); }
  .gnode.o-out{ border-left-color:var(--out); }
  .gnode.o-in{ border-left-color:var(--in); }
  .gnode.running{ border-color:var(--running); box-shadow:0 0 20px rgba(245,184,0,.28), 0 6px 16px rgba(0,0,0,.4); }
  .gnode.done{ border-color:rgba(34,197,94,.55); }
  .gnode.failed{ border-color:rgba(239,68,68,.6); }
  .gnode.running::after{
    content:''; position:absolute; left:0; right:0; bottom:0; height:2px;
    background:linear-gradient(90deg,transparent,var(--running),transparent);
    background-size:220% 100%; animation:shimmer 1.4s linear infinite;
  }
  .gled{ width:11px; height:11px; border-radius:50%; flex-shrink:0; background:var(--pending); }
  .gled.running{ background:var(--pilot); animation:pilot 1.4s ease-out infinite; }
  .gled.done{ background:var(--done); box-shadow:0 0 9px #22c55eaa; }
  .gled.failed{ background:var(--failed); box-shadow:0 0 9px #ef4444aa; }
  .ginfo{ flex:1; min-width:0; }
  .gid{ font-size:12px; font-weight:700; color:var(--txt); font-family:'SF Mono',ui-monospace,Menlo,monospace; }
  .gmodel{ color:var(--model); font-weight:600; font-family:inherit; }
  .ggoal{ font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; margin-top:2px; }

  .gears{ opacity:0; transform:scale(.7); transition:opacity .3s, transform .3s; line-height:0; }
  .gears.on{ opacity:1; transform:scale(1); }
  .cog{ transform-box:fill-box; transform-origin:center; }
  .gears.on .cog.cw{ animation:spin 3s linear infinite; }
  .gears.on .cog.ccw{ animation:spin 3s linear infinite reverse; }

  .ackbar{ display:none; margin:0 0 15px; padding:14px 16px; border-radius:15px;
    background:linear-gradient(150deg,#1e2942,#141c2e);
    border:1px solid #3a4a68; box-shadow:0 12px 30px rgba(0,0,0,.5);
    animation:slidein .3s ease; }
  body.ack .ackbar{ display:block; }
  body.ack .legend{ display:none; }
  .ackbar .atitle{ font-size:14.5px; font-weight:700; color:#fde68a;
    display:flex; align-items:center; gap:9px; }
  .ackbar .asub{ font-size:12px; color:var(--muted); margin:6px 0 12px; line-height:1.5; }
  .ackrow{ display:flex; gap:9px; align-items:center; flex-wrap:wrap; }
  .ackrow input{ flex:1 1 240px; min-width:180px; background:#0d1523; color:var(--txt);
    border:1px solid #2a3547; border-radius:9px; padding:9px 11px; font-size:12px;
    font-family:inherit; }
  .ackrow input:focus{ outline:none; border-color:#3b82f6; }
  .ackrow input.warn{ border-color:var(--failed); animation:shake .32s; }
  @keyframes shake{ 25%{transform:translateX(-5px)} 75%{transform:translateX(5px)} }
  .btn{ border-radius:9px; padding:9px 15px; font-size:12.5px; font-weight:700;
    cursor:pointer; color:#e6edf7; background:#233047; border:1px solid #3a4a68;
    font-family:inherit; transition:background .18s, transform .12s; }
  .btn:hover:not([disabled]){ background:#2f3f5c; transform:translateY(-1px); }
  .btn.run{ background:#1d4ed8; border-color:#3b82f6; }
  .btn.run:hover:not([disabled]){ background:#2563eb; }
  .btn.no{ background:#3a1d24; border-color:#7f1d1d; color:#fca5a5; }
  .btn.armed{ background:#7c5b00; border-color:#f5b800; color:#fff7d6;
    box-shadow:0 0 14px rgba(245,184,0,.35); }
  .btn.no:hover:not([disabled]){ background:#5b2028; }
  .btn[disabled]{ opacity:.4; cursor:not-allowed; }
  .acount{ font-size:12px; color:var(--muted); margin-left:auto; white-space:nowrap; }
  .ackchk{ display:flex; align-items:center; gap:6px; font-size:12px;
    color:var(--muted); cursor:pointer; user-select:none; white-space:nowrap;
    padding:6px 10px; border:1px solid #2a3547; border-radius:9px;
    background:#0d1523; }
  .ackchk:hover{ border-color:#3a4a68; color:var(--txt); }
  .ackchk input{ accent-color:#f5b800; cursor:pointer; }

  .card.edit{ border-left-color:var(--running); align-items:flex-start; }
  .goalbox{ font-size:12.5px; color:var(--txt); background:#0d1523;
    border:1px solid #2a3547; border-radius:8px; padding:7px 9px; line-height:1.45;
    outline:none; white-space:pre-wrap; }
  .goalbox:focus{ border-color:#3b82f6; background:#0b1220; }
  .sel{ background:#0d1523; color:#e6edf7; border:1px solid #2a3547; border-radius:7px;
    padding:3px 6px; font-size:12px; margin-right:6px; font-family:inherit; cursor:pointer; }
  .sel.mdl{ color:#d8b4fe; }
  .sel:focus{ outline:none; border-color:#3b82f6; }
  .xbtn{ background:#2a1520; color:#fca5a5; border:1px solid #7f1d1d; border-radius:9px;
    width:31px; height:31px; font-size:13px; cursor:pointer; flex-shrink:0;
    transition:background .16s, transform .12s; }
  .xbtn:hover{ background:#5b2028; color:#fee2e2; transform:scale(1.08); }
</style></head>
<body>
  <div class="head">
    <h2 id="boardtitle">🤖 Agents at work</h2>
    <span class="gears" id="gears"></span>
  </div>
  <div class="msum" id="modelsummary"></div>
  <div class="legend"><span id="lgorigin">Origin:</span>
    <span class="dot" style="background:var(--local)"></span><span id="lglocal">local</span>
    <span class="dot" style="background:var(--out)"></span><span id="lgout">sent</span>
    <span class="dot" style="background:var(--in)"></span><span id="lgin">received</span>
  </div>
  <div class="ackbar" id="ackbar">
    <div class="atitle"><span>⏸</span><span id="acktitle"></span></div>
    <div class="asub" id="acksub"></div>
    <div class="ackrow">
      <input id="ackgoal" type="text" placeholder="" style="flex:1">
    </div>
    <div class="ackrow">
      <input id="ackfb" type="text" placeholder="" oninput="onFb()">
      <label class="ackchk" title=""><input type="checkbox" id="ackgrow"><span id="ackgrowlbl"></span></label>
      <button class="btn" id="ackedit"></button>
      <button class="btn no" id="ackno"></button>
      <button class="btn run" id="ackrun"></button>
      <span class="acount" id="ackcount"></span>
    </div>
  </div>
  <div class="intentband" id="intentband" style="display:none">
    <div class="ihead"><span>🧭</span><span id="intenttitle"></span></div>
    <div class="icards" id="icards"></div>
  </div>
  <div class="layout">
    <div class="cards" id="cards"><div class="empty">No active agents.</div></div>
    <div class="graph-wrap" id="graphwrap"></div>
  </div>

<script>
let STATE = [];
let INTENT = [];
let L = {};
let ACK = null;
let DECISION = null;
const SCOL = {pending:'#64748b',running:'#f5b800',done:'#22c55e',failed:'#ef4444',cancelled:'#7c5cff'};

function lb(k, d){ const v = L && L[k]; return (v === undefined || v === null || v === '') ? (d || '') : v; }
function setLabels(json){
  try{ L = JSON.parse(json) || {}; }catch(e){ L = {}; }
  const set = (id, k, d) => { const e=document.getElementById(id); if(e) e.textContent = lb(k, d); };
  set('boardtitle','title','🤖 Agents at work');
  set('lgorigin','origin','Origin:'); set('lglocal','o_local','local');
  set('lgout','o_out','sent');        set('lgin','o_in','received');
  const fb=document.getElementById('ackfb');
  if(fb){ fb.placeholder=lb('ack_fb_hint',''); fb.title=lb('ack_fb_tip',''); }
  set('ackedit','ack_replan','Re-plan'); set('ackno','ack_no','No');
  set('ackrun','ack_run','Run'); set('ackgrowlbl','ack_grow','allow re-scaling');
  const gw=document.getElementById('ackgrow');
  if(gw && gw.parentElement) gw.parentElement.title = lb('ack_grow_tip','');
  if(STATE.length || ACK || INTENT.length){ renderAll(); }
}
window.setLabels = setLabels;

function setIntent(json){
  try{ INTENT = JSON.parse(json) || []; }catch(e){ INTENT = []; }
  renderIntent(); renderGears();
}
window.setIntent = setIntent;

function renderIntent(){
  const band = document.getElementById('intentband'); if(!band) return;
  if(!INTENT.length){ band.style.display='none'; return; }
  band.style.display = 'block';
  const ti = document.getElementById('intenttitle');
  if(ti) ti.textContent = lb('intent_title','Intent — its plan, crossed out live');
  document.getElementById('icards').innerHTML = INTENT.map(it=>{
    const st = it.status || 'pending';
    const cls = (st==='doing') ? 'doing' : (st==='done' ? 'done'
               : (st==='failed' ? 'failed'
               : (st==='unfinished' ? 'unfinished' : '')));
    const led = (st==='doing') ? 'running' : (st==='done' ? 'done'
               : (st==='failed' ? 'failed'
               : (st==='unfinished' ? 'cancelled' : 'pending')));
    const mark = st==='done' ? '✓' : (st==='failed' ? '✗'
                 : (st==='doing' ? '<div class="spinner"></div>' : '⋯'));
    return `<div class="icard ${cls}">
      <div class="led ${led}" style="width:11px;height:11px"></div>
      <div class="igoal">${escapeHtml(it.goal||'')}</div>
      <div style="color:#94a3b8;font-size:13px">${mark}</div>
    </div>`;
  }).join('');
}

const EXP = new Set();
function stepClass(st){
  return (st==='doing'||st==='done'||st==='failed'||st==='unfinished') ? st : '';
}
function railHtml(items){
  return '<div class="rail">'+items.map(it=>
    `<span class="seg ${stepClass(it.status)}"></span>`).join('')+'</div>';
}
function stepsHtml(s){
  const items = Array.isArray(s.intent) ? s.intent : [];
  if(!items.length) return '';
  const n = items.length;
  const done = items.filter(it=>it.status==='done').length;
  const cur = items.find(it=>it.status==='doing')
           || items.find(it=>!it.status || it.status==='pending');
  let now = '';
  if(cur){
    const spin = (cur.status==='doing' && (s.status||'')==='running')
      ? '<div class="spinner"></div>' : '';
    now = `${spin}<span class="ntxt">${escapeHtml(cur.goal||'')}</span>`;
  }else{
    now = `<span class="nok">✓</span><span class="ntxt">${escapeHtml(items[n-1].goal||'')}</span>`;
  }
  let list = '';
  if(EXP.has(String(s.id))){
    list = '<div class="slist">'+items.map(it=>
      `<div class="sitem ${stepClass(it.status)}"><span class="sdot"></span>`+
      `<span class="stxt">${escapeHtml(it.goal||'')}</span></div>`).join('')+'</div>';
  }
  return `<div class="steps">${railHtml(items)}
    <div class="now">${now}<span class="ncount">${done}/${n}</span></div>${list}</div>`;
}
function togSteps(el){
  const id = String(el.dataset.id||'');
  if(!id) return;
  if(EXP.has(id)) EXP.delete(id); else EXP.add(id);
  renderCards();
}
window.togSteps = togSteps;

function escapeHtml(s){ return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function statusClass(s){ return s||'pending'; }

function renderModelSummary(){
  const el=document.getElementById('modelsummary'); if(!el) return;
  const local=STATE.filter(s=>(s.origin||'local')==='local');
  if(!local.length){ el.innerHTML=''; return; }
  const models=[...new Set(local.map(s=>(s.model||'base')))];
  el.innerHTML = models.length>1
    ? '🧠 <b>'+escapeHtml(lb('multi','Multi-model'))+'</b> — '+escapeHtml(lb('multi_sub','specialized per task'))+': '+models.map(escapeHtml).join(' · ')
    : '🧠 <b>'+escapeHtml(lb('single','Single model'))+'</b>: '+escapeHtml(models[0]);
}

function ackCard(s){
  const cur=(s.model||'base'), tgt=(s.target||'local');
  const opts=(arr,sel)=>arr.map(v=>`<option${v===sel?' selected':''}>${escapeHtml(v)}</option>`).join('');
  const models=ACK.models.slice(), targets=ACK.targets.slice();
  if(models.indexOf(cur)<0) models.push(cur);
  if(targets.indexOf(tgt)<0) targets.push(tgt);
  const dep=(s.deps&&s.deps.length)
    ? `<span class="role-badge">←${escapeHtml(s.deps.join(','))}</span>` : '';
  return `<div class="card edit o-local">
    <div class="led pending" style="margin-top:6px"></div>
    <div class="info">
      <div class="goalbox" contenteditable="true" data-id="${escapeHtml(s.id)}"
           title="${escapeHtml(lb('ack_goal_tip',''))}" oninput="onGoal(this)">${escapeHtml(s.goal||'')}</div>
      <div class="meta" style="margin-top:7px">
        <span class="role-badge">[${escapeHtml(s.id)}] ${escapeHtml(s.role||'')}</span>
        <select class="sel mdl" data-id="${escapeHtml(s.id)}" title="${escapeHtml(lb('ack_model_tip',''))}"
                onchange="onSel(this,'model')">${opts(models,cur)}</select>
        <select class="sel" data-id="${escapeHtml(s.id)}" title="${escapeHtml(lb('ack_node_tip',''))}"
                onchange="onSel(this,'target')">${opts(targets,tgt)}</select>
        ${dep}
      </div>
    </div>
    <button class="xbtn" data-id="${escapeHtml(s.id)}" title="${escapeHtml(lb('ack_remove',''))}"
            onclick="onRemove(this)">✕</button>
  </div>`;
}

function renderCards(){
  const c=document.getElementById('cards');
  renderModelSummary();
  if(ACK){ c.innerHTML=STATE.map(ackCard).join(''); return; }
  if(!STATE.length){ c.innerHTML='<div class="empty">'+escapeHtml(lb('empty','No active agents.'))+'</div>'; return; }
  c.innerHTML = STATE.map(s=>{
    const cls=statusClass(s.status);
    const o=(s.origin==='out'||s.origin==='in')?s.origin:'local';
    const peer=s.peer?escapeHtml(String(s.peer)):'';
    const obadge=o==='local'?'⌂':(o==='out'?'→ '+(peer||'remote'):'← '+(peer||'remote'));
    const right=cls==='running'?'<div class="spinner"></div>':(cls==='done'?'✓':(cls==='failed'?'✗':'⋯'));
    const steps=stepsHtml(s);
    const tog=steps?` has-steps" data-id="${escapeHtml(String(s.id))}" onclick="togSteps(this)`:'';
    return `<div class="card ${cls} o-${o}${tog}">
      <div class="led ${cls}"></div>
      <div class="info">
        <div class="goal">${escapeHtml(s.goal||'')}</div>
        <div class="meta"><span class="origin-badge o-${o}">${obadge}</span><span class="role-badge">${escapeHtml(s.role||'general')}</span><span class="model-badge">🧠 ${escapeHtml(s.model||'base')}</span>
          ${s.attempts>1 ? escapeHtml(lb('attempt','attempt'))+' '+s.attempts : ''}
          ${s.error ? '· '+escapeHtml(String(s.error).slice(0,40)) : ''}</div>
        ${steps}
      </div>
      <div style="color:#94a3b8;font-size:14px">${right}</div>
    </div>`;
  }).join('');
}

function renderGraph(){
  const w=document.getElementById('graphwrap');
  if(STATE.length<2){ w.innerHTML='<div class="empty" style="padding:34px">'+escapeHtml(lb('graph_hint','The dependency flow appears when the orchestrator plans 2+ sub-tasks.'))+'</div>'; return; }
  const byId={}; STATE.forEach(s=>byId[s.id]=s);
  const level={};
  function lvl(id,seen){
    if(level[id]!=null) return level[id];
    if(seen.has(id)) return 0;
    seen.add(id);
    const s=byId[id]; if(!s||!s.deps||!s.deps.length){ level[id]=0; return 0; }
    const m=Math.max(...s.deps.map(d=>lvl(d,seen)));
    level[id]=m+1; return m+1;
  }
  STATE.forEach(s=>lvl(s.id,new Set()));
  const levels={}; STATE.forEach(s=>{ (levels[level[s.id]]=levels[level[s.id]]||[]).push(s); });
  const maxLvl=Math.max(...Object.keys(levels).map(Number));
  const maxRows=Math.max(...Object.values(levels).map(a=>a.length));

  const cardW=182, cardH=64, colW=250, rowH=96, padX=16, labelBand=30, padY=labelBand+8;
  const pos={};
  Object.keys(levels).forEach(Lv=>{ levels[Lv].forEach((s,i)=>{ pos[s.id]={x:padX+Lv*colW, y:padY+i*rowH}; }); });
  const W=padX*2 + maxLvl*colW + cardW;
  const H=padY + Math.max(1,maxRows)*rowH + 6;

  let stages='';
  for(let Lv=0; Lv<=maxLvl; Lv++){
    stages+=`<div class="stage" style="left:${padX+Lv*colW}px;top:8px">${escapeHtml(lb('stage','STAGE'))} ${Lv+1}</div>`;
  }
  let grads='', conns='', packets='', gi=0;
  STATE.forEach(s=>{
    (s.deps||[]).forEach(d=>{
      if(!pos[d]||!pos[s.id]) return;
      const x1=pos[d].x+cardW, y1=pos[d].y+cardH/2;
      const x2=pos[s.id].x,    y2=pos[s.id].y+cardH/2;
      const mx=(x1+x2)/2;
      const dd=`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
      const dCol=SCOL[byId[d]&&byId[d].status||'pending']||'#64748b';
      const tCol=SCOL[s.status||'pending']||'#64748b';
      const flow=(s.status==='running')||(byId[d]&&byId[d].status==='running');
      const id='eg'+(gi++);
      grads+=`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}">
        <stop offset="0" stop-color="${dCol}"/><stop offset="1" stop-color="${tCol}"/></linearGradient>`;
      conns+=`<path class="conn ${flow?'flow':''}" style="color:${tCol}" stroke="url(#${id})" d="${dd}"/>`;
      if(flow){
        packets+=`<circle class="packet" r="3.4" fill="${tCol}">
          <animateMotion dur="1.15s" repeatCount="indefinite" path="${dd}"/></circle>`;
      }
    });
  });
  let cards='';
  STATE.forEach(s=>{
    const p=pos[s.id]; const cls=statusClass(s.status);
    const o=(s.origin==='out'||s.origin==='in')?s.origin:'local';
    cards+=`<div class="gnode ${cls} o-${o}" style="left:${p.x}px;top:${p.y}px;width:${cardW}px;min-height:${cardH}px">
      <div class="gled ${cls}"></div>
      <div class="ginfo">
        <div class="gid">[${escapeHtml(String(s.id))}] <span class="gmodel">🧠${escapeHtml(s.model||'base')}</span></div>
        <div class="ggoal">${escapeHtml(String(s.goal||'').slice(0,46))}</div>
        ${(Array.isArray(s.intent)&&s.intent.length) ? railHtml(s.intent) : ''}
      </div>
    </div>`;
  });
  w.innerHTML=`<div class="graph-inner" style="width:${W}px;height:${H}px">
    <svg width="${W}" height="${H}" style="position:absolute;left:0;top:0;overflow:visible" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <filter id="glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.4" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        ${grads}
      </defs>
      ${conns}${packets}
    </svg>${stages}${cards}</div>`;
}

function cogPath(cx,cy,rO,rI,rHole,teeth){
  let d=''; const n=teeth*2, step=Math.PI/teeth;
  for(let i=0;i<n;i++){
    const r=(i%2===0)?rO:rI, a=i*step-Math.PI/2;
    const x=cx+r*Math.cos(a), y=cy+r*Math.sin(a);
    d+=(i?'L':'M')+x.toFixed(2)+','+y.toFixed(2);
  }
  d+='Z';
  d+=` M${(cx+rHole).toFixed(2)},${cy} A${rHole},${rHole} 0 1,0 ${(cx-rHole).toFixed(2)},${cy} A${rHole},${rHole} 0 1,0 ${(cx+rHole).toFixed(2)},${cy}Z`;
  return d;
}
function buildGears(){
  const el=document.getElementById('gears'); if(!el) return;
  const A=cogPath(16,15,12,8.5,3.6,9);
  const B=cogPath(33,23,9,6.2,2.8,7);
  el.innerHTML=`<svg width="46" height="36" viewBox="0 0 46 36">
    <g class="cog cw"><path d="${A}" fill="#f5b800" fill-rule="evenodd" style="filter:drop-shadow(0 0 4px #f5b80088)"/></g>
    <g class="cog ccw"><path d="${B}" fill="#fbbf24" fill-rule="evenodd" style="filter:drop-shadow(0 0 3px #fbbf2488)"/></g>
  </svg>`;
}
function renderGears(){
  const g=document.getElementById('gears'); if(!g) return;
  g.classList.toggle('on', STATE.some(s=>(s.status||'')==='running')
                        || INTENT.some(s=>(s.status||'')==='doing'));
}

function renderAll(){ renderCards(); renderGraph(); renderGears(); renderCount(); renderIntent(); }

function byId(id){ return STATE.filter(s=>String(s.id)===String(id))[0]; }

function renderCount(){
  const el=document.getElementById('ackcount'); if(!el) return;
  if(!ACK){ el.textContent=''; return; }
  el.textContent=lb('ack_count','').replace('{n}', STATE.length);
  const run=document.getElementById('ackrun');
  if(run && !DECISION) run.disabled = (STATE.length===0);
}



function onGoal(el){
  const s=byId(el.dataset.id);
  if(s){ s.goal = el.innerText; renderGraph(); }
}
function onSel(el, field){
  const s=byId(el.dataset.id);
  if(!s) return;
  s[field] = el.value;
  renderGraph();               
}


function onRemove(el){
  const id=String(el.dataset.id);
  STATE = STATE.filter(s=>String(s.id)!==id);
  STATE.forEach(s=>{ if(s.deps) s.deps = s.deps.filter(d=>String(d)!==id); });
  renderAll();
}

function onFb(){
  const fb=document.getElementById('ackfb'), ed=document.getElementById('ackedit');
  if(ed) ed.classList.toggle('armed', !!(fb && fb.value.trim()));
}
window.onFb=onFb;

function ackBusy(msg){
  ['ackrun','ackno','ackedit'].forEach(i=>{
    const b=document.getElementById(i); if(b) b.disabled=true; });
  const sub=document.getElementById('acksub'); if(sub) sub.textContent=msg;
}

function startAck(json){
  let d={};
  try{ d=JSON.parse(json)||{}; }catch(e){ d={}; }
  ACK={targets:(d.targets&&d.targets.length?d.targets:['local']),
       models:(d.models&&d.models.length?d.models:['base'])};
  DECISION=null;
  STATE=(d.subtasks||[]).map(s=>Object.assign({}, s, {status:'pending'}));
  document.body.classList.add('ack');
  const ti=document.getElementById('acktitle'); if(ti) ti.textContent=lb('ack_title','');
  const su=document.getElementById('acksub');   if(su) su.textContent=lb('ack_sub','');
  const fb=document.getElementById('ackfb');
  if(fb){ fb.value=''; fb.classList.remove('warn'); fb.placeholder=lb('ack_fb_hint',''); }
  const gl=document.getElementById('ackgoal');
  if(gl){ gl.placeholder=lb('ack_goal_hint',''); }
  const gw=document.getElementById('ackgrow');
  if(gw) gw.checked=false;
  const _run=document.getElementById('ackrun');
  if(_run) delete _run.dataset.warned;
  onFb();
  ['ackrun','ackno','ackedit'].forEach(i=>{
    const b=document.getElementById(i); if(b) b.disabled=false; });
  renderAll();
  window.scrollTo(0,0);
}
function stopAck(){
  ACK=null; DECISION=null;
  document.body.classList.remove('ack');
  renderAll();
}

function getAck(){ return DECISION ? JSON.stringify(DECISION) : null; }

window.startAck=startAck; window.stopAck=stopAck; window.getAck=getAck;
window.onGoal=onGoal; window.onSel=onSel; window.onRemove=onRemove;

function setState(json){
  

  if(ACK && !DECISION) return;
  try{ STATE=JSON.parse(json)||[]; }catch(e){ STATE=[]; }
  renderCards(); renderGraph(); renderGears();
}
window.setState=setState;

(function wireAck(){
  const run=document.getElementById('ackrun');
  const no=document.getElementById('ackno');
  const ed=document.getElementById('ackedit');
  if(run) run.onclick=function(){
    if(!STATE.length) return;
    const fb=document.getElementById('ackfb');
    if(fb && fb.value.trim() && !run.dataset.warned){
      run.dataset.warned='1';
      const sub=document.getElementById('acksub');
      if(sub) sub.textContent=lb('ack_run_ignores_note','');
      onFb();
      return;
    }
    const _grow=document.getElementById('ackgrow');
    const _goal=document.getElementById('ackgoal');
    DECISION={decision:'run', allow_expand:!!(_grow && _grow.checked),
              goal:((_goal && _goal.value) || '').trim(),
              subtasks:STATE.map(s=>({
      id:s.id, goal:s.goal||'', deps:s.deps||[],
      model:((s.model||'base')==='base'?'':s.model),
      target:(s.target||'local')}))};
    ackBusy(lb('ack_launching',''));
  };
  if(no) no.onclick=function(){ DECISION={decision:'cancel'}; ackBusy(lb('ack_cancelling','')); };
  if(ed) ed.onclick=function(){
    const fb=document.getElementById('ackfb');
    const v=(fb && fb.value || '').trim();
    if(!v){ if(fb){ fb.classList.add('warn'); fb.placeholder=lb('ack_fb_required','');
                    fb.focus(); setTimeout(()=>fb.classList.remove('warn'),420); } return; }
    DECISION={decision:'edit', feedback:v};
    ackBusy(lb('ack_replanning',''));
  };
})();
buildGears();
</script>
</body></html>"""


class AgentsWidget(QWidget):
    _update_sig = pyqtSignal(str)
    _intent_sig = pyqtSignal(str)
    _ask_sig = pyqtSignal(str)
    ack_decided = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setHtml(_HTML)
        layout.addWidget(self.view)
        self._ready = False
        self.view.loadFinished.connect(self._on_loaded)
        self._pending = None
        self._pending_ask = None
        self._pending_intent = None
        self._labels_json = None
        self._update_sig.connect(self._apply_update)
        self._intent_sig.connect(self._apply_intent)
        self._ask_sig.connect(self._apply_ask)
        self._ack_timer = QTimer(self)
        self._ack_timer.setInterval(220)
        self._ack_timer.timeout.connect(self._poll_ack)
        self._polling = False

    def _on_loaded(self, ok):
        self._ready = True
        if self._labels_json is not None:
            self.view.page().runJavaScript(
                "setLabels(" + json.dumps(self._labels_json) + ");")
        if self._pending_ask is not None:
            self._apply_ask(self._pending_ask)
            self._pending_ask = None
        elif self._pending is not None:
            self._apply_update(self._pending)
            self._pending = None
        if self._pending_intent is not None:
            self._apply_intent(self._pending_intent)
            self._pending_intent = None

    def set_labels(self, labels):
        try:
            self._labels_json = json.dumps(labels, ensure_ascii=False)
        except Exception:
            return
        if self._ready:
            self.view.page().runJavaScript(
                "setLabels(" + json.dumps(self._labels_json) + ");")

    def ask_plan(self, subtasks, targets, models):
        try:
            payload = json.dumps({"subtasks": subtasks, "targets": list(targets),
                                  "models": list(models)}, ensure_ascii=False)
        except Exception:
            return
        self._ask_sig.emit(payload)

    def _apply_ask(self, payload):
        if not self._ready:
            self._pending_ask = payload
            return
        self.view.page().runJavaScript("startAck(" + json.dumps(payload) + ");")
        self._ack_timer.start()

    def close_ack(self):
        self._ack_timer.stop()
        self._pending_ask = None
        if self._ready:
            self.view.page().runJavaScript("stopAck();")

    def _poll_ack(self):
        if not self._ready or self._polling:
            return
        self._polling = True
        self.view.page().runJavaScript("getAck();", self._on_ack_result)

    def _on_ack_result(self, raw):
        self._polling = False
        if not raw:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        if not isinstance(data, dict) or not data.get("decision"):
            return
        self._ack_timer.stop()
        self.ack_decided.emit(data)

    def update_state(self, subtasks):
        try:
            payload = json.dumps(subtasks, ensure_ascii=False)
        except Exception:
            payload = "[]"
        self._update_sig.emit(payload)

    def _apply_update(self, payload):
        if not self._ready:
            self._pending = payload
            return
        js = "setState(" + json.dumps(payload) + ");"
        self.view.page().runJavaScript(js)

    def set_intent(self, items):
        try:
            payload = json.dumps(items or [], ensure_ascii=False)
        except Exception:
            payload = "[]"
        self._intent_sig.emit(payload)

    def _apply_intent(self, payload):
        if not self._ready:
            self._pending_intent = payload
            return
        self.view.page().runJavaScript("setIntent(" + json.dumps(payload) + ");")

    def clear(self):
        self.update_state([])
        self.set_intent([])
