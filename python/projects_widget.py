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
    --alta:#ef4444; --media:#f5b800; --baja:#22c55e; --none:#64748b;
    --cyc:#ef4444; --miss:#ef4444;
  }
  html,body{
    margin:0; padding:14px; box-sizing:border-box; color:var(--txt);
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
  .head{ display:flex; align-items:baseline; gap:10px; margin:0 0 12px; }
  h2{ color:#f4f7fc; margin:0; font-size:16px; font-weight:700; }
  .sub{ font-size:12px; color:var(--muted); }
  .empty{ color:#64748b; font-size:13px; padding:26px; text-align:center; }

  .cycband{ display:none; margin:0 0 12px; padding:9px 12px; border-radius:11px;
    background:#2a1215; border:1px solid #7f1d1d; color:#fca5a5;
    font-size:12px; line-height:1.5; }
  .cycband b{ color:#fecaca; }

  .tcard{ position:relative; overflow:hidden; display:flex; flex-direction:column;
    gap:7px; padding:11px 13px; border-radius:13px;
    background:linear-gradient(150deg,var(--surf1),var(--surf2));
    border:1px solid var(--line); border-left:5px solid var(--none);
    box-shadow:0 6px 16px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.03);
    transition:border-color .25s, transform .12s; }
  .tcard:hover{ transform:translateY(-1px); }
  .tcard.p-alta{ border-left-color:var(--alta); }
  .tcard.p-media{ border-left-color:var(--media); }
  .tcard.p-baja{ border-left-color:var(--baja); }
  .tcard.incycle{ border-color:var(--cyc);
    box-shadow:0 0 16px rgba(239,68,68,.25), 0 6px 16px rgba(0,0,0,.35); }
  .tcard.stub{ border:1.5px dashed var(--miss); border-left:5px dashed var(--miss);
    background:rgba(42,18,21,.55); }
  .trow{ display:flex; align-items:center; gap:8px; }
  .ttitle{ flex:1; min-width:0; font-size:13px; font-weight:600; color:var(--txt);
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .prio{ font-size:12px; font-weight:700; padding:1px 8px; border-radius:8px;
    flex-shrink:0; }
  .prio.p-alta{ background:#5e1616; color:#fca5a5; }
  .prio.p-media{ background:#4d3c07; color:#fde68a; }
  .prio.p-baja{ background:#0f3d24; color:#86efac; }
  .prio.p-none{ background:#2a3346; color:#cbd5e1; }
  .tdesc{ font-size:12px; color:var(--muted); line-height:1.4; max-height:31px;
    overflow:hidden; }
  .stubwhy{ font-size:12px; color:#fca5a5; }

  .pwrap{ display:flex; align-items:center; gap:7px; }
  .pstep{ width:24px; height:24px; border-radius:7px; border:1px solid var(--line);
    background:#0d1523; color:var(--txt); font-size:13px; cursor:pointer;
    flex-shrink:0; line-height:1; }
  .pstep:hover{ border-color:#3b82f6; background:#152036; }
  .pbar{ position:relative; flex:1; height:20px; border-radius:7px;
    background:#0d1523; border:1px solid var(--line); overflow:hidden;
    cursor:pointer; }
  .pfill{ position:absolute; inset:0 auto 0 0; border-radius:6px 0 0 6px;
    background:linear-gradient(90deg,#1d4ed8,#3b82f6); transition:width .25s; }
  .tcard.p-alta  .pfill{ background:linear-gradient(90deg,#7f1d1d,#ef4444); }
  .tcard.p-media .pfill{ background:linear-gradient(90deg,#7c5b00,#f5b800); }
  .tcard.p-baja  .pfill{ background:linear-gradient(90deg,#14532d,#22c55e); }
  .pnum{ position:relative; z-index:1; display:block; text-align:center;
    font-size:12px; font-weight:700; color:#f4f7fc; line-height:20px;
    text-shadow:0 1px 2px rgba(0,0,0,.7); }
  .pnum input{ width:52px; background:#0b1220; color:var(--txt);
    border:1px solid #3b82f6; border-radius:5px; font-size:12px; padding:1px 4px;
    text-align:center; }

  .chips{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .chip{ font-size:12px; padding:2px 8px; border-radius:8px; background:#16305e;
    color:#93c5fd; cursor:pointer; border:1px solid transparent; }
  .chip:hover{ border-color:#3b82f6; }
  .chip.warn{ background:#5e1616; color:#fca5a5; }
  .chip.gate{ background:#4d3c07; color:#fde68a; cursor:default; }
  .chip.gate.go{ background:#0f3d24; color:#86efac; }
  .depnames{ font-size:12px; color:var(--muted); white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; min-width:0; flex:1; }

  #board{ display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
    gap:12px; }

  #treewrap{ overflow:auto; border-radius:15px; border:1px solid #1f293c;
    padding:2px; background:
      radial-gradient(circle at 1px 1px, rgba(148,163,184,.10) 1px, transparent 0) 0 0/22px 22px,
      linear-gradient(180deg,#0e1626,#0b1220); }
  #treeinner{ position:relative; }
  #treeinner .tcard{ position:absolute; }
  .tcard.mini{ width:200px; padding:8px 10px 9px; gap:5px; border-radius:11px; }
  .tcard.mini .ttitle{ font-size:12px; }
  .tcard.mini .tdesc{ display:none; }
  .tcard.mini .depnames{ display:none; }
  .tcard.mini .prio{ font-size:12px; padding:1px 6px; }
  .tcard.mini .pbar{ height:16px; }
  .tcard.mini .pnum{ line-height:16px; font-size:12px; }
  .tcard.mini .pnum input{ width:44px; font-size:12px; }
  .tcard.mini .pstep{ width:20px; height:20px; font-size:12px; border-radius:6px; }
  .tcard.mini .chip{ font-size:9.5px; padding:1px 6px; }
  .conn{ fill:none; stroke-width:2.6; opacity:.85; stroke-linecap:round;
    filter:drop-shadow(0 0 2px rgba(59,130,246,.45)); }
  .conn.cyc{ stroke:var(--cyc); opacity:1;
    filter:drop-shadow(0 0 3px rgba(239,68,68,.7)); stroke-dasharray:7 5; }
  .conn.miss{ stroke:var(--miss); stroke-dasharray:3 5;
    filter:drop-shadow(0 0 2px rgba(239,68,68,.45)); }

  .day{ font-size:12px; font-weight:700; letter-spacing:.6px; color:#e6edf7;
    margin:16px 0 8px; text-transform:uppercase; }
  .ev{ display:flex; align-items:baseline; gap:9px; padding:7px 11px;
    border-radius:11px; background:linear-gradient(150deg,var(--surf1),var(--surf2));
    border:1px solid var(--line); margin-bottom:7px; font-size:12px; }
  .ev .tm{ font-family:'SF Mono',ui-monospace,Menlo,monospace; font-size:12px;
    color:#7c8aa3; flex-shrink:0; }
  .ev .txt{ flex:1; min-width:0; line-height:1.45; }
  .ev .txt b{ color:#bfdbfe; font-weight:600; }
  .ev .txt i{ color:var(--muted); font-style:normal; }
  .hostchip{ font-size:12px; padding:1px 7px; border-radius:7px;
    background:#2a3346; color:#cbd5e1; flex-shrink:0; }

  #veil{ display:none; position:fixed; inset:0; background:rgba(5,9,18,.72);
    z-index:40; }
  #picker{ display:none; position:fixed; z-index:41; left:50%; top:50%;
    transform:translate(-50%,-50%); width:min(460px,92vw); max-height:74vh;
    overflow:auto; padding:16px; border-radius:15px;
    background:linear-gradient(150deg,#1e2942,#141c2e);
    border:1px solid #3a4a68; box-shadow:0 14px 34px rgba(0,0,0,.6); }
  #picker h3{ margin:0 0 4px; font-size:13.5px; color:#f4f7fc; }
  #picker .sub{ margin:0 0 12px; display:block; }
  .prow{ display:flex; align-items:center; gap:9px; padding:7px 9px;
    border-radius:9px; cursor:pointer; font-size:12.5px; }
  .prow:hover{ background:#1b2740; }
  .prow input{ accent-color:#3b82f6; }
  .prow .miss{ color:#fca5a5; }
  .pbtns{ display:flex; gap:9px; justify-content:flex-end; margin-top:13px; }
  .btn{ border-radius:9px; padding:8px 15px; font-size:12.5px; font-weight:700;
    cursor:pointer; color:#e6edf7; background:#233047; border:1px solid #3a4a68; }
  .btn:hover{ background:#2f3f5c; }
  .btn.run{ background:#1d4ed8; border-color:#3b82f6; }
</style></head>
<body>
  <div class="head"><h2 id="ptitle"></h2><span class="sub" id="psub"></span></div>
  <div class="cycband" id="cycband"></div>
  <div id="board"></div>
  <div id="treewrap" style="display:none"><div id="treeinner"></div></div>
  <div id="timeline" style="display:none"></div>
  <div id="veil" onclick="closePicker()"></div>
  <div id="picker"></div>

<script>
let S = {project:'', tasks:[], missing:{}, events:[]};
let L = {};
let VIEW = 'board';
let EDITS = [];
let PICK = null;
const STEP_T = {};

function lb(k,d){ const v=L&&L[k]; return (v===undefined||v===null||v==='')?(d||''):v; }
function esc(s){ return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(k,d,vars){ let s=lb(k,d);
  Object.keys(vars||{}).forEach(x=>{ s=s.split('{'+x+'}').join(vars[x]); });
  return s; }
function pcls(p){ p=String(p||'').toLowerCase();
  if(p==='alta'||p==='high') return 'p-alta';
  if(p==='media'||p==='normal'||p==='medium') return 'p-media';
  if(p==='baja'||p==='low') return 'p-baja';
  return 'p-none'; }
function byU(u){ return S.tasks.find(t=>t.uuid===u); }

function setLabels(json){ try{ L=JSON.parse(json)||{}; }catch(e){ L={}; }
  render(); }
window.setLabels = setLabels;

function setState(json){
  try{ const s=JSON.parse(json); if(s && typeof s==='object') S=Object.assign(
    {project:'',tasks:[],missing:{},events:[]}, s); }catch(e){ return; }
  render(); }
window.setState = setState;

function showView(v){ if(['board','tree','timeline'].indexOf(v)>=0){ VIEW=v; render(); } }
window.showView = showView;

function drainEdits(){ const out=JSON.stringify(EDITS); EDITS=[]; return out; }
window.drainEdits = drainEdits;

function queueEdit(u, field, value){
  const t=byU(u); if(!t) return;
  EDITS=EDITS.filter(e=>!(e.uuid===u && e.field===field));
  EDITS.push({uuid:u, id:t.id, rev:t.rev||1, project:S.project,
              field:field, value:value});
}


function stepProg(u, d){
  const t=byU(u); if(!t) return;
  t.progress=Math.max(0,Math.min(100,(parseInt(t.progress)||0)+d));
  paintProg(u);
  clearTimeout(STEP_T[u]);
  STEP_T[u]=setTimeout(()=>{ queueEdit(u,'progress',t.progress); },650);
}
function barClick(ev,u){
  const r=ev.currentTarget.getBoundingClientRect();
  const v=Math.round(((ev.clientX-r.left)/r.width)*20)*5;
  const t=byU(u); if(!t) return;
  t.progress=Math.max(0,Math.min(100,v)); paintProg(u);
  queueEdit(u,'progress',t.progress);
}
function editNum(ev,u){
  ev.stopPropagation();
  const t=byU(u); if(!t) return;
  const span=ev.currentTarget;
  span.innerHTML='<input type="number" min="0" max="100" step="5" value="'
    +(parseInt(t.progress)||0)+'">';
  const inp=span.querySelector('input');
  inp.focus(); inp.select();
  const commit=()=>{ let v=parseInt(inp.value); if(isNaN(v)) v=t.progress;
    t.progress=Math.max(0,Math.min(100,v));
    queueEdit(u,'progress',t.progress); paintProg(u); };
  inp.addEventListener('keydown',e=>{
    if(e.key==='Enter'){ commit(); }
    if(e.key==='Escape'){ paintProg(u); }
    e.stopPropagation(); });
  inp.addEventListener('blur',commit);
  inp.addEventListener('click',e=>e.stopPropagation());
}
function paintProg(u){
  const t=byU(u); if(!t) return;
  document.querySelectorAll('[data-u="'+u+'"]').forEach(card=>{
    const f=card.querySelector('.pfill'), n=card.querySelector('.pnum');
    if(f) f.style.width=(parseInt(t.progress)||0)+'%';
    if(n) n.textContent=(parseInt(t.progress)||0)+'%';
  });
}


function openDeps(ev,u){
  ev.stopPropagation();
  const t=byU(u); if(!t) return;
  PICK=u;
  const cur=new Set(t.deps||[]);
  let rows='';
  S.tasks.filter(x=>x.uuid && x.uuid!==u).forEach(x=>{
    rows+='<label class="prow"><input type="checkbox" value="'+esc(x.uuid)+'"'
      +(cur.has(x.uuid)?' checked':'')+'><span>'+esc(x.title||'')+'</span></label>';
  });
  (t.deps||[]).forEach(d=>{
    const m=S.missing[d];
    if(!m) return;
    rows+='<label class="prow"><input type="checkbox" value="'+esc(d)+'" checked>'
      +'<span class="miss">'+esc(m.title||d.slice(0,8))+' — '+esc(missWhy(m))
      +'</span></label>';
  });
  document.getElementById('picker').innerHTML=
    '<h3>'+esc(fmt('deps_title','«{t}» depends on…',{t:t.title||''}))+'</h3>'
    +'<span class="sub">'+esc(lb('deps_sub',''))+'</span>'
    +(rows||'<div class="empty">'+esc(lb('deps_none_avail',''))+'</div>')
    +'<div class="pbtns"><button class="btn" onclick="closePicker()">'
    +esc(lb('cancel','Cancel'))+'</button>'
    +'<button class="btn run" onclick="saveDeps()">'+esc(lb('save','Save'))
    +'</button></div>';
  document.getElementById('veil').style.display='block';
  document.getElementById('picker').style.display='block';
}
function closePicker(){ PICK=null;
  document.getElementById('veil').style.display='none';
  document.getElementById('picker').style.display='none'; }
function saveDeps(){
  if(PICK==null) return;
  const t=byU(PICK);
  const sel=[...document.querySelectorAll('#picker input:checked')].map(i=>i.value);
  if(t){ t.deps=sel; queueEdit(PICK,'deps',sel); }
  closePicker(); render();
}

function missWhy(m){
  if(!m) return lb('miss_unknown','does not exist here');
  if(m.state==='deleted') return lb('miss_deleted','deleted')+(m.editor?' · '+m.editor:'');
  if(m.state==='elsewhere') return fmt('miss_elsewhere','in «{p}»',{p:m.project||'?'});
  return lb('miss_unknown','does not exist here');
}


function depGate(t){
  const ds=(t.deps||[]).filter(d=>byU(d)||S.missing[d]);
  if(!ds.length) return null;
  const ok=ds.filter(d=>{ const x=byU(d);
    return x && (parseInt(x.progress)||0)>=100; }).length;
  return {ok:ok, n:ds.length, go:ok===ds.length};
}

function cardHtml(t, cyc, mini){
  const pc=pcls(t.priority);
  const depNames=(t.deps||[]).map(d=>{
    const x=byU(d); if(x) return esc(x.title||'');
    const m=S.missing[d];
    return '<span style="color:#fca5a5">'+esc((m&&m.title)||d.slice(0,8))+'</span>';
  }).join(' · ');
  const p=parseInt(t.progress)||0;
  return '<div class="tcard '+pc+(cyc?' incycle':'')+(mini?' mini':'')
    +'" data-u="'+esc(t.uuid)+'">'
    +'<div class="trow"><div class="ttitle" title="'+esc(t.title||'')+'">'
    +esc(t.title||'')+'</div>'
    +'<span class="prio '+pc+'">'+esc(t.priority||'')+'</span></div>'
    +(t.description?'<div class="tdesc" title="'+esc(t.description)+'">'
      +esc(t.description)+'</div>':'')
    +'<div class="pwrap">'
    +'<button class="pstep" onclick="stepProg(\''+esc(t.uuid)+'\',-5)">−</button>'
    +'<div class="pbar" onclick="barClick(event,\''+esc(t.uuid)+'\')">'
    +'<div class="pfill" style="width:'+p+'%"></div>'
    +'<span class="pnum" onclick="editNum(event,\''+esc(t.uuid)+'\')" title="'
    +esc(lb('pnum_tip',''))+'">'+p+'%</span></div>'
    +'<button class="pstep" onclick="stepProg(\''+esc(t.uuid)+'\',5)">+</button>'
    +'</div>'
    +'<div class="chips"><span class="chip'+((t.deps||[]).some(d=>S.missing[d])?' warn':'')
    +'" title="'+esc(lb('deps_tip',''))+'" onclick="openDeps(event,\''
    +esc(t.uuid)+'\')">🔗 '+((t.deps||[]).length)+'</span>'
    +(function(){ const g=depGate(t); if(!g) return '';
      return '<span class="chip gate'+(g.go?' go':'')+'" title="'
        +esc(g.go?lb('ready_tip',''):fmt('blocked_tip','',{k:g.ok,n:g.n}))
        +'">'+(g.go?'✔':'⛔')+' '+g.ok+'/'+g.n+'</span>'; })()
    +(depNames?'<span class="depnames" title="'+esc(lb('depends_on','depends on'))
      +'">← '+depNames+'</span>':'')
    +'</div></div>';
}
function stubHtml(d){
  const m=S.missing[d]||{};
  return '<div class="tcard stub mini" data-stub="'+esc(d)+'">'
    +'<div class="trow"><div class="ttitle">'+esc(m.title||d.slice(0,8))+'</div></div>'
    +'<div class="stubwhy">⚠ '+esc(missWhy(m))+'</div></div>';
}




function sccs(){
  const idx={}, low={}, onstk={}, stk=[]; let n=0; const out=[];
  const adj={};
  S.tasks.forEach(t=>{ adj[t.uuid]=adj[t.uuid]||[];
    (t.deps||[]).forEach(d=>{ if(byU(d)){ (adj[d]=adj[d]||[]).push(t.uuid); } }); });
  function strong(v){
    const st=[[v,0]];
    while(st.length){
      const fr=st[st.length-1], u=fr[0];
      if(fr[1]===0){ idx[u]=low[u]=n++; stk.push(u); onstk[u]=true; }
      let advanced=false;
      const nb=adj[u]||[];
      while(fr[1]<nb.length){
        const w=nb[fr[1]++];
        if(idx[w]===undefined){ st.push([w,0]); advanced=true; break; }
        else if(onstk[w]){ low[u]=Math.min(low[u],idx[w]); }
      }
      if(advanced) continue;
      if(low[u]===idx[u]){
        const comp=[]; let w;
        do{ w=stk.pop(); onstk[w]=false; comp.push(w); }while(w!==u);
        if(comp.length>1) out.push(comp);
      }
      st.pop();
      if(st.length){ const p=st[st.length-1][0]; low[p]=Math.min(low[p],low[u]); }
    }
  }
  S.tasks.forEach(t=>{ if(t.uuid && idx[t.uuid]===undefined) strong(t.uuid); });
  return out;
}

function render(){
  const boardEl=document.getElementById('board');
  const treeEl=document.getElementById('treewrap');
  const tlEl=document.getElementById('timeline');
  boardEl.style.display = VIEW==='board' ? 'grid' : 'none';
  treeEl.style.display  = VIEW==='tree'  ? 'block' : 'none';
  tlEl.style.display    = VIEW==='timeline' ? 'block' : 'none';
  document.getElementById('ptitle').textContent =
    (VIEW==='timeline'?'🕒 ':(VIEW==='tree'?'🕸 ':'🧩 '))+(S.project||'');
  document.getElementById('psub').textContent =
    fmt('subcount','{n} task(s)',{n:S.tasks.length});
  const comps=sccs();
  const cyc=new Set(); comps.forEach(c=>c.forEach(u=>cyc.add(u)));
  const band=document.getElementById('cycband');
  if(comps.length && VIEW!=='timeline'){
    band.style.display='block';
    band.innerHTML='<b>'+esc(lb('cycle','⚠ dependency cycle — nobody may have created it: two replicas can converge into one. Shown, never auto-broken:'))+'</b><br>'
      +comps.map(c=>{
        const names=c.map(u=>esc((byU(u)||{}).title||u.slice(0,8)));
        return names.concat(names[0]).join(' → ');
      }).join('<br>');
  } else band.style.display='none';
  if(VIEW==='board') renderBoard(cyc);
  else if(VIEW==='tree') renderTree(cyc,comps);
  else renderTimeline();
}

function renderBoard(cyc){
  const el=document.getElementById('board');
  if(!S.tasks.length){ el.innerHTML='<div class="empty" style="grid-column:1/-1">'
    +esc(lb('board_empty','No tasks in this project.'))+'</div>'; return; }
  el.innerHTML=S.tasks.map(t=>cardHtml(t,cyc.has(t.uuid))).join('');
}

function renderTree(cyc,comps){
  const inner=document.getElementById('treeinner');
  if(!S.tasks.length){ inner.style.width='auto'; inner.style.height='auto';
    inner.innerHTML='<div class="empty">'+esc(lb('board_empty','No tasks in this project.'))+'</div>'; return; }
  const live={}; S.tasks.forEach(t=>live[t.uuid]=t);
  const inComp={}; comps.forEach((c,i)=>c.forEach(u=>inComp[u]=i));

  const par={};
  function find(x){ while(par[x]!==x){ par[x]=par[par[x]]; x=par[x]; } return x; }
  S.tasks.forEach(t=>{ if(t.uuid) par[t.uuid]=t.uuid; });
  Object.keys(S.missing).forEach(d=>{ par[d]=d; });
  S.tasks.forEach(t=>(t.deps||[]).forEach(d=>{
    if(par[d]!==undefined && par[t.uuid]!==undefined){
      const a=find(t.uuid), b=find(d); if(a!==b) par[a]=b;
    }}));
  const groups={};
  Object.keys(par).forEach(id=>{ const r=find(id); (groups[r]=groups[r]||[]).push(id); });
  const bands=[], loose=[];
  Object.values(groups).forEach(g=>{ (g.length>1?bands:loose).push(g.length>1?g:g[0]); });
  bands.sort((a,b)=>b.length-a.length);

  const level={};
  function lvl(u,seen){
    if(level[u]!=null) return level[u];
    if(seen.has(u)) return 0;
    seen.add(u);
    const t=live[u];
    const ds=(t&&t.deps||[]).filter(d=>live[d]||S.missing[d]);
    if(!ds.length){ level[u]=0; return 0; }
    const m=Math.max(...ds.map(d=>live[d]?lvl(d,seen):0));
    level[u]=m+1; return m+1;
  }
  S.tasks.forEach(t=>lvl(t.uuid,new Set()));

  const nodeW=200, nodeMidY=40, colW=330, rowH=104, padX=14, padY=14, bandGap=30;
  const pos={}; let y=padY, maxX=padX+nodeW;
  bands.forEach(g=>{
    const cols={};
    g.forEach(id=>{ const L=live[id]?(level[id]||0):0; (cols[L]=cols[L]||[]).push(id); });
    const bandH=Math.max(...Object.values(cols).map(c=>c.length))*rowH;
    Object.keys(cols).forEach(L=>{
      const off=(bandH-cols[L].length*rowH)/2;
      cols[L].forEach((id,i)=>{
        pos[id]={x:padX+Number(L)*colW, y:Math.round(y+off+i*rowH)};
        maxX=Math.max(maxX, padX+Number(L)*colW+nodeW);
      });
    });
    y+=bandH+bandGap;
  });
  loose.forEach(id=>{
    if(!live[id] && !S.missing[id]) return;
    pos[id]={x:padX, y:y}; y+=rowH;
  });
  const W=Math.max(maxX+padX, 560), H=Math.max(y+padY, 120);

  let conns='';
  S.tasks.forEach(t=>{
    (t.deps||[]).forEach(d=>{
      if(!pos[d]||!pos[t.uuid]) return;
      const x1=pos[d].x+nodeW, y1=pos[d].y+nodeMidY;
      const x2=pos[t.uuid].x-3, y2=pos[t.uuid].y+nodeMidY;
      const mx=(x1+x2)/2;
      const sameComp=(inComp[d]!==undefined && inComp[d]===inComp[t.uuid]);
      const cls=sameComp?'cyc':(S.missing[d]?'miss':'');
      const col=(sameComp||S.missing[d])?'#ef4444':'#3b82f6';
      const arr=(sameComp||S.missing[d])?'arr_r':'arr_b';
      conns+='<path class="conn '+cls+'" stroke="'+col+'" marker-end="url(#'+arr
        +')" d="M'+x1+','+y1+' C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2+'"/>';
    });
  });
  let nodes='';
  S.tasks.forEach(t=>{ const p=pos[t.uuid]; if(!p) return;
    nodes+='<div style="left:'+p.x+'px;top:'+p.y+'px;position:absolute;width:'
      +nodeW+'px">'+cardHtml(t,cyc.has(t.uuid),true)+'</div>'; });
  Object.keys(S.missing).forEach(d=>{ const p=pos[d]; if(!p) return;
    nodes+='<div style="left:'+p.x+'px;top:'+p.y+'px;position:absolute;width:'
      +nodeW+'px">'+stubHtml(d)+'</div>'; });
  inner.style.width=W+'px'; inner.style.height=H+'px';
  inner.innerHTML='<svg width="'+W+'" height="'+H
    +'" style="position:absolute;left:0;top:0;pointer-events:none">'
    +'<defs>'
    +'<marker id="arr_b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"'
    +' markerHeight="7" orient="auto-start-reverse">'
    +'<path d="M0,0 L10,5 L0,10 z" fill="#3b82f6"/></marker>'
    +'<marker id="arr_r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"'
    +' markerHeight="7" orient="auto-start-reverse">'
    +'<path d="M0,0 L10,5 L0,10 z" fill="#ef4444"/></marker>'
    +'</defs>'
    +conns+'</svg>'+nodes;
}

function renderTimeline(){
  const el=document.getElementById('timeline');
  const evs=S.events||[];
  if(!evs.length){ el.innerHTML='<div class="empty">'
    +esc(lb('timeline_empty','Nothing has happened in this project yet.'))+'</div>'; return; }
  const ICO={task_created:'➕',task_updated:'✏️',task_deleted:'🗑'};
  let html='', day='';
  evs.forEach(e=>{
    const d=String(e.ts||'').slice(0,10);
    if(d!==day){ day=d; html+='<div class="day">'+esc(d)+'</div>'; }
    const who=e.editor?esc(e.editor):esc(lb('me','me'));
    const verb=lb('ev_'+String(e.action||''),String(e.action||''));
    const detail=e.detail?' <i>('+esc(e.detail)+')</i>':'';
    html+='<div class="ev"><span class="tm">'+esc(String(e.ts||'').slice(11,16))
      +'</span><span>'+(ICO[e.action]||'·')+'</span>'
      +'<span class="txt"><b>«'+esc(e.title||'')+'»</b> — '+esc(verb)+detail
      +' · '+who+'</span>'
      +(e.host?'<span class="hostchip" title="'+esc(lb('host_tip',''))+'">🖥 '
        +esc(e.host)+'</span>':'')
      +'</div>';
  });
  el.innerHTML=html;
}
</script>
</body></html>"""


class ProjectsBoardWidget(QWidget):

    edits = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        try:
            from PyQt6.QtGui import QColor as _QColor
            self.view.page().setBackgroundColor(_QColor("#0b1220"))
        except Exception:
            pass
        self.view.setStyleSheet("background:#0b1220;")
        self.view.setHtml(_HTML)
        layout.addWidget(self.view)
        self._ready = False
        self._pending_state = None
        self._labels_json = None
        self._pending_view = None
        self.view.loadFinished.connect(self._on_loaded)
        self._drain = QTimer(self)
        self._drain.setInterval(600)
        self._drain.timeout.connect(self._poll_edits)
        self._drain.start()
        self._polling = False

    def _on_loaded(self, ok):
        self._ready = True
        if self._labels_json is not None:
            self.view.page().runJavaScript(
                "setLabels(" + json.dumps(self._labels_json) + ");")
        if self._pending_view is not None:
            self.show_view(self._pending_view)
            self._pending_view = None
        if self._pending_state is not None:
            self.set_state(self._pending_state)
            self._pending_state = None

    def set_labels(self, labels):
        try:
            self._labels_json = json.dumps(labels, ensure_ascii=False)
        except Exception:
            return
        if self._ready:
            self.view.page().runJavaScript(
                "setLabels(" + json.dumps(self._labels_json) + ");")

    def set_state(self, payload):
        if not self._ready:
            self._pending_state = payload
            return
        try:
            js = json.dumps(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            return
        self.view.page().runJavaScript("setState(" + js + ");")

    def show_view(self, name):
        if not self._ready:
            self._pending_view = name
            return
        self.view.page().runJavaScript(
            "showView(" + json.dumps(str(name)) + ");")

    def _poll_edits(self):
        if not self._ready or self._polling or not self.isVisible():
            return
        self._polling = True
        self.view.page().runJavaScript("drainEdits();", self._on_edits)

    def _on_edits(self, raw):
        self._polling = False
        if not raw or raw == "[]":
            return
        try:
            batch = json.loads(raw)
        except Exception:
            return
        if isinstance(batch, list) and batch:
            self.edits.emit(batch)
