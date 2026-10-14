"""Software ka screen (browser me khulta hai, sirf is computer par). Alag file taaki design badalna aasaan rahe.

__TOKEN__ ki jagah har baar ek naya random token lagta hai: sirf ye page software se baat kar sakta hai,
koi doosri website nahi.
"""

PAGE = r"""<!doctype html>
<html lang="hi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sthamly Desk</title>
<style>
:root{--bg:#070b14;--panel:#0e1524;--raised:#141d31;--line:#1f2b47;--ink:#e8edf7;--muted:#8d9ab3;--brand:#ff8a3d;--ok:#46d19a;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:14px 22px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0}h1 span{color:var(--brand)}.muted{color:var(--muted)}
main{display:grid;grid-template-columns:270px 1fr;gap:16px;padding:16px 22px;height:calc(100vh - 110px)}
@media(max-width:800px){main{grid-template-columns:1fr;height:auto}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;overflow:auto}
button{background:var(--raised);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 12px;cursor:pointer;font:inherit}
button:hover{border-color:var(--brand)}button.primary{background:var(--brand);color:#1a0f05;border-color:var(--brand);font-weight:600}
button:disabled{opacity:.5;cursor:default}input[type=text],textarea{width:100%;background:var(--raised);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:10px;font:inherit}
.doc{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)}.doc small{color:var(--muted)}
.chat{display:flex;flex-direction:column;height:100%}.log{flex:1;overflow:auto;display:flex;flex-direction:column;gap:10px;padding-bottom:10px}
.msg{padding:10px 12px;border-radius:10px;white-space:pre-wrap;max-width:92%}.me{background:#1b2a4a;align-self:flex-end}.ai{background:var(--raised);align-self:flex-start}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.row{display:flex;gap:8px}.row textarea{height:46px;resize:none}
.err{color:var(--bad);margin-top:8px}.note{font-size:12px;color:var(--muted);margin-top:10px}.lock{max-width:460px;margin:60px auto}
</style></head><body>
<header><h1>Sthamly <span>Desk</span></h1><div class="muted" id="who"></div></header>
<div id="lock" class="lock panel" hidden>
  <h2 style="margin-top:0">Software chalu karo</h2>
  <p class="muted">Jo activation key aapko mili hai wo yahan daalo. Ye ek baar karna hai.</p>
  <input type="text" id="key" placeholder="STH-XXXX-XXXX-XXXX-XXXX" autocomplete="off">
  <p><button class="primary" id="go">Chalu karo</button></p><div class="err" id="lockerr"></div>
</div>
<main id="app" hidden>
  <aside class="panel">
    <button class="primary" id="up" style="width:100%">PDF jodo</button>
    <input type="file" id="file" accept="application/pdf,.pdf" hidden multiple>
    <div id="docs" style="margin-top:10px"></div>
    <div class="note">PDF sirf is computer par rehti hai. Software band karte hi hat jaati hai. Scanned (photo wali) PDF abhi nahi padhi jaati.</div>
    <div class="err" id="uperr"></div>
  </aside>
  <section class="panel chat">
    <div class="actions" id="actions"></div>
    <div class="log" id="log"><div class="muted">PDF jodo, phir sawal poochho ya upar ka button dabao.</div></div>
    <div class="row"><textarea id="q" placeholder="Apna sawal likho..."></textarea><button class="primary" id="send">Bhejo</button></div>
    <div class="note" id="disc"></div>
  </section>
</main>
<script>
const TOKEN="__TOKEN__";const $=s=>document.querySelector(s);let state=null,history=[],busy=false;
async function api(path,body){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Desk-Token':TOKEN,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.message||'Kuch gadbad hui');return j;}
async function load(){state=await api('/api/state');render();}
function render(){
  const lic=state.license;$('#lock').hidden=!!lic;$('#app').hidden=!lic;$('#who').textContent=lic?lic.label:'';
  if(!lic)return;
  $('#disc').textContent=lic.disclaimer||'';
  const box=$('#docs');box.textContent='';
  state.docs.forEach(d=>{const row=document.createElement('div');row.className='doc';
    const t=document.createElement('div');t.textContent=d.name;const s=document.createElement('small');s.textContent=' '+d.pages+' page';t.appendChild(s);
    const x=document.createElement('button');x.textContent='×';x.onclick=async()=>{await api('/api/delete',{id:d.id});load();};
    row.append(t,x);box.appendChild(row);});
  const a=$('#actions');a.textContent='';
  (lic.actions||[]).forEach(label=>{const b=document.createElement('button');b.textContent=label;
    b.onclick=()=>label.toLowerCase().includes('upload')?$('#file').click():handleActionClick(label);a.appendChild(b);});
}

function handleActionClick(actionLabel){
  // Captures the custom query context from textarea instead of wiping it blank
  const currentMsg=$('#q').value.trim();
  $('#q').value='';
  ask(currentMsg,actionLabel);
}

function bubble(cls,text){const m=document.createElement('div');m.className='msg '+cls;m.textContent=text;const log=$('#log');
  if(log.firstElementChild&&log.firstElementChild.classList.contains('muted'))log.textContent='';log.appendChild(m);log.scrollTop=log.scrollHeight;return m;}
async function ask(message,action){
  if(busy)return;busy=true;$('#send').disabled=true;
  bubble('me',action?('['+action+'] '+message).trim():message);const out=bubble('ai','soch raha hu...');let text='';
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'X-Desk-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify({message,action,history})});
    if(!r.ok){const j=await r.json().catch(()=>({}));throw new Error(j.message||'Kuch gadbad hui');}
    const reader=r.body.getReader(),dec=new TextDecoder();
    for(;;){const {done,value}=await reader.read();if(done)break;text+=dec.decode(value,{stream:true});out.textContent=text;$('#log').scrollTop=$('#log').scrollHeight;}
    history.push({role:'user',content:message||action},{role:'assistant',content:text});history=history.slice(-6);
  }catch(e){out.textContent=e.message;out.style.color='var(--bad)';}
  busy=false;$('#send').disabled=false;
}
$('#send').onclick=()=>{const v=$('#q').value.trim();if(v){$('#q').value='';ask(v,'');}};
$('#q').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#send').click();}};
$('#up').onclick=()=>$('#file').click();
$('#file').onchange=async e=>{$('#uperr').textContent='';
  for(const f of e.target.files){
    // SAFE FILE READER PATCH: Find the first comma index instead of split array matrix slicing
    const data=await new Promise(res=>{const r=new FileReader();r.onload=()=> {
      const s = String(r.result);
      const idx = s.indexOf(',');
      res(idx !== -1 ? s.substring(idx + 1) : '');
    };r.readAsDataURL(f);});
    try{await api('/api/upload',{name:f.name,data});}catch(err){$('#uperr').textContent=f.name+': '+err.message;}}
  e.target.value='';load();};
$('#go').onclick=async()=>{$('#lockerr').textContent='';
  try{await api('/api/activate',{key:$('#key').value});load();}catch(e){$('#lockerr').textContent=e.message;}};
load().catch(e=>{document.body.textContent=e.message;});
</script></body></html>
"""
