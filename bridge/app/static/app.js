/* The Bridge: the page (plain JS, no framework, no build step).
   Look and behaviour: design/bridge/mockup.html (Mission Control · Phosphor r3, approved 2026-11-18).
   Contract: docs/BRIDGE.md §6. Every URL is relative (ingress serves this under /api/hassio_ingress/<token>/),
   nothing is fetched from outside, and every value from the server is set with textContent. */
(function(){
'use strict';
const $=id=>document.getElementById(id);
const root=$('root'),frame=$('frame'),main=$('main');
const NS='http://www.w3.org/2000/svg';
const REDUCE=matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---------- helpers (Crew Hub card style: build with textContent only) ---------- */
function h(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=String(text);return e;}
function ap(p,...k){for(const c of k){if(c==null||c===false)continue;p.appendChild(typeof c==='string'?document.createTextNode(c):c);}return p;}
function key(text,cls,fn){const b=h('button','key'+(cls?' '+cls:''),text);b.type='button';if(fn)b.addEventListener('click',fn);return b;}
function lnk(text,fn){const b=h('button','lnk',text);b.type='button';if(fn)b.addEventListener('click',fn);return b;}
function plate(t,cls){return h('span','plate'+(cls?' '+cls:''),t);}
function pip(cls){const p=h('span','pip'+(cls?' '+cls:''));p.setAttribute('aria-hidden','true');return p;}
function tag(pc,word,cls){return ap(h('span','tag'+(cls?' '+cls:'')),pip(pc),word);}
function grouphd(t){return h('h2','grouphd',t);}
function gap(el,g){el.style.gap=g;return el;}
const arr=x=>Array.isArray(x)?x:[];
const str=x=>x==null?'':String(x);
const PACE=()=>parseFloat(getComputedStyle(root).getPropertyValue('--pace'))||1;
let uidN=0;const uid=p=>(p||'u')+(++uidN);
function uuid(){   /* crypto.randomUUID needs a secure context; HA is often plain http on the LAN */
  if(window.crypto&&crypto.randomUUID&&window.isSecureContext)return crypto.randomUUID();
  const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
  const x=[...b].map(v=>v.toString(16).padStart(2,'0')).join('');return x.slice(0,8)+'-'+x.slice(8,12)+'-'+x.slice(12,16)+'-'+x.slice(16,20)+'-'+x.slice(20);}
const ICON={now:'M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z',
 crew:'M4 7a3 3 0 1 0 6 0a3 3 0 1 0-6 0M2 17c0-3 2.4-5 5-5s5 2 5 5M11.6 8a2.4 2.4 0 1 0 4.8 0a2.4 2.4 0 1 0-4.8 0M12.5 17c0-2 1-3.6 2.8-3.6s2.7 1.6 2.7 3.6',
 chat:'M3 4h14v10H8l-4 3v-3H3z', tickets:'M4 3h12v14H4zM7 7h6M7 10h6M7 13h4',
 tasks:'M3 5h2M3 10h2M3 15h2M8 5h9M8 10h9M8 15h9', library:'M3 3h4v14H3zM8.5 3h4v14h-4zM14 4.5l2.6-.9 2.4 12.8-2.6.9z',
 inbox:'M3 4h14v12H3zM3 11h4l1 2h4l1-2h4', more:'M4 6h12M4 10h12M4 14h12',settings:'M4 6h4M12 6h4M10 4v4M4 14h8M14 12v4'};
function icon(n){const s=document.createElementNS(NS,'svg');s.setAttribute('class','ic');s.setAttribute('viewBox','0 0 20 20');s.setAttribute('aria-hidden','true');const p=document.createElementNS(NS,'path');p.setAttribute('d',ICON[n]);s.appendChild(p);return s;}

/* ---------- time (the browser's own clock and zone; quiet hours are UK time) ---------- */
const MIN=60000;
const now=()=>Date.now();
const p2=n=>String(n).padStart(2,'0');
const tm=v=>{if(v==null||v==='')return null;if(typeof v==='number')return v;const t=Date.parse(v);return isNaN(t)?null:t;};
const hm=ms=>{if(ms==null)return'—';const d=new Date(ms);return p2(d.getHours())+':'+p2(d.getMinutes());};
function ago(ms){if(ms==null)return'—';const s=Math.max(0,Math.round((now()-ms)/1000));if(s<60)return s+' s ago';const m=Math.round(s/60);if(m<60)return m+' min ago';const H=Math.floor(m/60);if(H<24)return H+' h ago';return Math.floor(H/24)+' d ago';}
function span(ms){const m=Math.max(0,Math.round(ms/60000));return m<60?m+' min':Math.floor(m/60)+' h '+p2(m%60);}
function dur(ms){const s=ms/1000;return s<10?s.toFixed(1)+' s':s<60?Math.round(s)+' s':Math.floor(s/60)+' min '+Math.round(s%60)+' s';}
const dayKey=ms=>{const d=new Date(ms);return d.getFullYear()+'-'+p2(d.getMonth()+1)+'-'+p2(d.getDate());};
const dmon=ms=>new Date(ms).toLocaleDateString('en-GB',{day:'numeric',month:'short'});
function dayName(ms){const d=Math.round((Date.parse(dayKey(now()))-Date.parse(dayKey(ms)))/864e5);if(d<=0)return'Today';if(d===1)return'Yesterday';if(d<7)return'Last week';return dmon(ms);}
function ukHour(){try{return +new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/London',hour:'numeric',hourCycle:'h23'}).format(new Date());}catch(e){return new Date().getHours();}}
const quietHours=()=>{const L=D.library;if(L&&typeof L.quiet==='boolean')return L.quiet;const Q=(L&&L.quietHours)||{},hr=x=>{const m=/^(\d{1,2})/.exec(str(x));return m?+m[1]:null;};const f=hr(Q.from)??17,t=hr(Q.to)??1,H=ukHour();return f>t?(H>=f||H<t):(H>=f&&H<t);};

/* ---------- server API (docs/BRIDGE.md §6; relative URLs only) ---------- */
const enc=encodeURIComponent;
const API={state:'api/state',overview:'api/overview',crew:'api/crew',threads:'api/threads',thread:id=>'api/threads/'+enc(id),
  messages:(id,before)=>'api/threads/'+enc(id)+'/messages?limit=50'+(before!=null?'&before='+enc(before):''),send:id=>'api/threads/'+enc(id)+'/messages',
  retry:(id,mid)=>'api/threads/'+enc(id)+'/messages/'+enc(mid)+'/retry',read:'api/read',tickets:'api/tickets',ticket:id=>'api/tickets/'+enc(id),
  tasks:'api/tasks',runs:wf=>'api/tasks/runs?workflowId='+enc(wf),run:(aid,rid)=>'api/agents/'+enc(aid)+'/runs/'+enc(rid),output:aid=>'api/agents/'+enc(aid)+'/output',rerun:'api/agents/rerun',
  conv:(mk,id,o)=>'api/sessions/'+enc(mk)+'/'+enc(id)+'/conversation'+(o&&o.after?'?after='+enc(o.after):o&&o.fresh?'?fresh=1':''),convClose:(mk,id)=>'api/sessions/'+enc(mk)+'/'+enc(id)+'/conversation/close',
  library:'api/library',precheck:'api/library/precheck',apply:'api/library/apply',inbox:b=>'api/inbox'+(b!=null?'?before='+enc(b):''),
  command:'api/claude/command',dismiss:'api/claude/dismiss',settings:'api/settings',events:'api/events',
  ocAgents:'api/opencode/agents',ocSessions:id=>'api/opencode/agents/'+enc(id)+'/sessions',ocSession:(s,f)=>'api/opencode/sessions/'+enc(s)+(f?'?fresh=1':''),ocSend:id=>'api/opencode/agents/'+enc(id)+'/messages'};
class ApiError extends Error{constructor(msg,status,code,data){super(msg);this.status=status;this.code=code;this.data=data||{};}}
async function req(method,url,body){
  const o={method,credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}};
  if(method!=='GET'){o.headers['Content-Type']='application/json';o.headers['X-Bridge']='1';o.body=JSON.stringify(Object.assign({clientId:uuid()},body||{}));}
  let r;try{r=await fetch(url,o);}catch(e){throw new ApiError('The Bridge isn\'t answering',0,'network');}
  let j=null;try{j=await r.json();}catch(e){}
  if(!r.ok){const m=j&&j.error?str(j.error):r.status===403?'Open The Bridge from Home Assistant to make changes':'Error '+r.status;throw new ApiError(m,r.status,j&&j.code,j);}
  return j;}
const get=u=>req('GET',u),post=(u,b)=>req('POST',u,b),patch=(u,b)=>req('PATCH',u,b);

/* ---------- data: one cache per endpoint; views ask with use(), which fetches once and re-renders ---------- */
const D={state:null,overview:null,crew:null,threads:null,msgs:{},more:{},tickets:null,tasks:null,runs:{},run:{},output:{},library:null,inbox:null,inboxMore:false,jobs:{},oca:null,ocs:{},oct:{}};
const ERR={},LOADING={};
const URLS={state:API.state,overview:API.overview,crew:API.crew,threads:API.threads,tickets:API.tickets,tasks:API.tasks,library:API.library,inbox:API.inbox(),oca:API.ocAgents};
const PICK={oca:j=>arr(j&&j.agents),threads:j=>arr(j&&(j.threads||j)),tickets:j=>arr(j&&(j.tickets||j)),inbox:j=>{D.inboxMore=!!(j&&j.more);return arr(j&&(j.items||j.inbox||j));},
  library:j=>j&&j.report!==undefined?Object.assign({findings:[],notes:[],errors:[],none:!j.report},j.report||{},{safeIds:j.safeIds,applyJob:j.applyJob,agent:j.agent,quietHours:j.quietHours}):j};
function load(k,force){
  if(LOADING[k]||(!force&&(D[k]!=null||ERR[k])))return;LOADING[k]=true;
  get(URLS[k]).then(j=>{D[k]=PICK[k]?PICK[k](j):j;delete ERR[k];onLoaded(k);},e=>{ERR[k]=e;}).finally(()=>{LOADING[k]=false;schedule();});}
function use(k){load(k);return D[k];}
function useSub(map,id,url,pick){if(map[id]!==undefined||LOADING[url]||ERR[url])return map[id];LOADING[url]=true;
  get(url).then(j=>{map[id]=pick?pick(j):j;delete ERR[url];},e=>{ERR[url]=e;}).finally(()=>{LOADING[url]=false;schedule();});return undefined;}
function retryAll(){Object.keys(ERR).forEach(k=>delete ERR[k]);render();}
function onLoaded(k){
  if(k==='state'){applySettings();}
  const aj=k==='library'?D.library&&D.library.applyJob:k==='state'?D.state&&D.state.applyJob:null;if(aj&&aj.id)D.jobs[aj.id]=Object.assign({},D.jobs[aj.id],aj);
}
/* refetch whatever the page holds (after an SSE hello: there is no replay) */
function refetchAll(){D.ocs={};D.oct={};['state','crew','overview','threads','tickets','tasks','library','inbox','oca'].forEach(k=>{if(D[k]!=null||ERR[k]){delete ERR[k];load(k,true);}});
  D.runs={};D.run={};D.output={};Object.keys(CONV).forEach(k=>delete CONV[k]);const t=S.chat&&S.chat!=='list'&&S.chat!=='new'?S.chat:null;D.msgs={};D.more={};if(t)loadMsgs(t);}

/* ---------- normalised views of the data ---------- */
const SYS_NAMES=['The Bridge: API','Phone Reply Agent','Engineer: notify','Engineer: dispatch'];
const isSys=a=>a.group==='system'||/^Agent Hub: /.test(a.name||'')||SYS_NAMES.includes(a.name);
const short=n=>str(n).replace(/\s*\([^)]*\)\s*$/,'').replace(/ voice assistant$/,'');
function n8nOf(){const c=D.crew||{};return c.n8n||c.status||{};}
let SANDBOX_N=0;   // SANDBOX:* copies never reach AGENTS (counts, Now, hero); only the System fold mentions them
const isSandbox=a=>/^SANDBOX:/i.test(str(a.name));
const isOC=a=>!!a&&(/^opencode/.test(str(a.kind))||/^oc:/.test(str(a.id)));
/* OpenCode agents: the status row (api/crew) plus the live fields from api/opencode/agents */
function ocMerge(a){if(!isOC(a))return a;const o=arr(D.oca).find(x=>x.id===a.id);if(!o)return a;
  return Object.assign({},a,{short_name:o.short_name,description:o.description||a.description,busy:o.busy,busySince:o.busySince,needsApproval:o.needsApproval,lastSession:o.lastSession,role:a.role||o.role});}
function agentsOf(){const c=D.crew;if(!c)return[];const g=c.agents;let out=[];
  if(Array.isArray(g))out=g.slice();else if(g&&typeof g==='object')['agents','services','system'].forEach(k=>arr(g[k]).forEach(a=>out.push(Object.assign({group:k},a))));else out=arr(n8nOf().agents);
  const v=out.filter(a=>!isSandbox(a));SANDBOX_N=(+c.sandboxHidden||0)+out.length-v.length;return v.map(ocMerge);}
function machinesOf(){const c=D.crew;if(!c||!c.machines)return[];return Object.keys(c.machines).map(k=>Object.assign({key:k},c.machines[k]||{})).filter(m=>m.machine);}
let AGENTS=[],MACH=[],SESS=[];
function derive(){AGENTS=agentsOf();if(AGENTS.some(isOC))load('oca');MACH=machinesOf();SESS=[];MACH.forEach(m=>arr(m.sessions).forEach(s=>{const c=Object.assign({},s);c.mk=m.key;c.m=m;SESS.push(c);}));}
const ANS={};   // answered Claude prompts, by requestId (until the snapshot moves on)
const ask=s=>s.status==='waiting'&&s.waiting?s.waiting:null;
const openAsk=s=>{const w=ask(s);return w&&!ANS[w.requestId]?w:null;};
const canAnswer=s=>{const w=ask(s);return !!(w&&w.hubCanAnswer&&w.requestId&&arr(s.controls).includes('answer')&&s.m.machine.commands&&s.m.machine.commands.enabled);};
const alertAgents=()=>AGENTS.filter(a=>a.state==='error'||a.state==='down');
/* an offline machine stays quiet unless it missed scheduled runs (server: machine.missed {count, since} or crew.missed[]) */
function machAlerts(){const c=D.crew||{},out=[];MACH.forEach(M=>{if(M.status==='online')return;let x=M.missed||(M.machine&&M.machine.missed);if(!x)x=arr(c.missed).find(y=>y.machine===M.key);const n=x?(typeof x==='number'?x:+(x.count||x.n||0)):0;if(n>0)out.push({M,n,since:tm(x.since)});});return out;}
const alertN=()=>alertAgents().length+machAlerts().length;
const needs=()=>SESS.filter(openAsk).sort((a,b)=>tm(a.waiting.since)-tm(b.waiting.since));
function litAsk(){if(alertN())return null;const n=needs().filter(canAnswer);return n.length&&n[0].waiting.kind==='permission'?n[0].waiting.requestId:null;}
const working=()=>SESS.filter(s=>s.status==='working'||(s.status==='waiting'&&ANS[(s.waiting||{}).requestId]));
function aState(a){
  if(isOC(a)){if(a.state==='down')return{c:'offline',w:'Not answering'};if(a.needsApproval)return{c:'caut',w:'Needs you'};
    if(a.busy||a.state==='running')return{c:'busy',w:'Working'};if(a.state==='off'||a.active===false)return{c:'off',w:'Off'};return{c:'run',w:'Idle'};}
  if(a.state==='error'&&!untracked(a))return{c:'warn',w:'Failed'};
  if(a.state==='down')return{c:'offline',w:'Not answering'};
  if(a.state==='off'||a.active===false)return{c:'off',w:'Off'};
  if(a.task)return{c:'busy',w:'Running'};
  if(a.state&&!['ok','idle','running'].includes(a.state)&&!(a.state==='error'&&untracked(a)))return{c:'run',w:str(a.state)};   // unknown states show as plain labels
  return{c:'run',w:a.kind==='service'?'Online':'On'};
}
const pipOf=c=>c==='off'?'':c;
const isDesk=()=>frame.clientWidth>=960;
function modelName(m){const x=/claude-([a-z]+)-(\d+)(?:-(\d+))?/.exec(m||'');return x?x[1][0].toUpperCase()+x[1].slice(1)+' '+x[2]+(x[3]?'.'+x[3]:''):(m||'');}
function tok(n){n=+n||0;return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1000?Math.round(n/1000)+'k':String(n);}
/* ticket status -> group (open | needs | progress | done); the Engineer (OpenCode, on the ticket inbox) sets needs_isaac
   when it wants direction and awaiting_approval when a sandboxed fix is ready for review */
function tStatus(t){const s=str(t.status).toLowerCase();return s==='closed'||s==='done'||s==='resolved'?'done':s==='needs_isaac'||s==='awaiting_approval'?'needs':s==='in_progress'||s==='in progress'||s==='progress'?'progress':'open';}
const tWord=t=>{const s=str(t.status).toLowerCase();return s==='needs_isaac'?'Needs you':s==='awaiting_approval'?'Ready to review':{done:'Done',progress:'In progress',open:'Open'}[tStatus(t)];};
const tPip=st=>st==='done'?'done':st==='progress'?'busy':st==='needs'?'caut':'';
const AGENT_AUTHORS=['NAS agent','Engineer'];   // any other author is the human (HA user name)
const isAgentC=c=>AGENT_AUTHORS.includes(c.author)||c.role==='agent'||c.agent===true;
/* tickets handed back to you, oldest first: from the tickets list when loaded, else Now's overview */
function tNeeds(){if(D.tickets)return D.tickets.filter(t=>tStatus(t)==='needs').sort((a,b)=>tm(a.updatedAt)-tm(b.updatedAt));
  const o=D.overview&&D.overview.needsYou;return o&&Array.isArray(o.ticketsNeedYou)?o.ticketsNeedYou:null;}
function tNeedN(){const l=tNeeds();return l?l.length:+(((D.state&&D.state.unread)||{}).ticketsNeedYou)||0;}
const lastAgentC=t=>t.last!==undefined?t.last:comments(t).slice().reverse().find(isAgentC)||null;
const TYPE={bug:'Bug',feature:'Feature',tool_feedback:'Tool feedback','tool feedback':'Tool feedback',question:'Question',test:'Test'};
const agentOfTicket=t=>{const id=t.agentId||t.agent_id||t.agentMatch||t.agent_match;if(id){const x=AGENTS.find(a=>a.id===id);if(x)return x;}if(!t.agent)return null;return AGENTS.find(a=>a.name===t.agent)||null;};
function comments(t){const c=t.comments;if(!c)return[];if(Array.isArray(c))return c;try{const x=JSON.parse(c);return Array.isArray(x)?x:[];}catch(e){return[];}}
const libAgent=()=>AGENTS.find(a=>a.id==='svc:media-agent')||AGENTS.find(a=>a.role==='library');
const libTotal=()=>{const L=D.library||{};if(L.total!=null)return L.total;const a=libAgent(),m=a&&/(\d+) findings/.exec(a.last_status||'');return m?+m[1]:arr(L.findings).length;};
const FINDS=()=>arr(D.library&&D.library.findings);
const NO_RUNS=['Agent Hub: Status','The Bridge: API'];   // they keep no successful runs (n8n side, 2026-11-18)
const user=()=>(D.state&&D.state.user)||{};
const untracked=a=>a.tracked===false;   // n8n keeps no successful runs for it: no last run to show, never a failure
const hasRuns=a=>!!a.workflowId&&a.hasRuns!==false&&!untracked(a)&&!isSys(a)&&!NO_RUNS.includes(a.name);
const health=()=>(D.state&&D.state.health)||{};

/* ---------- state of the page ---------- */
const S={view:'now',crew:'claude',agent:null,run:null,chat:null,ticket:null,newT:false,tf:'open',inbox:null,theme:'ha',
  open:{},draft:{type:'bug',agent:'',title:'',details:''},bad:false,drafts:{},page:{},sse:'connecting',sseDownAt:null,stick:false,sess:null};

/* ---------- scheme: Settings Follow HA / Night / Day, per HA user on the server ---------- */
const TH_IN={follow_ha:'ha',auto:'ha',ha:'ha',dark:'night',night:'night',light:'day',day:'day'},TH_OUT={ha:'follow_ha',night:'night',day:'day'};
function applySettings(){const st=D.state&&D.state.settings;const t=st&&(st.theme||st.mode);if(t&&TH_IN[t])S.theme=TH_IN[t];}
/* Follow HA: the ingress page runs in an iframe on HA's own origin, so the parent's <home-assistant> element is readable;
   hass.themes.darkMode is what HA applies. Opened on its own (or blocked), it follows the device instead. */
function haDark(){try{if(window.parent&&window.parent!==window){const el=window.parent.document.querySelector('home-assistant');const t=el&&el.hass&&el.hass.themes;if(t&&typeof t.darkMode==='boolean')return t.darkMode;}}catch(e){}return null;}
let haSource='device';
function scheme(){if(S.theme==='night'||S.theme==='day')return S.theme;const d=haDark();haSource=d==null?'device':'ha';if(d!=null)return d?'night':'day';return matchMedia('(prefers-color-scheme: light)').matches?'day':'night';}
function paintScheme(){const s=scheme();if(root.dataset.scheme!==s){root.dataset.scheme=s;document.documentElement.style.background=s==='day'?'#e9e3d5':'#0b0e0d';return true;}return false;}
matchMedia('(prefers-color-scheme: light)').addEventListener('change',()=>{if(paintScheme())render();});
setInterval(()=>{if(S.theme==='ha'&&paintScheme()&&S.view==='settings')render();},2000);

/* ---------- toast: the readout line (one at a time) ---------- */
let toastT=null;
function toast(msg,undo){const z=$('toastzone');z.textContent='';const t=h('div','toast in');t.setAttribute('role','status');t.append(h('span',null,msg));
  if(undo){const k=h('button','k','Undo');k.type='button';k.addEventListener('click',()=>{undo();z.textContent='';});t.append(k);}
  z.append(t);clearTimeout(toastT);toastT=setTimeout(()=>{z.textContent='';},4000*PACE());}
const failToast=(what,e)=>toast(what+' · '+(e&&e.message||'error'));

/* ---------- guarded dialog with a go/no-go poll: first tap opens + polls, the action lights, second tap acts ----------
   o.checks: lines [{t, v: go|caut|nogo, word?, note?}] or a function returning a Promise of them (o.pending = placeholder texts). */
let dlgOpen=false;
function guarded(o){
  const z=h('div','dlgzone'),sc=h('div','scrim'),d=h('div','dlg guarded'),tid=uid('dt');
  d.setAttribute('role','dialog');d.tabIndex=-1;d.setAttribute('aria-modal','true');d.setAttribute('aria-labelledby',tid);
  const dh=ap(h('div','dh'),plate(o.title),plate('Guarded'));dh.firstChild.id=tid;
  const pend=typeof o.checks==='function'?o.pending:o.checks.map(c=>typeof c==='string'?c:c.t);
  const poll=h('div','poll'),rows=pend.map(t=>{const v=h('span','v','…'),tx=h('span',null,t);poll.append(ap(h('div','ln'),tx,v));return{v,tx};});
  const db=ap(h('div','db'),h('p',null,o.q),poll);
  const bg=['band','tabbar','toastzone'].map($).concat([root.querySelector('.shell')]);bg.forEach(x=>x.inert=true);dlgOpen=true;
  let closed=false;
  const close=()=>{if(closed)return;closed=true;dlgOpen=false;bg.forEach(x=>x.inert=false);z.remove();document.removeEventListener('keydown',esc);if(o.back&&o.back.isConnected)o.back.focus({preventScroll:true});schedule();};
  const esc=e=>{if(e.key==='Escape')close();};
  const act=key(o.act,'',()=>{if(act.disabled)return;close();o.go();});act.disabled=true;
  const df=ap(h('div','df'),key(o.cancel||'Keep waiting','',close),act);
  ap(d,dh,db,df);ap(z,sc,d);sc.addEventListener('click',close);document.addEventListener('keydown',esc);$('dlgroot').append(z);
  const arm=()=>{if(closed)return;act.disabled=false;act.classList.add('lit');};d.focus({preventScroll:true});   // focus the dialog, not the armed key
  const show=CK=>{CK=CK.map(c=>typeof c==='string'?{t:c,v:'go'}:c);
    while(rows.length<CK.length){const v=h('span','v','…'),tx=h('span');poll.append(ap(h('div','ln'),tx,v));rows.push({v,tx});}
    const setV=i=>{const c=CK[i],r=rows[i];r.tx.textContent=c.t;if(c.note)r.tx.append(h('span','pn',c.note));r.v.textContent=c.v==='go'?'GO':c.v==='caut'?'▲ '+(c.word||'CHECK'):'NO-GO';r.v.classList.add(c.v);};
    const ok=CK.every(c=>c.v!=='nogo'),fin=()=>{if(ok)arm();};   // caution lines still allow; a NO-GO keeps the action off
    if(REDUCE){CK.forEach((c,i)=>setV(i));fin();return;}
    CK.forEach((c,i)=>setTimeout(()=>{if(closed)return;setV(i);if(i===CK.length-1)setTimeout(fin,220*PACE());},(i+1)*220*PACE()));};
  if(typeof o.checks==='function')o.checks().then(show,()=>show(pend.map(t=>({t,v:'nogo'}))));else show(o.checks);
}

/* ---------- live data: SSE api/events with reconnect (no replay: hello refetches) ---------- */
let es=null,esTry=0,esTimer=null,seenHello=false;
const BACKOFF=[1,2,5,10,30];
function sseOpen(){
  clearTimeout(esTimer);try{if(es)es.close();}catch(e){}
  es=new EventSource(API.events);
  const on=(n,f)=>es.addEventListener(n,ev=>{let d=null;try{d=JSON.parse(ev.data);}catch(e){return;}try{f(d||{});}catch(e){console.warn('event '+n,e);}});
  on('hello',d=>{esTry=0;const was=S.sse;S.sse='live';S.sseDownAt=null;if(D.state&&d.version&&D.state.version&&d.version!==D.state.version){location.reload();return;}
    if(seenHello||was==='down')refetchAll();seenHello=true;schedule();});
  on('crew',d=>{if(d.connected!=null&&D.state){const hh=health();D.state.health=Object.assign({},hh,{crew:Object.assign({},hh.crew,{connected:d.connected})});}if(!D.crew)return;if(d.machines)D.crew.machines=d.machines;if(d.missed)D.crew.missed=d.missed;sched('crew');});
  on('health',d=>{if(!D.state)return;const hh=Object.assign({},health());if(d.n8n)hh.n8n=Object.assign({},hh.n8n,d.n8n);if(d.crew)hh.crew=Object.assign({},hh.crew,d.crew);D.state.health=hh;sched('all');});
  on('crew.machine',d=>{if(!D.crew||!d.id)return;D.crew.machines=D.crew.machines||{};if(d.data==null)delete D.crew.machines[d.id];else D.crew.machines[d.id]=d.data;sched('crew');});
  on('n8n',d=>{const hh=health();if(D.state){D.state.health=Object.assign({},hh,{n8n:Object.assign({},hh.n8n,{ok:!d.error,at:d.at||(hh.n8n||{}).at,error:d.error||null,code:d.code||null,since:d.since||null})});}
    if(D.crew&&d.status&&typeof d.status==='object'&&d.status.agents&&!d.error){load('crew',true);if(D.overview)load('overview',true);}
    sched('n8n');});
  on('thread',d=>{const t=d.thread||d;if(!t||!t.id)return;if(D.threads){const i=D.threads.findIndex(x=>x.id===t.id);if(t.archived){if(i>=0)D.threads.splice(i,1);}else if(i>=0)D.threads[i]=Object.assign({},D.threads[i],t);else D.threads.unshift(t);}sched('chat');});
  on('message',d=>{const m=d.message||d;if(!m||m.id==null)return;upsertMsg(m);sched('chat');});
  on('tickets',()=>{if(D.tickets||ERR.tickets)load('tickets',true);if(D.state)load('state',true);if(D.overview)load('overview',true);});
  on('inbox',d=>{if(D.inbox||ERR.inbox)load('inbox',true);if(D.state&&D.state.unread&&d.count!=null)D.state.unread.inbox=d.count;sched('inbox');});
  on('library',d=>{if(D.library){const ap_=new Set(arr(d.applied));if(d.date&&D.library.date&&d.date!==D.library.date){load('library',true);return;}FINDS().forEach(f=>{if(ap_.has(f.id)&&!f.applied){f.applied=true;f.at=now();}});}sched('library');});
  on('job',d=>{if(!d.id)return;D.jobs[d.id]=Object.assign({},D.jobs[d.id],d);if(d.kind==='library.apply'||/library/.test(str(d.kind)))jobChanged(d);sched('library');});
  on('opencode',d=>ocChanged(d));
  on('settings',d=>{if(D.state){D.state.settings=Object.assign({},D.state.settings,d);applySettings();sched('all');}});
  es.onerror=()=>{if(es.readyState===EventSource.CLOSED){S.sse='down';if(!S.sseDownAt)S.sseDownAt=now();const w=BACKOFF[Math.min(esTry,BACKOFF.length-1)];esTry++;esTimer=setTimeout(sseOpen,w*1000);schedule();}
    else if(S.sse!=='down'){S.sse='retry';if(!S.sseDownAt)S.sseDownAt=now();}};
}
/* re-render for an event only when it touches what's on screen; the nav counts always */
const VIEW_OF={crew:['now','crew','tasks','more','chat'],n8n:['now','crew','tasks','library'],chat:['chat','now'],inbox:['inbox','more'],library:['library','tasks','more','now'],all:null};
function sched(what){const vs=VIEW_OF[what];if(!vs||vs.includes(S.view))schedule();else{derive();renderNav();}}
let rq=0;function schedule(){if(rq)return;rq=requestAnimationFrame(()=>{rq=0;render();});}

/* ---------- navigation (hash routes: the phone's back key works inside HA's iframe) ---------- */
const TABS=[['now','Now'],['crew','Crew'],['chat','Chat'],['tickets','Tickets'],['more','More']];
const RAIL=[['now','Now'],['crew','Crew'],['chat','Chat'],['tickets','Tickets'],['tasks','Tasks'],['library','Library'],['inbox','Inbox']];
const UNDER_MORE=['tasks','library','inbox','settings'];
function route(){const v=S.view;
  if(v==='crew'&&S.oc)return 'oc/'+enc(S.oc.a)+'/'+enc(S.oc.s);
  if(v==='crew'&&S.sess)return 'session/'+enc(S.sess.mk)+'/'+enc(S.sess.id);
  if(v==='crew'&&S.agent)return 'agent/'+enc(S.agent)+(S.run?'/run/'+enc(S.run):'');
  if(v==='crew')return 'crew'+(S.crew==='agents'?'/agents':'');
  if(v==='chat')return 'chat'+(S.chat&&S.chat!=='list'&&S.chat!=='new'?'/'+enc(S.chat):S.chat==='list'?'/list':'');
  if(v==='tickets')return S.newT?'tickets/new':S.ticket!=null?'ticket/'+enc(S.ticket):'tickets'+(S.tf!=='open'?'/'+S.tf:'');
  return v;}
function parseRoute(){const p=location.hash.replace(/^#\/?/,'').split('/').map(x=>{try{return decodeURIComponent(x);}catch(e){return x;}});
  Object.assign(S,{view:'now',agent:null,run:null,ticket:null,newT:false,inbox:null,sess:null,oc:null});const v=p[0]||'now';
  if(v==='oc'&&p[1]&&p[2]){S.view='crew';S.crew='agents';S.oc={a:p[1],s:p[2]};return;}
  if(v==='session'&&p[1]&&p[2]){S.view='crew';S.crew='claude';S.sess={mk:p[1],id:p[2]};return;}
  if(v==='agent'&&p[1]){S.view='crew';S.crew='agents';S.agent=p[1];S.run=p[2]==='run'&&p[3]?p[3]:null;return;}
  if(v==='crew'){S.view='crew';S.crew=p[1]==='agents'?'agents':'claude';return;}
  if(v==='chat'){S.view='chat';S.chat=p[1]==='list'?'list':p[1]||null;return;}
  if(v==='ticket'&&p[1]){S.view='tickets';S.ticket=isNaN(+p[1])?p[1]:+p[1];return;}
  if(v==='tickets'){S.view='tickets';if(p[1]==='new')S.newT=true;else if(['open','needs','progress','done'].includes(p[1]))S.tf=p[1];return;}
  if(['now','tasks','library','inbox','more','settings'].includes(v))S.view=v;}
function go(view,o){S.view=view;Object.assign(S,{agent:null,run:null,ticket:null,newT:false,inbox:null,sess:null,oc:null},o||{});
  if(view==='chat'&&!(o&&'chat' in o))S.chat=isDesk()?(S.chat&&S.chat!=='list'?S.chat:null):'list';
  if(view==='chat')S.stick=true;
  const r='#'+route();if(location.hash!==r)history.pushState(null,'',r);render(true);window.scrollTo(0,0);}
window.addEventListener('popstate',()=>{parseRoute();render(true);});
function threadUnread(){return arr(D.threads).reduce((a,t)=>a+(+t.unread||0),0);}
function counts(){const un=(D.state&&D.state.unread)||{};const n=needs().length+alertN()+tNeedN()+ocNeeds().length;
  const u=D.threads?threadUnread():(+un.chat||+un.threads||0);
  const o=D.tickets?D.tickets.filter(t=>tStatus(t)!=='done').length:(+un.tickets||0);
  const l=D.library?FINDS().filter(f=>!f.applied).length:(+un.library||0);return{now:n,chat:u,tickets:o,library:l,inbox:+un.inbox||0};}
function navLink(v,label,cur,cls){const a=h('a',cls);a.href='#'+v;ap(a,icon(v),h('span',null,label));if(cur)a.setAttribute('aria-current','page');a.addEventListener('click',e=>{e.preventDefault();go(v);});return a;}
const nowCls=()=>alertN()?'lit':'caut';   // waiting = amber outline; filled red only while something has failed
function countBox(n,cls,label){if(!n)return null;const c=h('span','count'+(cls?' '+cls:''),n);c.setAttribute('aria-label',n+' '+label);return c;}
function renderNav(){
  const c=counts(),tb=$('tabbar'),rl=$('rail');tb.textContent='';rl.textContent='';
  TABS.forEach(([v,l])=>{const cur=S.view===v||(v==='more'&&UNDER_MORE.includes(S.view));const a=navLink(v,l,cur);
    if(v==='now')ap(a,countBox(c.now,nowCls(),'need you'));if(v==='chat')ap(a,countBox(c.chat,false,'unread'));if(v==='tickets')ap(a,countBox(c.tickets,false,'open'));tb.append(a);});
  RAIL.forEach(([v,l])=>{const a=navLink(v,l,S.view===v);
    ap(a,v==='now'?countBox(c.now,nowCls(),'need you'):v==='chat'?countBox(c.chat,false,'unread'):v==='tickets'?countBox(c.tickets,false,'open'):v==='library'?countBox(c.library,false,'waiting'):null);rl.append(a);});
  rl.append(navLink('settings','Settings',S.view==='settings','foot'));
}
function backOf(){
  if(S.view==='crew'&&S.oc){const a=AGENTS.find(x=>x.id===S.oc.a);return[a?a.name:'Agent',()=>go('crew',{agent:S.oc.a,crew:'agents'})];}
  if(S.view==='crew'&&S.sess)return['Crew',()=>go('crew',{crew:'claude'})];
  if(S.view==='crew'&&S.agent&&S.run)return['Agent',()=>go('crew',{agent:S.agent,crew:'agents'})];
  if(S.view==='crew'&&S.agent)return['Crew',()=>go('crew',{crew:'agents'})];
  if(S.view==='tickets'&&(S.ticket!=null||S.newT))return['Tickets',()=>go('tickets')];
  if(S.view==='chat'&&S.chat&&S.chat!=='list')return['Chats',()=>go('chat',{chat:'list'})];
  if(UNDER_MORE.includes(S.view))return['More',()=>go('more')];
  return null;}
function n8nLamp(){const n=health().n8n||{};if(!D.state)return null;if(n.ok)return['run','n8n · online'];if(isKeyErr(n))return['warn','n8n · key'];return['caut','n8n · no answer'];}
const isKeyErr=n=>n&&!n.ok&&(n.code==='key'||n.code==='unauthorized'||n.status===401||n.status===403||/\bkey\b/i.test(str(n.error)));
function renderBand(){
  const b=$('band');b.textContent='';const bk=backOf();
  if(bk&&!isDesk()){const a=h('a','bk',bk[0]);a.href='#';a.addEventListener('click',e=>{e.preventDefault();bk[1]();});b.append(a);}
  else b.append(h('span','name','The Bridge'));
  b.append(h('span','sp'));
  if(S.sse==='down'&&S.sseDownAt&&now()-S.sseDownAt>3000)b.append(h('span','num dim','Reconnecting…'));
  if(isDesk()){const L=n8nLamp();if(L){const l=h('span','ann '+L[0]);l.append(h('i',null,L[1]));b.append(l);}
    const at=tm((health().n8n||{}).at);if(at){const u=h('span','num dim','Updated '+ago(at));u.id='updated';b.append(u);}}
  const ck=h('span','clock',hm(now()));ck.id='clock';b.append(ck);
}
function crumb(parent,fn,here){const c=h('nav','crumb desk-only');c.setAttribute('aria-label','Breadcrumb');const a=h('a',null,parent);a.href='#';a.addEventListener('click',e=>{e.preventDefault();fn();});const sep=h('span','sep','/');sep.setAttribute('aria-hidden','true');ap(c,a,sep,h('span',null,here));c.lastChild.style.color='var(--tx)';return c;}

/* ---------- setup banners, loading and error states (kit: .banner, .skel rows, .errst) ---------- */
function banner(cls,sym,title,text,keys){const b=h('div','banner '+cls);if(cls==='warn')b.setAttribute('role','alert');ap(b,h('span','sym',sym),ap(h('div'),h('b',null,title),text?h('p',null,text):null),keys||h('span'));return b;}
function setupBanners(){
  const out=[];if(!D.state)return out;const hh=health(),n=hh.n8n||{},c=hh.crew||{};
  if(isKeyErr(n))out.push(banner('warn','◆','n8n key missing or wrong','Paste the key in The Bridge\'s add-on options (Configuration › n8n_key), then restart the add-on. Showing the last copy.'));
  else if(n.ok===false){const since=tm(n.since)||tm(n.errorSince)||tm(n.at);out.push(banner('caut','▲','n8n unreachable since '+(since?(dayKey(since)===dayKey(now())?hm(since):dmon(since)+' '+hm(since)):'—'),'Showing the last copy'+(tm(n.at)?' from '+hm(tm(n.at)):'')+'. The Bridge keeps trying.'));}
  if(c.connected===false)out.push(banner('caut','▲','Crew Hub not connected','Claude sessions can\'t be shown or messaged until Home Assistant answers. The Bridge keeps trying.'));
  if(D.state&&user().canWrite===false)out.push(banner('info','○','Read only','Open The Bridge from the Home Assistant sidebar as an admin to send, answer or file.'));
  return out;}
function skel(n){const l=h('div','list');l.setAttribute('aria-label','Loading');l.setAttribute('aria-busy','true');for(let i=0;i<(n||3);i++){const r=h('div','li');ap(r,pip('busy'),h('span','skel'),h('span','skel'),h('span'));r.children[1].style.width=[70,55,62][i%3]+'%';r.children[2].style.width='40px';l.append(r);}return l;}
function errState(what,e,back){const x=h('div','errst');x.setAttribute('role','alert');ap(x,h('span','h num','Couldn\'t load '+what),h('p',null,e&&e.status===0?'The Bridge didn\'t answer. Check that the add-on is running.':'The Bridge answered with an error.'));
  x.append(h('div','well',(e&&e.message)||'error'));const r=h('div','row');r.append(key('Try again','lit',retryAll));if(back)r.append(key(back[0],'',back[1]));x.append(r);return x;}
/* returns the data, or puts a loading / error state in m and returns null */
function need(m,k,what,n){const v=use(k);if(v!=null)return v;if(ERR[k]){m.append(errState(what,ERR[k]));return null;}m.append(skel(n));return null;}

/* ---------- Claude parts (Crew Hub card logic, Phosphor look) ---------- */
const CMD_ERR={not_waiting:'No longer waiting',already_answered:'Already answered',no_ack:'No answer from the machine',unauthorized:'Admins only',
  publish_failed:'Couldn\'t reach the machine',commands_disabled:'Commands are off on that machine',unknown_session:'That session is gone'};
async function command(machine,action,args,confirm){
  const j=await post(API.command,{machine,action,args,confirm:confirm||undefined});
  if(j&&j.ok===false)throw new ApiError(CMD_ERR[j.code||j.error]||str(j.error||'refused'),200,j.code);return j;}
function stateTag(s){const w=openAsk(s);if(w)return tag('caut','Needs you','caut-t');if(s.status==='working'||(s.status==='waiting'&&!w))return tag('busy','Working');if(s.status==='ended')return tag('','Ended');return tag('','Idle');}
function askBlock(s){
  const w=ask(s),box=gap(h('div','stack ask'),'10px'),a=ANS[w.requestId];
  if(a){box.append(h('p','dim',a));return box;}
  const q=h('div','well q');
  if(w.kind==='permission')ap(q,plate('Wants to run · '+(w.tool||'tool')),h('span','say',w.text));
  else{const qq=arr(w.questions)[0]||{};ap(q,plate('Question'+(qq.header?' · '+qq.header:'')),h('span','say',qq.question||w.text));}
  box.append(q);
  if(!canAnswer(s)){box.append(h('p','dim','Answer this on '+s.mk+'.'));return box;}
  const nk='note:'+w.requestId,ks=h('div','keys');
  const answer=(args,past,t)=>{ANS[w.requestId]=past+' '+hm(now());render();
    command(s.mk,'answer',Object.assign({session:s.id,requestId:w.requestId},args)).then(()=>{toast(t);delete S.drafts[nk];},e=>{delete ANS[w.requestId];failToast('Not answered',e);render();});};
  const note=()=>{const v=str(S.drafts[nk]).trim();return v?{message:v.slice(0,500)}:{};};
  if(w.kind==='permission'){
    ks.append(key('Allow',litAsk()===w.requestId?'lit':'',()=>answer(Object.assign({decision:'allow'},note()),'Allowed','Allowed · '+s.name)),key('Deny','',()=>answer(Object.assign({decision:'deny'},note()),'Denied','Denied · '+s.name)));
  }else{
    const qq=arr(w.questions)[0]||{};arr(qq.options).forEach(o=>{const l=typeof o==='string'?o:str(o.label);ks.append(key(l,'',()=>answer({decision:'allow',answers:{[qq.question||w.text]:l}},'Answered "'+l+'"','Answered · '+l)));});
  }
  /* "Add a note": a permission note goes with Deny (Claude reads it as the reason); for a question it is the answer text */
  const nb=h('div','stack');nb.hidden=!S.open[nk];const ta=h('textarea','inp');ta.rows=2;ta.maxLength=500;ta.placeholder='A note for '+s.name;ta.setAttribute('aria-label','Note');bindDraft(ta,nk);
  nb.append(ta,ap(h('div','keys'),key('Send note','',()=>{const v=str(S.drafts[nk]).trim();if(!v){ta.focus();return;}
    if(w.kind==='permission')answer({decision:'deny',message:v.slice(0,500)},'Note sent','Note sent · '+s.name);
    else{const qq=arr(w.questions)[0]||{};answer({decision:'allow',answers:{[qq.question||w.text]:v.slice(0,500)}},'Note sent','Note sent · '+s.name);}})));
  ks.append(lnk('Add a note',()=>{S.open[nk]=!S.open[nk];nb.hidden=!S.open[nk];if(!nb.hidden)ta.focus();}));
  ap(box,ks,nb);return box;
}
function tasksOf(s){
  const P=s.plan;
  if(P&&arr(P.tasks).length)return P.tasks.map(t=>({title:t.title,st:t.status==='done'?'done':/run|progress/.test(t.status)?'running':t.status==='failed'?'failed':'queued'}));
  if(arr(s.tasks).length)return s.tasks.map(t=>({title:str(t.subject).replace(/^\[[^\]]*\]\s*/,'').replace(/^[^:]+:\s*/,''),st:t.status==='completed'?'done':t.status==='in_progress'?'running':'queued'}));
  if(P)return Array.from({length:+P.total||0},(_,i)=>({st:i<P.done?'done':(!P.finished&&i===P.done?'running':'queued')}));
  return[];}
function planBlock(s){
  const t=tasksOf(s),d=t.filter(x=>x.st==='done').length,fin=s.plan&&s.plan.finished,p=h('div','plan'+(fin?' fin':''));
  const pt=ap(h('div','pt'),h('span','pn',(s.plan&&s.plan.title)||s.name),h('span','pc',fin?'Done '+hm(tm(s.plan.updatedAt)):d+'/'+t.length));
  const tr=h('div','track');tr.setAttribute('role','img');tr.setAttribute('aria-label',d+' of '+t.length+' tasks done');t.forEach(x=>tr.append(h('s',x.st)));
  ap(p,pt,tr);const cur=t.find(x=>x.st==='running');if(cur&&cur.title)p.append(h('p','agl','Now: '+cur.title));return p;}
function agentRow(a){
  const st=a.status,r=h('div','ag'),pc=st==='running'?'busy':st==='done'?'done':st==='failed'?'warn':'';
  const when=st==='running'?span(now()-tm(a.startedAt)):(st==='done'?'Done ':st==='failed'?'Failed ':'Stopped ')+hm(tm(a.endedAt));
  ap(r,pip(pc),ap(h('div','agt'),h('span','agn',a.title),h('span','age',when)));
  if(st==='running'&&a.current)r.append(h('div','step',a.current.text));
  return r;}
function spine(s){
  const T=a=>tm(a.endedAt||a.startedAt)||0;
  const ags=arr(s.agents).slice().sort((x,y)=>(x.status==='running'?0:1)-(y.status==='running'?0:1)||T(y)-T(x));
  const run=ags.filter(a=>a.status==='running'),rest=ags.filter(a=>a.status!=='running'),recent=rest.filter(a=>now()-(tm(a.endedAt)||0)<30*MIN).slice(0,2),older=rest.filter(a=>!recent.includes(a));
  const sp=h('div','spine');run.concat(recent).forEach(a=>sp.append(agentRow(a)));
  if(older.length){const k='old'+s.id,open=!!S.open[k];if(open)older.forEach(a=>sp.append(agentRow(a)));
    const b=lnk(open?'Fold earlier agents':older.length+' earlier agents',()=>{S.open[k]=!open;render();});b.setAttribute('aria-expanded',open);b.style.marginLeft='-6px';sp.append(b);}
  return sp;}
function meterEl(label,pct){pct=Math.max(0,Math.min(100,pct||0));const m=h('div','meter'+(pct>=85?' caut':''));m.setAttribute('role','meter');m.setAttribute('aria-valuenow',Math.round(pct));m.setAttribute('aria-valuemin',0);m.setAttribute('aria-valuemax',100);m.setAttribute('aria-label',label);
  const sg=h('div','segs');for(let i=0;i<20;i++)sg.append(h('s',i<Math.round(pct/5)?'on':null));ap(m,plate(label),sg,h('span','val',Math.round(pct)+'%'));return m;}
const can=(s,c)=>arr(s.controls).includes(c)&&!!(s.m.machine.commands&&s.m.machine.commands.enabled);
function sessPanel(s){
  const w=openAsk(s),p=h('section','panel sess'+(w?' need':''));
  p.append(ap(h('div','sh'),h('h3','nm',s.name),stateTag(s)));
  if(s.summary)p.append(h('p','sum',s.summary));
  if(ask(s))p.append(askBlock(s));
  if(s.plan||arr(s.tasks).length)p.append(planBlock(s));
  if(arr(s.agents).length)p.append(spine(s));
  const cx=s.ctx||{};
  const meta=ap(h('div','meta'),h('span',null,[modelName(s.model),s.effort,s.costUsd!=null?'$'+(+s.costUsd).toFixed(2):null].filter(Boolean).join(' · ')),cx.max?h('span',null,tok(cx.used)+' of '+tok(cx.max)):null);
  p.append(meta);if(cx.max)p.append(meterEl('Ctx',100*cx.used/cx.max));
  const kX='more'+s.id,acts=h('div','acts');
  const why=sessChatWhy(s);if(why&&!S.sess)p.append(ap(h('p','nochat'),h('span','sym','○'),ap(h('span'),h('b',null,'Can\'t chat: '),why)));   // Isaac: if it can't chat, it isn't in Chat
  const bm=S.sess||!(can(s,'transcript')||(!why&&s.m.status!=='online'))?null:lnk('Conversation',()=>go('crew',{crew:'claude',sess:{mk:s.mk,id:s.id}})),bx=lnk('More',()=>{S.open[kX]=!S.open[kX];render();});bx.setAttribute('aria-expanded',!!S.open[kX]);   // the session page holds the two-way conversation
  ap(acts,bm,bx);p.append(acts);
  if(S.open[kX]){const c=h('div','morebox'),kv=h('dl','kv');
    [['Machine',s.mk],['Started',hm(tm(s.startedAt))],['Updated',ago(tm(s.updatedAt))],['Session',str(s.id).slice(0,8)]].forEach(([k,v])=>ap(kv,h('dt',null,k),h('dd',null,v)));
    c.append(kv);
    if(s.status==='working'||s.status==='waiting'){const ks=h('div','keys');
      if(can(s,'interrupt')){let armed=null;const ik=key('Interrupt','',()=>{if(!armed){ik.textContent='Tap again to interrupt';armed=setTimeout(()=>{armed=null;ik.textContent='Interrupt';},4000*PACE());return;}clearTimeout(armed);armed=null;
        command(s.mk,'interrupt',{session:s.id},true).then(()=>toast('Interrupted · '+s.name),e=>failToast('Not interrupted',e));});ks.append(ik);}
      if(can(s,'stop')){const sk=key('Stop','guard');sk.addEventListener('click',()=>stopDlg(s,sk));ks.append(sk);}
      if(ks.childNodes.length)c.append(ks);}
    if(s.status==='ended'||s.status==='idle')c.append(ap(h('div','keys'),key('Clear','',()=>post(API.dismiss,{machine:s.mk,session:s.id,sessionDismissed:true,agents:arr(s.agents).filter(a=>a.status!=='running').map(a=>a.id).filter(Boolean)}).then(()=>toast('Cleared · '+s.name),e=>failToast('Not cleared',e)))));
    p.append(c);}
  return p;}
function stopDlg(s,back){guarded({title:'Stop '+s.name,q:'Stopping ends this session now. Its files stay as they are; anything mid-step is lost.',
  checks:[{t:'No question waiting for you',v:openAsk(s)?'caut':'go',word:'WAITING'},{t:s.mk+' is answering',v:s.m.status==='online'?'go':'nogo'},{t:'Last step saved '+hm(tm(s.updatedAt)),v:'go'}],
  act:'Stop '+s.name,back,go:()=>command(s.mk,'stop',{session:s.id},true).then(()=>toast('Stopped · '+s.name),e=>failToast('Not stopped',e))});}
function machineBlock(m){
  const box=h('div','stack'),ss=SESS.filter(s=>s.mk===m.key),on=m.status==='online',mx=machAlerts().find(x=>x.M===m),seen=hm(tm(m.machine.updatedAt));
  box.append(ap(h('div','mach'),h('h3','mn',m.machine.name||m.key),on?tag('run','Online'):mx?tag('warn','Missed '+mx.n+' runs · seen '+seen):tag('offline','Offline · seen '+seen)));
  const act=ss.filter(s=>s.status==='working'||s.status==='waiting');act.sort((a,b)=>(openAsk(b)?1:0)-(openAsk(a)?1:0));act.forEach(s=>box.append(sessPanel(s)));
  const rest=ss.filter(s=>!act.includes(s));
  if(rest.length){const k='idle'+m.key,open=!!S.open[k],l=h('div','list'),f=h('button','fold',rest.length+(rest.length===1&&rest[0].status==='ended'?' ended':' idle'));f.type='button';f.setAttribute('aria-expanded',open);f.addEventListener('click',()=>{S.open[k]=!open;render();});l.append(f);
    if(open)rest.sort((a,b)=>tm(b.updatedAt)-tm(a.updatedAt)).forEach(s=>{const r=h('div','li');ap(r,pip(''),h('span','t',s.name),h('span','m',s.status==='ended'?'Ended '+hm(tm(s.updatedAt)):'Idle · '+ago(tm(s.updatedAt))));r.append(h('span'));if(s.plan&&s.plan.finished)r.append(h('span','s','Plan done '+hm(tm(s.plan.updatedAt))));l.append(r);});
    box.append(l);}
  else if(!act.length)box.append(h('p','dim',on?'No Claude sessions.':'Nothing running here.'));
  return box;}

/* ---------- n8n parts ---------- */
/* the Agents tab: one section per job in a fixed order (Other last), then System folded; a failing or needs-you agent
   lights its section header and list edge in place, never reordering (design/bridge/categories-opencode) */
const JOBS=[['engineering','Engineering'],['home','Home'],['security','Security'],['downloads','Downloads'],['library','Library & media'],['general','Assistants'],['service','Infrastructure']];
const jobOf=a=>a.job||(a.role==='system'||(!a.role&&isSys(a))?'system':JOBS.some(j=>j[0]===a.role)?a.role:'other');
function agentRowEl(a){
  const st=aState(a),oc=isOC(a),r=h('div','li agr'+(oc?' ocrow':'')),nm=h('button','t',a.name);nm.type='button';
  nm.addEventListener('click',()=>go('crew',{agent:a.id,crew:'agents'}));
  let sub;
  if(oc){const L=ocSessions(a)||[],cur=L.find(s=>s.needsApproval)||L.find(s=>s.busy)||L[0]||a.lastSession;sub=h('span','s');
    if(st.c==='caut')ap(sub,h('span','cw','Approve in OpenCode'),cur?' · '+ocTitle(cur):'');else sub.append('OpenCode'+(cur?' · '+ocTitle(cur):''));}
  else{const lr=tm(a.last_run);sub=h('span','s',a.kind==='service'?['Service',a.version?'v'+a.version:null,a.last_status].filter(Boolean).join(' · '):untracked(a)?'Not tracked':(lr?'Last run '+(dayKey(lr)===dayKey(now())?hm(lr):(a.last_run_ago||dmon(lr))):'No runs yet'));}
  const bs=oc&&st.c==='busy'?ocBusySince(a):null;
  ap(r,pip(pipOf(st.c)),nm,h('span','m'+(st.c==='caut'?' caut':st.c==='warn'?' warn':''),bs?'Working '+span(now()-bs):st.w));
  if(a.chat){const tk=h('button','tk');tk.type='button';if(oc)tk.append(h('span','oc'));tk.append('Talk');tk.setAttribute('aria-label','Talk to '+a.name);   // off agents keep Talk too
    tk.addEventListener('click',e=>{e.stopPropagation();if(oc)ocTalk(a);else talk(a);});r.append(tk);}else r.append(h('span'));
  r.append(h('span','go'),sub);if(S.agent===a.id)r.setAttribute('aria-current','true');return r;}
function secSummary(L){const c={warn:0,caut:0,busy:0,run:0,off:0,offline:0};L.forEach(a=>{const k=aState(a).c;c[k]=(c[k]||0)+1;});
  const hot=[],calm=[];if(c.warn+c.offline)hot.push((c.warn+c.offline)+' failing');if(c.caut)hot.push(c.caut+' needs you');
  if(c.busy)calm.push(c.busy+' working');if(c.run)calm.push(c.run+' on');if(c.off)calm.push(c.off+' off');
  return{hot,calm,lvl:c.warn+c.offline?'warn':c.caut?'caut':''};}
function agentsBlock(){
  const box=h('div','secs');
  if(!AGENTS.length&&!SANDBOX_N){box.append(h('p','dim','No agents reported by n8n yet.'));return box;}
  JOBS.concat([['other','Other']]).forEach(([k,label])=>{const L=AGENTS.filter(a=>jobOf(a)===k);if(!L.length)return;
    const sm=secSummary(L),sec=h('section','sec'+(sm.lvl?' '+sm.lvl:'')),id=uid('sec'),hd=h('h3','sechd'),sum=h('span','sum');
    if(sm.hot.length)sum.append(h('span','hot',sm.hot.join(' · ')));if(sm.hot.length&&sm.calm.length)sum.append(' · ');sum.append(sm.calm.join(' · '));
    const pl=h('span','pl',label);pl.id=id;ap(hd,pl,sum);sec.setAttribute('aria-labelledby',id);
    const l=h('div','list');L.forEach(a=>l.append(agentRowEl(a)));ap(sec,hd,l);box.append(sec);});
  const sys=AGENTS.filter(a=>jobOf(a)==='system');
  if(sys.length||SANDBOX_N){const k='sys',open=!!S.open[k],sec=h('section','sec'),sl=h('div','list'),okN=sys.filter(a=>['run','busy'].includes(aState(a).c)).length;
    const f=h('button','fold');f.type='button';ap(f,h('span','plate','System'),h('span','num dim',okN===sys.length?sys.length+' online':(sys.length-okN)+' need you'),SANDBOX_N?h('span','hid','+'+SANDBOX_N+' sandbox cop'+(SANDBOX_N===1?'y':'ies')+' hidden'):null);
    f.setAttribute('aria-expanded',open);f.addEventListener('click',()=>{S.open[k]=!open;render();});sl.append(f);
    if(open)sys.forEach(a=>sl.append(agentRowEl(a)));const hd=h('div','sechd');hd.style.padding='8px 0 0';ap(sec,hd,sl);box.append(sec);}
  return box;}
const RUN_PICK=j=>arr(j&&(j.runs||j));
/* ---------- agent output + Talk (design/bridge/output-talk): plain words for roles, run modes and results ---------- */
const ROLE={engineering:'Engineering',home:'Home',downloads:'Downloads',library:'Library',security:'Security',general:'General',system:'System',service:'Service'};
const roleTag=a=>a&&ROLE[a.role]?h('span','rtag',ROLE[a.role]):null;
/* why an agent can't chat: n8n sends chat:false only; the words come from a chat_note field or this table */
const NOCHAT={'svc:wheatley':'Wheatley has its own voice app.','svc:llm':'it\'s the language model the agents use, not an agent.'};
const noChatWhy=a=>a.chat_note||NOCHAT[a.id]||(a.role==='system'||isSys(a)?'it only moves data for the Bridge.':'it isn\'t set up for chat yet.');
const MODE={trigger:'Scheduled',webhook:'From a message',manual:'Started in n8n',cli:'Started by command',integrated:'From the Bridge',retry:'Retried in n8n'};
const modeW=m=>MODE[m]||str(m);
const runBad=r=>['error','crashed','failed'].includes(r.status);
const runWord=r=>r.status==='success'?['run','Done']:runBad(r)?['warn','Failed']:['running','waiting','new'].includes(r.status)?['busy','Running']:r.status==='canceled'?['','Stopped']:['',str(r.status||'—')];
function whenLong(ms){if(ms==null)return'—';return dayKey(ms)===dayKey(now())?hm(ms):new Date(ms).toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short'})+' '+hm(ms);}
const updatedWord=ms=>now()-ms<3600e3?'Updated '+ago(ms):'Updated '+whenLong(ms)+' · '+ago(ms);
/* masked secrets arrive as one token (the server swaps the machine's •••• for it); drawn as •••• plus a tiny "masked" tag */
const MASK=/⟦masked⟧/g;
function maskInto(el,text){const parts=str(text).split(MASK);parts.forEach((p,i)=>{if(p)el.append(document.createTextNode(p));if(i<parts.length-1){const m=h('span','mask','••••');m.setAttribute('aria-label','hidden secret');el.append(m,h('span','mtag','masked'));}});return el;}
/* a readout well clipped to max lines, with Show all */
function outWell(text,max,k,label){
  const lines=str(text).replace(/\r\n?/g,'\n').split('\n'),long=lines.length>max+2,open=!!S.open[k];
  const w=h('div','well outw');w.setAttribute('tabindex','0');w.setAttribute('aria-label',label);maskInto(w,(long&&!open?lines.slice(0,max):lines).join('\n'));
  if(long&&!open)w.append(h('span','more','… '+(lines.length-max)+' more lines'));
  const box=gap(ap(h('div','stack'),w),'2px');
  if(long){const b=lnk(open?'Show less':'Show all '+lines.length+' lines',()=>{S.open[k]=!open;render();});b.setAttribute('aria-expanded',open);b.style.justifySelf='start';b.style.marginLeft='-6px';box.append(b);}
  return box;}
function patchAgent(id,o){const g=D.crew&&D.crew.agents;if(!g)return;['agents','services','system'].forEach(k=>arr(g[k]).forEach(a=>{if(a.id===id)Object.assign(a,o);}));}
function runLi(r,name,fn){const [pc,word]=runWord(r),d=h(fn?'button':'div','li');if(fn){d.type='button';d.addEventListener('click',fn);}const t0=tm(r.startedAt);
  ap(d,pip(pc),h('span','t',(name?name+' · ':'')+whenLong(t0)),h('span','m',r.stoppedAt&&r.startedAt?dur(tm(r.stoppedAt)-t0):''),h('span',fn?'go':null),
    h('span','s',[r.id?'Run '+r.id:'Last run',r.mode?modeW(r.mode):null,word].filter(Boolean).join(' · ')));
  if(String(S.run)===String(r.id))d.setAttribute('aria-current','true');return d;}
function lastFailedRun(a){const rs=a.workflowId?D.runs[a.workflowId]:null;return arr(rs).find(r=>r.status==='error'||r.status==='crashed'||r.status==='failed')||null;}
/* "Try again" needs a run trigger the API doesn't have: the fix key opens the failed run instead (its error and output) */
function failBanner(a,onDetail){const lr=tm(a.last_run),fr=lastFailedRun(a);
  const txt=(lr?'The last run stopped with an error at '+hm(lr)+'.':'The last run stopped with an error.')+(a.task?' '+a.task:'');
  const fix=onDetail?(fr?key('See error','fix',()=>go('crew',{agent:a.id,crew:'agents',run:fr.id})):null):key('Open','fix',()=>go('crew',{agent:a.id,crew:'agents'}));
  return banner('warn','◆',a.name+' failed',txt,ap(h('div','keys'),fix));}
function missBanner(x){const M=x.M;
  return banner('warn','◆',(M.machine.name||M.key)+' offline','Missed '+x.n+' scheduled run'+(x.n===1?'':'s')+' since '+hm(x.since||tm(M.machine.updatedAt))+'.',
    ap(h('div','keys'),key('Open Crew',alertN()===1?'fix':'',()=>go('crew',{crew:'claude'}))));}   // fix key lit only when it is the only alert

/* ---------- minimal safe formatting for agent text (markdown as plain text; no HTML, no links) ----------
   ``` fences → a code well; tables and headings → monospace preformatted lines; the rest → paragraphs (pre-wrap). */
function mdBlocks(text,into){
  const L=str(text).replace(/\r\n?/g,'\n').split('\n');let i=0,para=[];
  const flush=()=>{const t=para.join('\n').trim();if(t)into.append(h('p','md',t));para=[];};
  while(i<L.length){const ln=L[i];
    const f=/^\s*```\s*([\w+.-]*)/.exec(ln);
    if(f){flush();const body=[];i++;while(i<L.length&&!/^\s*```/.test(L[i]))body.push(L[i++]);i++;into.append(codeWell(f[1]||'Code',body.join('\n')));continue;}
    if(/^\s*\|.*\|\s*$/.test(ln)||/^#{1,6}\s/.test(ln)){flush();const body=[];
      while(i<L.length&&(/^\s*\|.*\|\s*$/.test(L[i])||/^#{1,6}\s/.test(L[i])))body.push(L[i++]);
      into.append(h('pre','mdpre',body.join('\n')));continue;}
    if(!ln.trim()){flush();i++;continue;}
    para.push(ln);i++;}
  flush();return into;}
function codeWell(label,text){const c=h('div','well code'),cp=h('button','cpy','Copy');cp.type='button';
  cp.addEventListener('click',()=>copyText(text).then(()=>toast('Copied'),()=>toast('Copy blocked · select the text instead')));
  ap(c,ap(h('div','hd plate'),h('span',null,label),cp),h('code',null,text));return c;}
function copyText(t){if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(t);
  return new Promise((res,rej)=>{const ta=h('textarea');ta.value=t;ta.style.cssText='position:fixed;left:-9999px';document.body.append(ta);ta.select();let ok=false;try{ok=document.execCommand('copy');}catch(e){}ta.remove();ok?res():rej();});}

/* ---------- views ---------- */
const V={};
V.now=function(m){
  const crew=need(m,'crew','the crew',4);if(!crew)return;use('overview');
  const n=needs(),al=alertAgents(),ma=machAlerts(),wk=working(),desk=isDesk(),N=n8nOf(),O=D.overview||{},tk=tNeeds()||[],oc=ocNeeds(),nn=n.length+tk.length+oc.length;
  const top=gap(h('div','stack'),'8px');
  const hero=al.length?al[0].name+' failed.':ma.length?str(ma[0].M.machine.name||ma[0].M.key).replace(/^./,c=>c.toUpperCase())+' missed '+ma[0].n+' runs.':nn===1?(n.length?n[0].name:tk.length?'Ticket #'+tk[0].id:oc[0].name)+' needs you.':nn?nn+' need you.':wk.length?'All fine.':'All quiet.';
  const mainA=AGENTS.filter(a=>!isSys(a)),onN=mainA.filter(a=>aState(a).c==='run').length,offN=mainA.filter(a=>aState(a).c==='off').length;
  const lead=[(al.length||ma.length)&&nn?nn+' need you':null,wk.length?wk.length+' running':'Nothing running',onN+' agents on'+(offN?', '+offN+' off':'')].filter(Boolean).join(' · ');
  ap(top,h('h1','hero',hero),h('p','lead',lead));
  const banners=[...al.map(a=>failBanner(a,false)),...ma.map(missBanner)];
  const needBox=nn?ap(h('div','stack'),...n.map(s=>{const p=h('section','panel need');p.append(ap(h('div','row sb'),plate('Needs you'),h('span','num dim','Since '+hm(tm(s.waiting.since)))),h('h2','vt',s.name),askBlock(s));return p;}),...tk.map(ticketNeed),...oc.map(ocNeed)):null;   // tickets after the Claude prompts, then OpenCode approvals
  const bd=h('div','board');bd.setAttribute('aria-label','Status board');
  const cell=(name,c,word,fn)=>{const b=h('button','ann '+c);b.type='button';ap(b,h('b',null,name),h('i',null,word));b.addEventListener('click',fn);bd.append(b);};
  SESS.filter(s=>s.status==='working'||s.status==='waiting').forEach(s=>cell(str(s.name).split(':')[0],openAsk(s)?'caut':'busy',openAsk(s)?'Needs you':'Working',()=>go('crew',{crew:'claude'})));
  mainA.forEach(a=>{const st=aState(a);cell(short(a.name),st.c,st.w,()=>go('crew',{agent:a.id,crew:'agents'}));});
  const sys=AGENTS.filter(isSys),sysBad=sys.filter(a=>!['run','busy'].includes(aState(a).c));
  if(sys.length)cell('System',sysBad.length?aState(sysBad[0]).c:'run',sysBad.length?sysBad.length+' need you':sys.length+' online',()=>{S.open.sys=true;go('crew',{crew:'agents'});});
  const boardBox=gap(ap(h('div','stack'),grouphd('Status board'),bd.childNodes.length?bd:h('p','dim','Nothing reported yet.')),'0');
  const rw=h('div','well logw');rw.setAttribute('aria-label','Running now');
  wk.forEach(s=>{const a=arr(s.agents).find(x=>x.status==='running');rw.append(ap(h('div','ln'),h('span','t',hm(tm(a&&a.current?a.current.at:s.updatedAt))),h('span','x',s.name+' · '+(a&&a.current?a.current.text:'Working'))));});
  const running=arr((O.running&&O.running.n8n)||N.running);
  running.forEach(r=>rw.append(ap(h('div','ln'),h('span','t',hm(tm(r.startedAt))),h('span','x',(r.name||r.workflowName||'n8n')+' · running'))));
  if(!running.length)rw.append(ap(h('div','ln'),h('span','t','—'),h('span','x','n8n · nothing running')));
  const runBox=gap(ap(h('div','stack'),grouphd('Running now'),rw),'0');
  const ml=h('div','list'),mrow=(pc,t,mm,s)=>{const r=h('div','li');ap(r,pip(pc),h('span','t',t),h('span','m',mm),h('span'),h('span','s',s));ml.append(r);};
  MACH.forEach(M=>{const ss=SESS.filter(s=>s.mk===M.key),mx=ma.find(x=>x.M===M),nm=M.machine.name||M.key,seen=hm(tm(M.machine.updatedAt));
    if(mx)return mrow('warn',nm,'Missed '+mx.n+' runs','Offline · seen '+seen);
    M.status==='online'?mrow('run',nm,'Online',ss.length+' Claude session'+(ss.length===1?'':'s')+(M.machine.version?' · Crew Hub '+M.machine.version:'')):mrow('offline',nm,'Offline','Seen '+seen+' · '+ss.length+' ended session'+(ss.length===1?'':'s'));});
  const hn=health().n8n||{},wf=AGENTS.filter(a=>a.kind!=='service').length,sv=AGENTS.length-wf,rd=tm(hn.at)||tm(N.updated);
  mrow(hn.ok===false?(isKeyErr(hn)?'warn':'offline'):'run','n8n',hn.ok===false?(isKeyErr(hn)?'Key refused':'No answer'):'Online',wf+' workflows · '+sv+' services'+(rd?' · read '+ago(rd):''));
  const hc=health().crew||{};if(!MACH.length)mrow(hc.connected===false?'offline':'','Claude machines',hc.connected===false?'Not connected':'None','Crew Hub reports no machines');
  const machBox=gap(ap(h('div','stack'),grouphd('Machines'),ml),'0');
  if(desk){const L=ap(h('div','stack'),top,...banners,boardBox,machBox),R=ap(h('div','stack'),needBox,runBox);m.append(ap(h('div','cols now'),L,R));}
  else ap(m,top,...banners,needBox,boardBox,runBox,machBox);
};
V.crew=function(m){
  if(!need(m,'crew','the crew',4))return;
  if(S.oc)return ocSession(m);
  if(S.sess)return sessionDetail(m);
  if(S.agent){const a=AGENTS.find(x=>x.id===S.agent);return isOC(a)?ocAgent(m,a):agentDetail(m);}
  const desk=isDesk(),cl=MACH.length?ap(h('div','stack'),...MACH.map(machineBlock)):ap(h('div','empty'),h('p','h','No Claude machines.'),h('p',null,'Machines show here once Crew Hub is set up on them.'));
  const ag=agentsBlock(),n=needs().length,al=alertAgents().length;
  m.append(ap(h('div','stack'),h('h1','hero',n?n+(n===1?' session needs':' sessions need')+' you.':'Crew'),h('p','lead',working().length+' Claude sessions working · '+AGENTS.filter(a=>aState(a).c==='run').length+' agents on'+(al?' · '+al+' failed':''))));
  if(desk){m.append(ap(h('div','cols crew'),ap(h('div','stack'),grouphd('Claude'),cl),ap(h('div','stack'),grouphd('Agents'),ag)));return;}
  const seg=h('div','segc full');seg.setAttribute('role','group');seg.setAttribute('aria-label','Crew group');
  [['claude','Claude',SESS.filter(s=>s.status==='working'||s.status==='waiting').length],['agents','Agents',AGENTS.length]].forEach(([v,l,c])=>{const b=h('button',null);b.type='button';ap(b,l,h('span','n',c));b.setAttribute('aria-pressed',S.crew===v);b.addEventListener('click',()=>{S.crew=v;history.replaceState(null,'','#'+route());render();});seg.append(b);});
  ap(m,seg,S.crew==='agents'?ag:cl);
};
function talk(a){post(API.threads,{kind:'n8n',target:a.id,title:a.name}).then(j=>{const t=j&&(j.thread||j);if(!t||!t.id)throw new ApiError('No thread',0);upsertThread(t);go('chat',{chat:t.id});},e=>failToast('Couldn\'t open the chat',e));}
/* Talk is lit when nothing else on the screen is; off agents with chat:true still talk (Isaac, 2026-11-18) */
function talkRow(a,lit){
  if(a.chat)return ap(h('div','keys'),key('Talk',lit?'lit':'',()=>talk(a)),h('span','dim','Chat with '+short(a.name)+' directly.'));
  return ap(h('p','nochat'),h('span','sym','○'),ap(h('span'),h('b',null,'Can\'t chat: '),noChatWhy(a)));}
const runKey=(a,id)=>a.id+'/'+id;
const RUN_ONE=j=>j&&j.run!==undefined?j.run:j;
/* agent detail: Latest output + Runs (workflows), Report (services) */
function agentDetail(m){
  const a=AGENTS.find(x=>x.id===S.agent);if(!a){m.append(ap(h('div','empty'),h('p','h','That agent is gone.'),h('p',null,'n8n no longer reports it.'),key('Back to Crew','',()=>go('crew',{crew:'agents'}))));return;}
  if(S.run)return runDetail(m,a);
  const st=aState(a),svc=a.kind==='service'||/^svc:/.test(a.id),desk=isDesk();
  m.append(crumb('Crew',()=>go('crew',{crew:'agents'}),a.name));
  m.append(ap(h('div','stack'),ap(h('div','row sb'),h('h1','vt',a.name),tag(pipOf(st.c),st.w)),
    h('p','lead',[svc?'Service'+(a.version?' v'+a.version:''):'n8n workflow'+(a.active===false?', turned off in n8n':''),a.role&&a.role!=='service'?ROLE[a.role]:null].filter(Boolean).join(' · '))));
  if(hasRuns(a))useSub(D.runs,a.workflowId,API.runs(a.workflowId),RUN_PICK);
  const failed=st.c==='warn';if(failed)m.append(failBanner(a,true));
  const lr=tm(a.last_run),kv=h('dl','kv');[['State',st.w],svc&&!lr?['Report',(()=>{const o=D.output[a.id];return o&&o.updated?updatedWord(tm(o.updated)).replace(/^Updated /,''):o===undefined&&!ERR[API.output(a.id)]?'…':'None';})()]:['Last run',untracked(a)?'Not tracked':lr?(svc?(a.last_run_ago||ago(lr)):whenLong(lr)+' · '+ago(lr)):'Never'],['Result',a.last_status||'—'],['Task',a.task||'None'],['ID',a.id]].forEach(([k,v])=>ap(kv,h('dt',null,k),h('dd',null,v)));
  m.append(ap(h('div','panel'),kv,talkRow(a,!failed)));
  const L=gap(h('div','stack'),'0'),R=gap(h('div','stack'),'0');
  if(svc)reportBlock(a,L);
  else if(!untracked(a)&&!isSys(a)){   // no Runs section for untracked or system workflows
    if(!hasRuns(a))L.append(grouphd('Runs'),h('p','dim',a.workflowId?'This workflow keeps no successful runs.':'No runs reported.'));
    else{const rs=D.runs[a.workflowId],e=ERR[API.runs(a.workflowId)];
      if(rs===undefined)L.append(grouphd('Latest output'),e?errState('the runs',e):skel(3));
      else if(!rs.length)L.append(grouphd('Runs'),h('p','dim','No runs yet.'));
      else{const r0=rs[0],u0=API.run(a.id,r0.id),R0=useSub(D.run,runKey(a,r0.id),u0,RUN_ONE),[pc,w]=runWord(r0);
        const lo=gap(h('button','panel lo'),'8px');lo.type='button';lo.addEventListener('click',()=>go('crew',{agent:a.id,crew:'agents',run:r0.id}));
        ap(lo,ap(h('span','row sb'),h('span','plate','Run '+r0.id+' · '+whenLong(tm(r0.startedAt))),tag(pc,w)));
        if(R0===undefined)lo.append(ERR[u0]?h('span','md clip6','Its summary didn\'t load.'):h('span','skel'));
        else{const sm=R0.summary||(R0.error?'Stopped with an error'+(R0.lastNode?' at “'+R0.lastNode+'”':'')+'.':null);lo.append(maskInto(h('span','md clip6'),sm||'No summary for this run.'));}
        lo.append(h('span','open','Open run '+r0.id));
        L.append(grouphd('Latest output'),lo,grouphd('Runs'),ap(h('div','list'),...rs.map(r=>runLi(r,null,()=>go('crew',{agent:a.id,crew:'agents',run:r.id})))));}}}
  R.append(grouphd('Tickets'));const TK=use('tickets');
  if(!TK)R.append(ERR.tickets?h('p','dim','Tickets didn\'t load.'):skel(2));
  else{const ts=TK.filter(t=>{const x=agentOfTicket(t);return x&&x.id===a.id;});R.append(ts.length?ap(h('div','list'),...ts.map(ticketLi)):h('p','dim','No tickets for this agent.'));}
  const nk=ap(h('div','keys'),key('New ticket','',()=>{S.draft={type:'bug',agent:a.id,title:'',details:''};go('tickets',{newT:true});}));nk.style.marginTop='10px';R.append(nk);
  m.append(!L.childNodes.length?R:desk?ap(h('div','cols two'),L,R):ap(h('div','stack'),L,R));
}
/* services: agent-output text (markdown shown as is in a monospace well), its time, Refresh (asks n8n again) */
function reportBlock(a,L){
  const url=API.output(a.id),o=useSub(D.output,a.id,url,j=>j&&j.output!==undefined?j.output:j),e=ERR[url],busy=S.refreshing===a.id,nm=short(a.name);
  L.append(grouphd('Report'));
  const asking=()=>ap(h('span','tag'),pip('busy'),'Asking '+nm+'…');
  if(o===undefined&&!e){L.append(ap(h('div','row sb rptop'),asking()),skel(3));return;}
  if(o===undefined){if(e.code==='no_output'||e.status===404){L.append(h('p','dim',nm+' doesn\'t write a report.'));return;}
    L.append(ap(h('div','errst'),h('span','h num','No report'),h('p',null,'n8n didn\'t send '+nm+'\'s report.'),h('div','well',e.message),key('Try again','',()=>{delete ERR[url];render();})));return;}
  if(o===null){L.append(h('p','dim',nm+' doesn\'t write a report.'));return;}
  const up=o&&tm(o.updated);
  L.append(ap(h('div','row sb rptop'),busy?asking():h('span','num dim',up?updatedWord(up):'No date on this report'),key('Refresh','',()=>refreshOut(a))));
  if(o&&o.text)L.append(outWell(o.text,24,'rpt:'+a.id,nm+' report'));else L.append(h('p','dim','Nothing written yet.'));}
function refreshOut(a){if(S.refreshing)return;S.refreshing=a.id;render();
  get(API.output(a.id)+'?fresh=1').then(j=>{D.output[a.id]=j&&j.output!==undefined?j.output:j;toast('Updated · '+short(a.name));},e=>failToast('Not updated',e)).finally(()=>{S.refreshing=null;render();});}
/* run view: lamp + word, times, error banner (+ Try again), Summary, What it did, Output */
function runDetail(m,a){
  const id=S.run,url=API.run(a.id,id),r=useSub(D.run,runKey(a,id),url,RUN_ONE),e=ERR[url],desk=isDesk();
  if(a.workflowId&&hasRuns(a))useSub(D.runs,a.workflowId,API.runs(a.workflowId),RUN_PICK);
  m.append(crumb(a.name,()=>go('crew',{agent:a.id,crew:'agents'}),'Run '+id));
  if(r===undefined){m.append(h('h1','vt','Run '+id));m.append(e?errState('run '+id,e,['Back to '+short(a.name),()=>go('crew',{agent:a.id,crew:'agents'})]):skel(3));return;}
  const [pc,word]=runWord(r),bad=runBad(r),t0=tm(r.startedAt),t1=tm(r.stoppedAt);
  m.append(ap(h('div','stack'),ap(h('div','row sb'),h('h1','vt','Run '+(r.id||id)),tag(pc,word)),h('p','lead',[short(a.name),r.mode?modeW(r.mode):null].filter(Boolean).join(' · ')),
    ap(h('div','meta runmeta'),h('span',null,'Started '+whenLong(t0)),h('span',null,t1?'Took '+dur(t1-t0):t0?'Running for '+dur(now()-t0):''))));
  if(bad)m.append(runErr(a,r));
  const L=h('div','stack'),R=h('div','stack');
  const sp=gap(h('div','panel'),'8px');sp.append(r.summary?maskInto(h('p','md'),r.summary):h('p','dim',bad?'No summary: the run stopped first.':'No summary for this run.'));
  L.append(gap(ap(h('div','stack'),grouphd('Summary'),sp),'0'));
  const tp=gap(h('div','panel'),'6px'),tl=arr(r.tools);
  if(tl.length){const l=h('div','steps');tl.forEach(t=>{const er=t.error,ln=h('div','stp');ap(ln,pip(er?'warn':'done'),h('span','n',t.tool||t.name||'Tool'),h('span','c',t.calls!=null?t.calls+(+t.calls===1?' call':' calls'):''));
    if(er)ln.append(h('span','why',er===true?'Failed':'Failed: '+str(er)));l.append(ln);});tp.append(l);}
  else tp.append(h('p','dim','No tools used in this run.'));
  if(r.lastNode)tp.append(ap(h('p','lastn'),h('span','plate',bad?'Stopped at':'Last step'),h('span',null,r.lastNode)));
  L.append(gap(ap(h('div','stack'),grouphd('What it did'),tp),'0'));
  const ob=gap(ap(h('div','stack'),grouphd('Output')),'0'),out=str(r.output).trim();
  if(out&&!['[]','{}','null','""'].includes(out))ob.append(outWell(prettyJson(r.output),12,'out:'+id,'Output of run '+id));
  else ob.append(ap(h('div','panel'),h('p','dim',bad?'No output: the run stopped before its last step.':'The last step'+(r.lastNode?', '+r.lastNode+',':'')+' returned nothing.')));
  R.append(ob);
  m.append(desk?ap(h('div','cols two'),L,R):ap(h('div','stack'),L,R));
}
function runErr(a,r){
  const rs=a.workflowId?arr(D.runs[a.workflowId]):[],newer=rs.filter(x=>tm(x.startedAt)>tm(r.startedAt)),ok=newer.find(x=>x.status==='success');
  let keys=null,note=null;
  if(ok)note='A later run worked: Run '+ok.id+', '+whenLong(tm(ok.startedAt))+'.';
  else if(a.canRerun&&!newer.length){const k=key('Try again','fix');k.addEventListener('click',()=>rerunDlg(a,k));keys=ap(h('div','keys'),k);}
  else if(!a.canRerun)note=r.mode==='webhook'?'It runs when someone asks it something, so there\'s nothing to run again here. Ask again in Chat.':'This workflow can\'t be run again from the Bridge.';
  const box=gap(h('div','stack'),'8px');
  box.append(banner('warn','◆','Run '+r.id+' failed',r.lastNode?'Stopped at “'+r.lastNode+'”.':'Stopped with an error.',keys));
  if(r.error){const w=gap(ap(h('div','well'),h('span','plate','Error from n8n')),'4px');w.style.display='grid';w.append(maskInto(h('span','outw'),r.error));box.append(w);}
  if(note)box.append(h('p','dim',note));
  return box;}
function rerunDlg(a,back){
  guarded({title:'Run again',q:(a.rerunHint||'Runs '+short(a.name)+' again now.')+' Its result shows here as a new run.',
    checks:[{t:'n8n is answering',v:(health().n8n||{}).ok===false?'nogo':'go'},{t:'Not running right now',v:a.task?'nogo':'go'},{t:'Sends your phone a notification again',v:'caut',word:'PHONE'}],
    act:'Run again',cancel:'Not now',back,
    go:()=>post(API.rerun,{agentId:a.id,confirm:true}).then(()=>{patchAgent(a.id,{state:'ok',task:'Running again (from the Bridge)'});delete D.runs[a.workflowId];toast('Started · '+short(a.name));go('crew',{agent:a.id,crew:'agents'});},e=>failToast('Not started',e))});}
function prettyJson(s){s=str(s);try{const v=JSON.parse(s);return JSON.stringify(v,null,2);}catch(e){return s;}}

/* ---------- Claude conversations (docs/BRIDGE.md §13): read from the machine while open, never stored here ----------
   The server asks the machine (crew_hub/transcript) and keeps the answer in memory for 60 s at most. While a conversation
   is on screen the page asks for newer turns every 3 s (after=cursor) and stops when it goes away (then tells the server
   to forget it). Steps are counts per assistant turn ("Read 3 files · Ran 2 commands"), never tool inputs. */
const CONV={},CONVL={};let convOpen=null,convSeen=null,convPolling=false;
const STEP_ORDER=['read','search','edit','write','run','web','agent','other'];
const STEPW={read:['Read','a file','files'],search:['Ran','a search','searches'],edit:['Edited','a file','files'],write:['Wrote','a file','files'],
  run:['Ran','a command','commands'],web:['Checked','a web page','web pages'],agent:['Started','an agent','agents'],other:['Took','another step','other steps']};
const stepWords=st=>STEP_ORDER.filter(k=>st&&+st[k]>0).map(k=>{const w=STEPW[k],n=+st[k];return w[0]+' '+(n===1?w[1]:n+' '+w[2]);}).join(' · ');
const CONV_ERR={offline:'The machine is offline.',no_transcript:'This session can\'t send its conversation. Update Crew Hub on its machine.',
  crew_disconnected:'The Bridge isn\'t connected to Home Assistant.',crew_timeout:'Home Assistant didn\'t answer in time.',no_ack:'The machine didn\'t answer.',
  bad_seal:'The machine\'s answer didn\'t check out, so it isn\'t shown.',too_large:'The conversation was too large to send.',not_configured:'Commands aren\'t set up for this machine in Crew Hub.',
  not_admin:'Only admins can read Claude conversations.',unknown_session:'That session is gone.'};
const convErr=e=>CONV_ERR[e&&e.code]||CONV_ERR[str(e&&e.code).replace(/^crew_/,'')]||(e&&e.message)||'error';
const mname=s=>(s.m&&s.m.machine&&s.m.machine.name)||s.mk;
/* why a session isn't a two-way chat (null when it is): ended, commands off, no send, or no transcript (an older Crew Hub) */
function sessChatWhy(s){const mn=mname(s);if(s.status==='ended')return'this session has ended.';
  if(!(s.m.machine.commands&&s.m.machine.commands.enabled))return'commands are off for '+mn+'. Turn them on in Crew Hub.';
  if(!can(s,'send'))return'this session can\'t take messages.';if(!can(s,'transcript'))return'update Crew Hub on '+mn+' to read its conversation here.';return null;}
const chatOk=s=>!sessChatWhy(s);
/* new machine turns join the list; a Bridge message the machine now reports replaces the local "Sending…" copy */
function mergeTurns(C,turns){const seen=new Set(arr(C.turns).map(t=>t.id)),loc=arr(C.turns).filter(t=>t.local),base=arr(C.turns).filter(t=>!t.local);
  arr(turns).forEach(t=>{if(seen.has(t.id))return;seen.add(t.id);
    if(t.k==='user'&&t.via==='bridge'){const i=loc.findIndex(x=>x.state!=='failed'&&str(x.text).trim()===str(t.text).trim());if(i>=0){t.state='delivered';loc.splice(i,1);}}
    base.push(t);});
  C.turns=base.concat(loc);}
function convOf(mk,id,fresh){const k=mk+'/'+id;convSeen={k,mk,id};if(!fresh&&(CONV[k]!==undefined||CONVL[k]))return CONV[k];CONVL[k]=true;
  get(API.conv(mk,id,{fresh})).then(j=>{const old=CONV[k],C=Object.assign({},j,{turns:arr(old&&old.turns).filter(t=>t.local)});mergeTurns(C,j&&j.turns);CONV[k]=C;},e=>{CONV[k]={error:e};})
    .finally(()=>{CONVL[k]=false;schedule();});return CONV[k];}
function convPoll(){const o=convOpen;if(!o||convPolling||document.hidden||CONVL[o.k])return;const C=CONV[o.k];if(!C||C.error||C.gone||!C.cursor)return;
  convPolling=true;
  get(API.conv(o.mk,o.id,{after:C.cursor})).then(j=>{if(CONV[o.k]!==C)return;if(j&&j.gone){C.gone=true;C.reason=j.reason;schedule();return;}
    if(j&&j.cursor)C.cursor=j.cursor;const n=arr(j&&j.turns);if(!n.length)return;const atB=nearBottom();mergeTurns(C,n);render();if(atB)toBottom();},()=>{})
    .finally(()=>{convPolling=false;});}
setInterval(convPoll,3000);
/* after each render: a conversation that is no longer on screen is forgotten here and on the server */
function convAfterRender(){const was=convOpen,now_=convSeen;convSeen=null;convOpen=now_;
  if(was&&(!now_||now_.k!==was.k)){delete CONV[was.k];delete CONVL[was.k];post(API.convClose(was.mk,was.id),{}).catch(()=>{});}}

/* Claude session page: the Crew panel, then the conversation (desktop: side by side) */
function sessionDetail(m){
  const s=SESS.find(x=>x.mk===S.sess.mk&&x.id===S.sess.id);
  if(!s){m.append(ap(h('div','empty'),h('p','h','That session is gone.'),h('p',null,'Crew Hub no longer reports it.'),key('Back to Crew','',()=>go('crew',{crew:'claude'}))));return;}
  const on=s.m.status==='online',mn=mname(s);
  m.append(crumb('Crew',()=>go('crew',{crew:'claude'}),s.name));
  m.append(ap(h('div','stack'),ap(h('div','row sb'),h('h1','vt',s.name),stateTag(s)),h('p','lead',[mn,modelName(s.model),s.effort].filter(Boolean).join(' · '))));
  const sp=sessPanel(s),sh=sp.querySelector('.sh'),spn=sp.querySelector('.spine');if(sh)sh.remove();if(spn)spn.replaceWith(agentsList(s));   // same panel as Crew: summary, ask, plan, agents, meta, More
  const cv=convBlock(s,on,mn);
  if(isDesk())m.append(ap(h('div','cols sessd'),sp,cv));else ap(m,sp,cv);
}
function agentReport(s,a){const C=CONV[s.mk+'/'+s.id],t=C&&arr(C.turns).find(x=>x.k==='report'&&x.agentId===a.id);return t?t.text:(a.status!=='running'&&a.summary)||null;}
function agentsList(s){
  const T=a=>tm(a.endedAt||a.startedAt)||0,sp=h('div','spine');
  arr(s.agents).slice().sort((x,y)=>(x.status==='running'?0:1)-(y.status==='running'?0:1)||T(y)-T(x)).forEach(a=>{
    const k='ag:'+s.id+':'+a.id,open=!!S.open[k],st=a.status,pc=st==='running'?'busy':st==='done'?'done':st==='failed'?'warn':'';
    const when=st==='running'?span(now()-tm(a.startedAt)):(st==='done'?'Done ':st==='failed'?'Failed ':'Stopped ')+hm(tm(a.endedAt));
    const b=h('button','ag');b.type='button';b.setAttribute('aria-expanded',open);
    ap(b,pip(pc),ap(h('span','agt'),h('span','agn',a.title),h('span','age',when)));
    b.append(st==='running'&&a.current?h('span','step',a.current.text):h('span','agl rep-l',open?'Hide report':'Final report'));
    b.addEventListener('click',()=>{S.open[k]=!open;render();});sp.append(b);
    if(open){const x=h('div','agx'),rep=agentReport(s,a);
      if(rep)x.append(gap(ap(h('div','well'),h('span','plate','Final report · '+short(a.title)),maskInto(h('p','md'),rep)),'6px'));
      else x.append(h('p','dim',st==='running'?'Still working. Its final report shows here when it\'s done.':'No final report.'));
      if(chatOk(s)){x.append(ap(h('p','dim tal'),'To talk to this agent, message its session.'),ap(h('div','keys'),lnk('Message '+s.name,()=>focusComposer(s))));x.lastChild.style.margin='-8px 0 0 -6px';}
      sp.append(x);}});
  return sp;}
function focusComposer(s){const ta=main.querySelector('[data-k="cm:'+s.mk+'/'+s.id+'"]');if(ta&&!ta.disabled){ta.focus({preventScroll:true});ta.scrollIntoView({block:'center'});}}
function turnEls(s,t,c,retry){const mn=mname(s),at=tm(t.at);
  if(t.k==='user'){c.append(maskInto(h('div','u'),t.text));const by=h('span','by'+(t.state==='failed'?' bad':''),['You',at?hm(at):null,t.via==='bridge'?'from the Bridge':'on '+mn,t.state==='sending'?'Sending…':t.state==='delivered'?'Delivered':t.state==='failed'?'Not delivered: '+(t.err||'no answer'):null].filter(Boolean).join(' · '));c.append(by);
    if(t.state==='failed'&&retry){const b=lnk('Try again',retry);b.style.justifySelf='end';c.append(b);}return;}
  if(t.k==='assistant'){const a=h('div','a');a.append(ap(h('span','who',s.name),h('span','num',at?hm(at):'')));str(t.text).split(/\n{2,}/).forEach(p=>{if(p.trim())a.append(maskInto(h('p','md'),p));});c.append(a);
    const sw=stepWords(t.steps);if(sw)c.append(ap(h('div','stepl'),ap(h('span','tool'),pip('done'),h('span',null,sw))));return;}
  if(t.k==='report'){c.append(gap(ap(h('div','well rep'),ap(h('span','row sb'),h('span','plate','Agent report'+(t.title?' · '+short(t.title):'')),h('span','plate',at?hm(at):'')),maskInto(h('p','md'),t.text)),'6px'));}}
function workingLine(s){const a=arr(s.agents).find(x=>x.status==='running');return ap(h('div','stepl'),ap(h('span','tool busy'),pip('busy'),h('span',null,'Working…'+(a&&a.title?' · '+a.title:''))));}
/* fetch line + turns; extra = stored or local turns still on their way (chat thread) */
function convBody(s,mn,extra){
  const k=s.mk+'/'+s.id,C=convOf(s.mk,s.id),busy=CONVL[k],out=[],c=h('div','convo');c.setAttribute('aria-live','polite');
  const nt=h('div','fetchln');
  nt.append(busy||C===undefined?ap(h('span','tag'),pip('busy'),'Asking '+mn+'…'):h('span',null,'Fetched from '+mn+' when you opened this; not stored.'),lnk('Refresh',()=>{convOf(s.mk,s.id,true);render();}));
  out.push(nt);
  if(C===undefined)c.append(skel(3));
  else if(C.error)c.append(ap(h('div','errst'),h('span','h num','Couldn\'t get the conversation'),h('p',null,mn+' didn\'t send it.'),h('div','well',convErr(C.error)),key('Try again','',()=>{convOf(s.mk,s.id,true);render();})));
  else if(C.gone)c.append(ap(h('div','empty'),h('p','h','Older turns aren\'t kept here.'),h('p',null,mn+' no longer has this session\'s conversation.'+(s.status==='ended'?' It ended '+whenLong(tm(s.updatedAt))+'; its last summary is above.':''))));
  else{const T=arr(C.turns),X=extra?extra(T):[];if(C.cut)c.append(h('p','st','Older turns aren\'t kept here.'));T.concat(X).forEach(t=>turnEls(s,t,c,t.retry));
    if(!T.length&&!X.length)c.append(h('p','st','Nothing said yet in this session.'));
    if(s.status==='working')c.append(workingLine(s));}
  out.push(c);return out;}
function convBlock(s,on,mn){
  const box=gap(h('div','stack conv'),'0');box.append(grouphd('Conversation'));
  const why=sessChatWhy(s);
  if(why&&!can(s,'transcript')){box.append(ap(h('p','nochat'),h('span','sym','○'),ap(h('span'),h('b',null,'Can\'t chat: '),why)));return box;}
  if(!on){const seen=hm(tm(s.m.machine.updatedAt||s.m.updatedAt));
    box.append(ap(h('div','oneway offbox'),ap(h('span','tag'),pip('offline'),mn+' is offline · last seen '+seen),h('p',null,'The conversation comes straight from '+mn+', so it shows again when '+mn+' is back. Nothing is kept here.')));
    box.append(sessComposer(s,false,mn,mn+' can\'t take messages while it\'s offline.'));return box;}
  ap(box,...convBody(s,mn));
  if(s.status==='ended')box.append(h('p','oneway','This session has ended, so it can\'t take messages.'));
  else if(why)box.append(ap(h('p','nochat'),h('span','sym','○'),ap(h('span'),h('b',null,'Can\'t chat: '),why)));
  else box.append(sessComposer(s,true,mn,'Goes to '+mn+' as a message from you. Replies show here while this is open.'));
  return box;}
function sessComposer(s,ok,mn,note){
  const k=s.mk+'/'+s.id,dk='cm:'+k,cp=h('div','composer'),pn=h('div','panel'),ta=h('textarea');ta.rows=2;ta.maxLength=4000;ta.placeholder='Message '+s.name;ta.setAttribute('aria-label','Message '+s.name);bindDraft(ta,dk);ta.disabled=!ok;
  const ck=h('label','chk'),cb=h('input');cb.type='checkbox';cb.disabled=!ok;ap(ck,cb,'Urgent: interrupt the current step');
  const send=()=>{const v=str(S.drafts[dk]).trim();if(!v||!ok)return;const urgent=cb.checked;delete S.drafts[dk];ta.value='';
    const C=CONV[k],t={id:'u'+uuid(),k:'user',at:now(),text:v,via:'bridge',state:'sending',local:true};if(C&&C.turns)C.turns.push(t);render();toBottom();
    command(s.mk,'send',{session:s.id,text:v,urgent}).then(()=>{if(t.state==='sending')t.state='delivered';render();},e=>{t.state='failed';t.err=e.message;render();});};
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
  const sk=key('Send',ok?'lit':'',send);sk.disabled=!ok;
  ap(pn,ta,ap(h('div','row sb'),s.status==='working'&&ok?ck:h('span'),sk));cp.append(pn,h('p','cnote',note));return cp;}

/* ---------- chat: threads persisted on the server (kept 90 days); n8n replies arrive over SSE ---------- */
const mT=m=>tm(m.created_at!=null?m.created_at:m.createdAt);
const mTid=m=>m.thread_id!=null?m.thread_id:m.threadId;
const mCid=m=>m.client_id!=null?m.client_id:m.clientId;
const tWhen=t=>tm(t.last_message_at||t.lastMessageAt||t.updated_at||t.updatedAt||t.created_at||t.createdAt)||0;
const INFLIGHT=['queued','sending'],FAILED=['failed','timeout','unknown','dropped'];
function upsertThread(t){if(!D.threads)D.threads=[];const i=D.threads.findIndex(x=>x.id===t.id);if(i>=0)D.threads[i]=Object.assign({},D.threads[i],t);else D.threads.unshift(t);}
function upsertMsg(m){const tid=mTid(m);
  if(D.msgs[tid]){const L=D.msgs[tid];const i=L.findIndex(x=>(x.id!=null&&x.id===m.id)||(mCid(m)&&mCid(x)===mCid(m)));if(i>=0)L[i]=Object.assign({},L[i],m);else{L.push(m);L.sort((a,b)=>(mT(a)||0)-(mT(b)||0)||(+a.id||0)-(+b.id||0));}}
  if(D.threads){const t=D.threads.find(x=>x.id===tid);if(t){const L=D.msgs[tid];const last=L&&L.length?L[L.length-1]:m;
    if(!L||last===m||last.id===m.id){t.last={role:m.role,text:m.text,state:m.state};t.last_message_at=mT(m)||now();}else t.last={role:last.role,text:last.text,state:last.state};
    if(m.role==='agent'&&!(S.view==='chat'&&S.chat===tid&&!document.hidden)&&!m._seen){t.unread=(+t.unread||0)+1;m._seen=1;}}}}
function loadMsgs(id,before){const url=API.messages(id,before);if(LOADING[url])return;LOADING[url]=true;
  get(url).then(j=>{const L=arr(j&&(j.messages||j));const cur=before!=null?arr(D.msgs[id]):[];const seen=new Set(cur.map(x=>x.id));
    D.msgs[id]=L.filter(x=>!seen.has(x.id)).concat(cur);D.more[id]=!!(j&&(j.more||j.hasMore))||(L.length>=50&&!(j&&j.more===false));delete ERR[url];if(before==null)S.stick=true;},
    e=>{ERR[url]=e;}).finally(()=>{LOADING[url]=false;schedule();});}
const isClaude=t=>t&&t.kind==='claude';
function threadName(t){if(!t)return'Agent Hub';const tn=t.targetName||t.target_name;if(isClaude(t))return tn||t.title||'Claude';if(!t.target||t.target==='crew')return'Agent Hub';const a=AGENTS.find(x=>x.id===t.target);return a?a.name:(tn||t.target);}
const directOf=t=>t&&t.kind==='n8n'&&t.target&&t.target!=='crew'?AGENTS.find(x=>x.id===t.target)||null:null;
function threadTitle(t){if(t&&t.kind==='n8n'&&t.target&&t.target!=='crew')return threadName(t);return t.title||(isClaude(t)?threadName(t):'New chat');}   // a direct thread is titled with its agent
function threadBusy(t){const L=D.msgs[t.id];if(L&&L.length){const u=[...L].reverse().find(x=>x.role==='user');return !!(u&&INFLIGHT.includes(u.state))&&!isClaude(t);}return !isClaude(t)&&!!(t.last&&t.last.role==='user'&&INFLIGHT.includes(t.last.state))||!!t.busy;}
function threadSub(t){if(threadBusy(t))return'Thinking…';const l=t.last||{};const tx=str(l.text||t.last_text||t.lastText).replace(/\s+/g,' ');if(!tx)return isClaude(t)?'Claude session':'No messages yet';return(l.role==='user'?'You: ':'')+tx;}
/* the page scrolls as a whole (inside HA's ingress iframe too): live updates keep its position, see render() */
const scroller=()=>document.scrollingElement||document.documentElement;
function nearBottom(px){const se=scroller();return se.scrollTop+innerHeight>=se.scrollHeight-(px==null?80:px);}
function toBottom(){const se=scroller();se.scrollTop=se.scrollHeight;}
function threadMark(){if(S.view!=='chat'||!S.chat||S.chat==='list'||S.chat==='new')return null;const L=D.msgs[S.chat];if(!L||!L.length)return{id:S.chat,n:L?0:-1};
  const l=L[L.length-1];return{id:S.chat,n:L.length,first:L[0].id,last:l.id,role:l.role,st:l.state};}
function placeChip(){const old=main.querySelector('.newreply');if(old)old.remove();if(!S.newReply||S.view!=='chat'||S.newReply!==S.chat)return;const cp=main.querySelector('.composer');if(!cp)return;
  const b=h('button','newreply','New reply ↓');b.type='button';b.addEventListener('click',()=>{S.newReply=null;b.remove();toBottom();});cp.prepend(b);}
addEventListener('scroll',()=>{if(S.newReply&&nearBottom()){S.newReply=null;placeChip();}},{passive:true});
function markRead(t){if(!t)return;const L=D.msgs[t.id];const last=L&&L.length?mT(L[L.length-1]):null;if(!(+t.unread)&&S.readFor===t.id+':'+last)return;S.readFor=t.id+':'+last;t.unread=0;
  post(API.read,{scope:'thread:'+t.id,at:now()}).catch(()=>{});}
function chatList(){
  const L=h('div','clist'),srch=h('div','search'),inp=h('input','inp');inp.type='search';inp.placeholder='Search chats';inp.setAttribute('aria-label','Search chats');bindDraft(inp,'chatsearch');srch.append(inp);
  L.append(ap(h('div','row sb'),plate('Chats'),key('New chat','',()=>go('chat',{chat:'new'}))),srch);
  const all=arr(D.threads).filter(t=>!t.archived).slice().sort((a,b)=>tWhen(b)-tWhen(a)),TH=all.filter(t=>!isClaude(t));
  /* Claude: two-way sessions only (send + transcript, commands on, not ended); a row without a thread opens one */
  const CS=SESS.filter(chatOk).sort((a,b)=>(tm(b.updatedAt)||0)-(tm(a.updatedAt)||0)),known=new Set(CS.map(s=>s.mk+'/'+s.id));
  const CT=all.filter(t=>isClaude(t)&&!known.has(t.target)&&!MACH.length);   // crew not loaded yet: keep the threads the server listed
  const OCC=ocChats();
  if(!TH.length&&!CS.length&&!CT.length&&!OCC.length){L.append(h('p','dim','No chats yet. Ask the crew something, or open an agent\'s Talk.'));return L;}
  const row=(title,sub,unread,cur,fn)=>{const b=h('button','cl');b.type='button';b.dataset.title=title.toLowerCase();ap(b,h('span','t',title),countBox(unread||0,false,'unread'),h('span','s',sub));if(cur)b.setAttribute('aria-current','true');b.addEventListener('click',fn);return b;};
  const IT=TH.map(t=>({t,w:tWhen(t)})).concat(OCC).sort((x,y)=>y.w-x.w);
  const groups={},order=[];IT.forEach(x=>{const g=dayName(x.w||now());if(!groups[g]){groups[g]=[];order.push(g);}groups[g].push(x);});
  order.forEach(g=>{const box=gap(h('div','stack'),'2px');box.append(h('div','grouphd',g));
    groups[g].forEach(({t,a,s})=>{if(a){const b=row(a.name+' · OpenCode '+ocTitle(s),'',0,false,()=>go('crew',{oc:{a:a.id,s:s.id}}));b.firstChild.textContent=a.name+' · OpenCode';
        const sub=b.lastChild;sub.textContent='';if(s.needsApproval)ap(sub,h('span','cw','Needs your approval'),' · '+ocTitle(s));else sub.append((s.busy?'Working… · ':'')+ocTitle(s));box.append(b);return;}
      const b=row(threadTitle(t)+' '+threadName(t),threadSub(t),+t.unread,S.chat===t.id,()=>go('chat',{chat:t.id}));b.firstChild.textContent=threadTitle(t);box.append(b);});L.append(box);});
  if(CS.length||CT.length){const box=gap(h('div','stack'),'2px');box.append(h('div','grouphd','Claude'));
    CS.forEach(s=>{const t=all.find(x=>isClaude(x)&&x.target===s.mk+'/'+s.id);const b=row(s.name+' '+mname(s),claudeSub(s,t),t&&+t.unread,t&&S.chat===t.id,()=>openClaude(s));b.firstChild.textContent=s.name;box.append(b);});
    CT.forEach(t=>{const b=row(threadTitle(t),threadSub(t),+t.unread,S.chat===t.id,()=>go('chat',{chat:t.id}));box.append(b);});L.append(box);}
  const filt=()=>{const q=inp.value.trim().toLowerCase();L.querySelectorAll('.cl').forEach(b=>{b.hidden=!!q&&!b.dataset.title.includes(q);});};inp.addEventListener('input',filt);filt();
  return L;}
function msgEl(t,mm){
  if(mm.role==='user')return h('div','u',mm.text);
  if(mm.role==='status'){const p=h('p','st',mm.text);return p;}
  const a=h('div','a');a.append(ap(h('span','who',threadName(t)),h('span','num',hm(mT(mm)))));
  mdBlocks(mm.text,a);return a;}
const FAILTXT={timeout:['No reply after 2 min',n=>n+' didn\'t answer. Your message is saved here.'],unknown:['No reply',()=>'The Bridge restarted while waiting. The agent may have acted, so check before trying again.'],
  dropped:['Not sent',()=>'It waited over 10 min behind another message.'],failed:['Not sent',(n,e)=>e?str(e):n+' couldn\'t take it. Your message is saved here.']};
function chatThread(t,id){
  if(isClaude(t))return claudeThread(t);
  const V_=h('div','cview'),top=h('div','ctop'),cl=false,dA=directOf(t);ap(top,h('h1','vt',t?threadTitle(t):'New chat'),dA?ap(h('span','keys'),roleTag(dA),plate('Direct')):plate(threadName(t)));V_.append(top);
  const c=h('div','convo');c.setAttribute('aria-live','polite');let fail=null;
  const L=id?D.msgs[id]:[];if(id&&L===undefined&&!ERR[API.messages(id)])loadMsgs(id);
  if(id&&ERR[API.messages(id)]&&L===undefined)c.append(errState('this chat',ERR[API.messages(id)]));
  else if(L===undefined)c.append(skel(3));
  else{
    if(D.more[id]){const b=lnk('Earlier messages',()=>{const first=L[0];loadMsgs(id,first?first.id:null);});b.style.justifySelf='center';c.append(b);}
    let lastDay=null;L.forEach(mm=>{const d=dayKey(mT(mm)||now());if(d!==lastDay){c.append(plate(dayName(mT(mm)||now()),'day'));lastDay=d;}c.append(msgEl(t,mm));});
    const lastU=[...L].reverse().find(x=>x.role==='user'),lastA=[...L].reverse().find(x=>x.role==='agent');
    const answered=lastU&&lastA&&(+lastA.id>+lastU.id||mT(lastA)>mT(lastU));
    if(lastU&&FAILED.includes(lastU.state)&&!answered){fail=lastU;const ft=FAILTXT[lastU.state]||FAILTXT.failed;const nm=threadName(t);
      c.append(banner('warn','◆',cl&&lastU.state==='failed'?'Not delivered':ft[0],cl?(CMD_ERR[lastU.error]||str(lastU.error||'The machine didn\'t take it.')):ft[1](nm,lastU.error),
        ap(h('div','keys'),key('Try again','fix',()=>post(API.retry(id,lastU.id),{}).then(j=>{if(j&&(j.message||j.id))upsertMsg(j.message||j);S.stick=true;render();},e=>failToast('Not sent',e))),lnk('All chats',()=>go('chat',{chat:'list'})))));}
    else if(!cl&&lastU&&INFLIGHT.includes(lastU.state)){const t0=tm(lastU.startedAt||lastU.sentAt)||mT(lastU)||now();S.think0=t0;const el=Math.max(0,Math.round((now()-t0)/1000));
      const q=lastU.state==='queued'&&L.some(x=>x!==lastU&&x.role==='user'&&x.state==='sending');
      const tl=ap(h('div','a'),ap(h('span','who',threadName(t))),ap(h('span','tool busy'),pip('busy'),ap(h('span',null,q?'Queued… ':'Thinking… '),h('span','num',Math.floor(el/60)+':'+p2(el%60)))),h('p','dim','Replies take 10–60 s, sometimes 2 min. You can close the Bridge; the reply waits here.'));
      tl.querySelector('.tool .num').id='think-el';c.append(tl);}
    else if(!L.length&&!cl)c.append(ap(h('div','empty'),h('p','h',t&&t.target&&t.target!=='crew'?'Ask '+short(threadName(t))+' anything.':'Ask the crew anything.'),h('p',null,dA?short(threadName(t))+' answers from its own data, in its own words. Replies take up to a minute.':'Agent Hub can read the agents\' state, file tickets and start runs. Replies take up to a minute.')));
  }
  V_.append(c);
  const dk='chat:'+(id||'new'),cp=h('div','composer'),pn=h('div','panel'),ta=h('textarea');ta.rows=2;ta.maxLength=4000;ta.placeholder='Message '+(cl?threadName(t):t&&t.target&&t.target!=='crew'?short(threadName(t)):'the crew');ta.setAttribute('aria-label','Message');bindDraft(ta,dk);
  const send=()=>{const v=str(S.drafts[dk]).trim();if(!v)return;delete S.drafts[dk];ta.value='';sendChat(t,id,v).catch(e=>{S.drafts[dk]=v;failToast('Not sent',e);render();});};
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
  ap(pn,ta,ap(h('div','row sb'),h('span','dim desk-only','Enter sends · Shift+Enter for a new line'),h('span','phone-only'),key('Send',fail?'':'lit',send)));
  cp.append(pn,h('p','cnote','Chats are kept on the Bridge for 90 days, so closing it loses nothing.'));V_.append(cp);return V_;}
/* Claude threads in Chat are pointers (machine/session): the conversation comes from the machine while the thread is
   open; only what you send is stored (and shown here until the machine reports it). */
const sessOf=t=>{const p=str(t&&t.target).split('/'),mk=p[0],id=p.slice(1).join('/');return SESS.find(x=>x.mk===mk&&x.id===id)||null;};
const sessWord=s=>s.m.status!=='online'?'Offline':openAsk(s)?'Needs you':s.status==='working'?'Working':s.status==='ended'?'Ended':'Idle';
function claudeSub(s,t){const l=(t&&t.last)||{},tx=str(l.text||(t&&(t.last_text||t.lastText))).replace(/\s+/g,' ');if(tx&&l.role==='user')return'You: '+tx;return mname(s)+' · '+sessWord(s);}
function openClaude(s){const target=s.mk+'/'+s.id,t=arr(D.threads).find(x=>isClaude(x)&&x.target===target&&!x.archived);if(t)return go('chat',{chat:t.id});
  post(API.threads,{kind:'claude',target}).then(j=>{const nt=j&&(j.thread||j);if(!nt||!nt.id)throw new ApiError('No thread',0);upsertThread(nt);go('chat',{chat:nt.id});},e=>failToast('Couldn\'t open the chat',e));}
function storedPending(t,T){const L=D.msgs[t.id];if(L===undefined&&!ERR[API.messages(t.id)])loadMsgs(t.id);
  const sent=T.filter(x=>x.k==='user'&&x.via==='bridge'),lastAt=T.reduce((a,x)=>Math.max(a,tm(x.at)||0),0),out=[];
  arr(L).filter(m=>m.role==='user').forEach(m=>{const i=sent.findIndex(x=>str(x.text).trim()===str(m.text).trim());if(i>=0){if(m.state==='delivered')sent[i].state='delivered';sent.splice(i,1);return;}
    const st=INFLIGHT.includes(m.state)?'sending':FAILED.includes(m.state)?'failed':m.state==='delivered'?'delivered':null,at=mT(m)||0;if(!st)return;
    if(st==='delivered'&&at<=lastAt)return;if(st==='failed'&&at<=lastAt&&now()-at>10*MIN)return;
    out.push({id:'m'+m.id,k:'user',at,text:m.text,via:'bridge',state:st,err:st==='failed'?(CMD_ERR[m.error]||str(m.error||'')||null):null,
      retry:st==='failed'&&m.id!=null&&!String(m.id).startsWith('tmp-')?()=>post(API.retry(t.id,m.id),{}).then(j=>{if(j&&(j.message||j.id))upsertMsg(j.message||j);render();},e=>failToast('Not sent',e)):null});});
  return out;}
function claudeThread(t){
  const s=sessOf(t),mn=s?mname(s):str(t.target).split('/')[0],V_=h('div','cview'),top=h('div','ctop');
  ap(top,h('h1','vt',threadTitle(t)),plate('Claude · '+mn));V_.append(top);
  const ok=!!s&&s.m.status==='online'&&chatOk(s);
  if(!s){V_.append(ap(h('div','convo'),ap(h('div','empty'),h('p','h','That session is gone.'),h('p',null,'Crew Hub no longer reports it, so there\'s nothing to read or send.'))));return V_;}
  if(s.m.status!=='online')V_.append(ap(h('div','convo'),ap(h('div','oneway offbox'),ap(h('span','tag'),pip('offline'),mn+' is offline · last seen '+hm(tm(s.m.machine.updatedAt||s.m.updatedAt))),h('p',null,'The conversation comes straight from '+mn+', so it shows again when '+mn+' is back. Nothing is kept here.'))));
  else if(!chatOk(s)&&!can(s,'transcript')){V_.append(ap(h('p','nochat'),h('span','sym','○'),ap(h('span'),h('b',null,'Can\'t chat: '),sessChatWhy(s))));return V_;}
  else{const [nt,c]=convBody(s,mn,T=>storedPending(t,T));c.prepend(nt);V_.append(c);}   // the chat view is ctop / convo / composer
  if(s.status==='ended'){V_.append(h('p','oneway','This session has ended, so it can\'t take messages.'));return V_;}
  const dk='chat:'+t.id,cp=h('div','composer'),pn=h('div','panel'),ta=h('textarea');ta.rows=2;ta.maxLength=4000;ta.placeholder='Message '+s.name;ta.setAttribute('aria-label','Message '+s.name);bindDraft(ta,dk);ta.disabled=!ok;
  const ck=h('label','chk'),cb=h('input');cb.type='checkbox';cb.disabled=!ok;ap(ck,cb,'Urgent: interrupt the current step');
  const send=()=>{const v=str(S.drafts[dk]).trim();if(!v||!ok)return;const urgent=cb.checked;delete S.drafts[dk];ta.value='';sendChat(t,t.id,v,{urgent}).catch(e=>{S.drafts[dk]=v;failToast('Not sent',e);render();});};
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
  const sk=key('Send',ok?'lit':'',send);sk.disabled=!ok;
  ap(pn,ta,ap(h('div','row sb'),s.status==='working'&&ok?ck:h('span'),sk));
  cp.append(pn,h('p','cnote',ok?'Goes to '+mn+' as a message from you. Replies show here while this is open.':mn+' can\'t take messages while it\'s offline.'));V_.append(cp);return V_;}
async function sendChat(t,id,text,opt){
  if(!id){const j=await post(API.threads,{kind:'n8n',target:'crew',new:true});const nt=j&&(j.thread||j);if(!nt||!nt.id)throw new ApiError('No thread',0);upsertThread(nt);D.msgs[nt.id]=[];id=nt.id;t=nt;S.chat=id;history.replaceState(null,'','#'+route());}
  const cid=uuid(),tmp={id:'tmp-'+cid,threadId:id,role:'user',text,state:'queued',clientId:cid,createdAt:now()};
  if(!D.msgs[id])D.msgs[id]=[];upsertMsg(tmp);S.stick=true;render();
  try{const j=await post(API.send(id),Object.assign({text,clientId:cid},opt&&opt.urgent?{urgent:true}:{}));const m=j&&(j.message||j);if(m&&m.id!=null){const L=D.msgs[id];const i=L.findIndex(x=>x.id===tmp.id);if(i>=0)L.splice(i,1);upsertMsg(Object.assign({threadId:id},m));}
    toast(isClaude(t)?'Sent · '+threadName(t):'Sent');render();}
  catch(e){const L=D.msgs[id]||[];const i=L.findIndex(x=>x.id===tmp.id);if(i>=0)L.splice(i,1);throw e;}}
V.chat=function(m){
  const TH=need(m,'threads','your chats',4);if(!TH){return;}use('crew');
  const desk=isDesk(),live=TH.filter(t=>!t.archived).sort((a,b)=>tWhen(b)-tWhen(a));
  if(!S.chat||(desk&&S.chat==='list'))S.chat=desk?((live.find(t=>!isClaude(t))||live[0]||{}).id||'new'):'list';
  const t=TH.find(x=>x.id===S.chat)||null,w=h('div','chatv');
  if(S.chat!=='list'&&S.chat!=='new'&&!t){w.dataset.open='list';w.append(chatList());m.append(w);toast('That chat is gone');S.chat=desk?'new':'list';return;}
  w.dataset.open=(!desk&&S.chat==='list')?'list':'thread';
  w.append(chatList());if(S.chat!=='list')w.append(chatThread(t,t?t.id:null));m.append(w);
  if(t&&D.msgs[t.id]&&!document.hidden)markRead(t);
};

/* ---------- tickets (n8n, cached by the server; limits: title 120, details 4000, comment 4000) ---------- */
function ticketLi(t){const st=tStatus(t),b=h('button','li');b.type='button';b.addEventListener('click',()=>go('tickets',{ticket:t.id}));
  const a=agentOfTicket(t),when=st==='done'?'Done '+hm(tm(t.updatedAt)):'Filed '+ago(tm(t.createdAt||t.created));
  const meta=[TYPE[t.type]||t.type||'Ticket',a?short(a.name):(t.agent||'No agent')].join(' · '),sl=h('span','s');   // alert first: the caution word leads
  if(st==='needs')ap(sl,h('span','cw',tWord(t)),' · '+meta);else sl.textContent=meta+' · '+tWord(t);
  ap(b,pip(tPip(st)),h('span','t',t.title||'(no title)'),h('span','m',when),h('span','go'),sl);
  if(S.ticket===t.id)b.setAttribute('aria-current','true');return b;}
const newDraft=a=>{S.draft={type:'bug',agent:a||'',title:'',details:''};S.bad=false;};
V.tickets=function(m){
  if(S.newT)return newTicket(m);
  const TK=need(m,'tickets','tickets',3);if(!TK)return;use('crew');
  if(S.ticket!=null)return ticketDetail(m,TK);
  const by={open:[],needs:[],progress:[],done:[]};TK.forEach(t=>by[tStatus(t)].push(t));
  m.append(ap(h('div','row sb'),h('h1','hero','Tickets'),key('New ticket','',()=>{newDraft();go('tickets',{newT:true});})));
  const seg=h('div','segc full wrap4');seg.setAttribute('role','group');seg.setAttribute('aria-label','Filter');
  [['open','Open'],['needs','Needs you'],['progress','In progress'],['done','Done']].forEach(([v,l])=>{const b=h('button',null);b.type='button';ap(b,l,h('span','n',by[v].length));b.setAttribute('aria-pressed',S.tf===v);b.addEventListener('click',()=>{S.tf=v;history.replaceState(null,'','#'+route());render();});seg.append(b);});
  m.append(seg);const list=by[S.tf].slice().sort((a,b)=>tm(b.updatedAt)-tm(a.updatedAt));
  if(list.length){m.append(ap(h('div','list'),...list.map(ticketLi)));return;}
  const e=h('div','empty');ap(e,h('p','h',S.tf==='open'?'Nothing open.':S.tf==='needs'?'Nothing needs you.':S.tf==='progress'?'Nothing in progress.':'Nothing done yet.'),h('p',null,'Tickets you file here, from Home Assistant or by asking Agent Hub land in this list. The agents reply in the comments.'));
  if(S.tf==='open'){m.querySelector('.row.sb').lastChild.remove();e.append(key('New ticket','lit',()=>{newDraft();go('tickets',{newT:true});}));}
  m.append(e);
  if(S.tf!=='done'&&by.done.length){const b=gap(h('div','stack'),'0');b.append(grouphd('Recently done'),ap(h('div','list'),...by.done.slice().sort((a,b)=>tm(b.updatedAt)-tm(a.updatedAt)).slice(0,3).map(ticketLi)));m.append(b);}
};
function ticketDetail(m,TK){
  const t=TK.find(x=>String(x.id)===String(S.ticket));if(!t){m.append(ap(h('div','empty'),h('p','h','That ticket is gone.'),h('p',null,'n8n no longer lists it.'),key('All tickets','',()=>go('tickets'))));return;}
  const st=tStatus(t),a=agentOfTicket(t);
  m.append(crumb('Tickets',()=>go('tickets'),'#'+t.id+' '+(t.title||'')));
  m.append(ap(h('div','stack'),h('span','plate',(TYPE[t.type]||t.type||'Ticket')+' · #'+t.id),h('h1','vt',t.title||'(no title)')));
  const kv=h('dl','kv'),c0=tm(t.createdAt||t.created);[['Agent',a?a.name:(t.agent||'None')],['From',t.source||'—'],['Filed',c0?hm(c0)+' · '+ago(c0):'—'],['Updated',hm(tm(t.updatedAt))]].forEach(([k,v])=>ap(kv,h('dt',null,k),h('dd',null,v)));
  const pn=ap(h('div','panel'),kv);if(t.details)pn.append(gap(ap(h('div','stack'),plate('Details'),h('p','md',t.details)),'4px'));if(t.resolution)pn.append(gap(ap(h('div','stack'),plate('Resolution'),h('p','md',t.resolution)),'4px'));
  m.append(pn);
  const seg=h('div','segc full');seg.setAttribute('role','group');seg.setAttribute('aria-label','Status');
  /* the Engineer owns needs_isaac / awaiting_approval: the control only sets Open / In progress / Done */
  [['open','Open','open'],['progress','In progress','in_progress'],['done','Done','closed']].forEach(([v,l,api])=>{const b=h('button',null,l);b.type='button';b.setAttribute('aria-pressed',st===v);
    b.addEventListener('click',()=>{if(st===v)return;const old=t.status;t.status=api;render();post(API.ticket(t.id),{status:api}).then(j=>{if(j&&(j.ticket||j.id))Object.assign(t,j.ticket||j);toast('Status · '+l);render();},e=>{t.status=old;failToast('Status not changed',e);render();});});seg.append(b);});
  const sw=st==='needs'?ap(h('div','row sb'),grouphd('Status'),tag('caut','▲ '+tWord(t),'caut-t')):grouphd('Status');
  m.append(gap(ap(h('div','stack'),sw,seg),'0'));
  const cs=comments(t),cv=h('div','convo');cv.style.padding='4px 0 0';
  if(!cs.length)cv.append(h('p','dim','No comments yet.'));
  cs.forEach(c=>{const at=hm(tm(c.at||c.createdAt||c.date));if(isAgentC(c)){cv.append(ap(h('div','a'),ap(h('span','who',c.author||'Agent'),h('span','num',at)),h('p','md',c.text)));}
    else{cv.append(h('div','u',c.text));const by_=h('span','num dim',(c.author||user().display||'You')+' · '+at);by_.style.cssText='justify-self:end;font-size:13px;margin-top:-12px';cv.append(by_);}});
  const s_=str(t.status).toLowerCase(),review=s_==='awaiting_approval',waiting=s_==='needs_isaac';
  S.approved=S.approved||{};S.changes=S.changes||{};if(!review){delete S.approved[t.id];delete S.changes[t.id];}
  else{const lc=cs[cs.length-1];if(lc&&!isAgentC(lc)&&str(lc.text).trim()==='Approved')S.approved[t.id]=true;}   // approved earlier: n8n hasn't moved it yet
  const pushC=v=>{const c=comments(t);c.push({at:new Date().toISOString(),author:user().display||user().name,text:v});t.comments=c;};
  const dk='tc:'+t.id,ta=h('textarea','inp');ta.rows=3;ta.maxLength=4000;ta.placeholder=review?'What should change?':waiting?'Your direction for the Engineer':'Add a comment for the agent';ta.setAttribute('aria-label','Comment');bindDraft(ta,dk);
  const asks=review&&!S.changes[t.id];   // Approve is the one action; Request changes opens the comment box
  const add=key(review?'Send':'Add comment',asks||S.approved[t.id]?'':'lit',()=>{const v=str(S.drafts[dk]).trim();if(!v){ta.focus();return;}add.disabled=true;
    post(API.ticket(t.id),{comment:v}).then(j=>{delete S.drafts[dk];delete S.changes[t.id];if(j&&(j.ticket||j.id))Object.assign(t,j.ticket||j);else pushC(v);toast((review?'Changes requested':'Comment added')+' · #'+t.id);render();},
      e=>{add.disabled=false;failToast(review?'Not sent':'Comment not added',e);});});
  const box=gap(ap(h('div','stack'),grouphd('Comments'),cv),'10px');
  if(review){
    if(S.approved[t.id])box.append(banner('info','■','Approved','Final step: approve the deploy in OpenCode.'));
    else if(asks){let busy=false;const ap_=key('Approve','lit',()=>{if(busy)return;busy=true;ap_.disabled=true;
        post(API.ticket(t.id),{approve:true}).then(()=>{S.approved[t.id]=true;pushC('Approved');toast('Approved · #'+t.id);render();},e=>{busy=false;ap_.disabled=false;failToast('Not approved',e);});});
      box.append(ap(h('div','keys'),ap_,key('Request changes','',()=>{S.changes[t.id]=true;render();setTimeout(()=>{const x=main.querySelector('[data-k="'+dk+'"]');if(x)x.focus();});})));}
    else ap(box,ta,ap(h('div','keys'),add,lnk('Cancel',()=>{delete S.changes[t.id];render();})));}
  else{if(waiting)box.append(plate('The Engineer is waiting for your direction','caut-t'));ap(box,ta,ap(h('div','keys'),add));}
  m.append(box);
  /* needs_isaac: the box is ready to type in, once per visit (not on live re-renders) */
  if(waiting&&S.focusT!==t.id){S.focusT=t.id;setTimeout(()=>{const x=main.querySelector('[data-k="'+dk+'"]');if(x&&document.activeElement!==x)x.focus({preventScroll:true});});}
  if(!waiting&&S.focusT===t.id)S.focusT=null;
}
/* Now: a ticket the Engineer handed back (title, agent, the last Engineer comment, Open ticket) */
function ticketNeed(t){const p=h('section','panel need'),a=agentOfTicket(t),lc=lastAgentC(t),since=tm(t.since||t.updatedAt);
  const tx=lc?str(lc.text).replace(/\s+/g,' ').trim():'';
  p.append(ap(h('div','row sb'),plate('Needs you'),h('span','num dim',since?'Since '+hm(since):'')),h('h2','vt',t.title||'(no title)'),
    h('p','dim',['#'+t.id,a?short(a.name):(t.agent||'No agent'),tWord(t)].join(' · ')));
  if(tx)p.append(ap(h('div','well'),h('span','plate',(lc.author||'Engineer')+' · '+hm(tm(lc.at))),h('p',null,tx.length>160?tx.slice(0,157).trimEnd()+'…':tx)));
  p.append(ap(h('div','keys'),key('Open ticket','',()=>go('tickets',{ticket:t.id}))));return p;}
function newTicket(m){
  use('crew');const d=S.draft;m.append(crumb('Tickets',()=>go('tickets'),'New ticket'),h('h1','vt','New ticket'));
  const f=h('form','panel');f.noValidate=true;f.style.gap='16px';
  const seg=h('div','segc wrap4');seg.setAttribute('role','group');seg.setAttribute('aria-labelledby','tt-l');
  [['bug','Bug'],['feature','Feature'],['tool_feedback','Tool feedback'],['question','Question']].forEach(([v,l])=>{const b=h('button',null,l);b.type='button';b.setAttribute('aria-pressed',d.type===v);b.addEventListener('click',()=>{d.type=v;seg.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b));});seg.append(b);});
  const f1=ap(h('div','fld'),h('span','lbl','Type'),seg);f1.firstChild.id='tt-l';
  const sel=h('select','inp');sel.id='tk-ag';sel.dataset.k='tk-ag';const o0=h('option',null,'No agent');o0.value='';sel.append(o0);
  AGENTS.filter(a=>!isSys(a)).concat(AGENTS.filter(isSys)).forEach(a=>{const o=h('option',null,a.name);o.value=a.id;if(d.agent===a.id||d.agent===a.name)o.selected=true;sel.append(o);});sel.addEventListener('change',()=>{d.agent=sel.value;});
  const f2=ap(h('div','fld'),h('label',null,'Agent'),sel,h('span','help','Who should pick this up. Leave empty if you\'re not sure.'));f2.firstChild.htmlFor='tk-ag';
  const ti=h('input','inp');ti.type='text';ti.id='tk-ti';ti.dataset.k='tk-ti';ti.maxLength=120;ti.value=d.title;ti.placeholder='What happened, in a line';ti.addEventListener('input',()=>{d.title=ti.value;});
  const f3=ap(h('div','fld'+(S.bad?' bad':'')),h('label',null,'Title'),ti);f3.firstChild.htmlFor='tk-ti';if(S.bad){const ms=h('span','msg','A title is needed.');ms.id='tk-ti-m';ti.setAttribute('aria-describedby','tk-ti-m');ti.setAttribute('aria-invalid','true');f3.append(ms);}
  const de=h('textarea','inp');de.id='tk-de';de.dataset.k='tk-de';de.rows=4;de.maxLength=4000;de.value=d.details;de.placeholder='Steps, what you expected, anything the agent should know';de.addEventListener('input',()=>{d.details=de.value;});
  const f4=ap(h('div','fld'),h('label',null,'Details'),de,h('span','help','Optional. Up to 4000 characters.'));f4.firstChild.htmlFor='tk-de';
  let busy=false;
  const fileIt=()=>{if(busy)return;if(!d.title.trim()){S.bad=true;render();setTimeout(()=>{const x=$('tk-ti');if(x)x.focus();});return;}S.bad=false;busy=true;
    const ag=AGENTS.find(a=>a.id===d.agent);
    post(API.tickets,{type:d.type,agentId:ag?ag.id:undefined,agent:ag?ag.name:undefined,title:d.title.trim().slice(0,120),details:d.details.trim().slice(0,4000)||null}).then(j=>{const t=j&&(j.ticket||(j.result&&j.result.id!=null?Object.assign({title:d.title.trim(),type:d.type,status:'open',createdAt:now(),updatedAt:now(),agentId:ag?ag.id:null,agent:ag?ag.name:null,details:d.details.trim()||null},j.result):null));busy=false;
      if(t&&t.id!=null){if(D.tickets){const i=D.tickets.findIndex(x=>x.id===t.id);if(i>=0)D.tickets[i]=t;else D.tickets.unshift(t);}toast('Ticket filed · #'+t.id);newDraft();go('tickets',{ticket:t.id});}
      else{toast('Ticket filed');newDraft();load('tickets',true);go('tickets');}},e=>{busy=false;failToast('Not filed',e);});};
  f.addEventListener('submit',e=>{e.preventDefault();fileIt();});
  ap(f,f1,f2,f3,f4,ap(h('div','keys'),key('File ticket','lit',fileIt),lnk('Cancel',()=>{S.bad=false;go('tickets');})));m.append(f);
}

/* ---------- tasks ---------- */
const agentOfWf=wf=>AGENTS.find(a=>a.workflowId===wf||a.id==='n8n:'+wf);
V.tasks=function(m){
  m.append(ap(h('div','stack'),h('h1','hero','Tasks'),h('p','lead','Claude plans, n8n runs and the nightly library check.')));
  if(!need(m,'crew','the crew',2))return;const T=use('tasks'),LIB=use('library');
  const pl=SESS.filter(s=>s.plan||arr(s.tasks).length),live=pl.filter(s=>!(s.plan&&s.plan.finished)||now()-tm(s.plan.updatedAt)<10*MIN),old=pl.filter(s=>!live.includes(s));
  const pb=h('div','panel');pb.style.gap='14px';live.forEach(s=>pb.append(planBlock(s)));if(!live.length)pb.append(h('p','dim','No plans running.'));
  if(old.length){const k='oldplans',open=!!S.open[k],b=lnk(open?'Fold older plans':old.length+' older',()=>{S.open[k]=!open;render();});b.setAttribute('aria-expanded',open);if(open)old.forEach(s=>pb.append(planBlock(s)));b.style.justifySelf='start';b.style.marginLeft='-6px';pb.append(b);}
  const A=gap(ap(h('div','stack'),grouphd('Claude plans'),pb),'0');
  const B=gap(ap(h('div','stack'),grouphd('n8n runs')),'0');
  if(!T)B.append(ERR.tasks?errState('the runs',ERR.tasks):skel(3));
  else{const RN=arr(T.running),wf=arr(T.workflows).filter(a=>a.last_run&&a.group!=='system').sort((a,b)=>tm(b.last_run)-tm(a.last_run));
    const rows=RN.map(r=>{const a=agentOfWf(r.workflowId);return runLi({id:r.id,status:'running',mode:r.mode,startedAt:r.startedAt},short(r.name||(a&&a.name)||'n8n'),a?()=>go('crew',{agent:a.id,crew:'agents'}):null);})
      .concat(wf.map(a=>runLi({id:'',status:a.last_status==='success'?'success':a.last_status||'',startedAt:a.last_run},short(a.name),()=>go('crew',{agent:a.id,crew:'agents'}))));
    if(!rows.length)B.append(h('p','dim','No n8n runs reported.'));
    else{B.append(ap(h('div','list'),...rows));const x=h('p','dim','The latest run of each workflow. Open one for its runs.');x.style.cssText='font-size:14px;padding-top:8px';B.append(x);}}
  const C=gap(ap(h('div','stack'),grouphd('Nightly')),'0'),lib=libAgent();
  if(!LIB)C.append(ERR.library?h('p','dim','The library report didn\'t load.'):skel(1));
  else{const nb=h('button','li');nb.type='button';nb.addEventListener('click',()=>go('library'));const fin=tm(LIB.finished);
    ap(nb,pip(arr(LIB.errors).length?'warn':'run'),h('span','t','Library check (Jellyfin)'),h('span','m',fin?'Done '+hm(fin):'—'),h('span','go'),h('span','s','Every night · '+libTotal()+' findings in the last report'+(lib&&lib.version?' · v'+lib.version:'')));
    C.append(ap(h('div','list'),nb));}
  m.append(isDesk()?ap(h('div','cols two'),ap(h('div','stack'),A,C),B):ap(h('div','stack'),A,B,C));
};

/* ---------- library: findings paged here (the report has no paging), apply as a job ---------- */
const PAGE=25;
const SEV=['high','medium','low'];
function applyJob(){const js=Object.values(D.jobs).filter(j=>(j.kind==='library.apply'||/library/.test(str(j.kind)))&&['queued','running','started','pending'].includes(j.state));return js[js.length-1]||null;}
function savedIds(id){try{const x=JSON.parse(sessionStorage.getItem('bridge-apply-'+id)||'null');return Array.isArray(x)?x:null;}catch(e){return null;}}
function jobIds(j){const d=j.detail||{};const x=Array.isArray(d.ids)?d.ids:(j.id&&savedIds(j.id))||(Array.isArray(j.ids)?j.ids:null)||S.applyIds;return Array.isArray(x)?x:d.ids==='all'?FINDS().filter(f=>!f.applied).map(f=>f.id):[];}
function jobChanged(j){
  if(j.state==='done'||j.state==='ok'||j.state==='finished'){const n=jobIds(j).length||(typeof (j.detail||{}).ids==='number'?j.detail.ids:0);toast('Applied · '+n+(n===1?' fix':' fixes'));S.applyIds=null;load('library',true);}
  else if(['error','failed','refused','timeout'].includes(j.state)){const d=j.detail||{},e=str(d.error||d.message||j.error||(j.state==='timeout'?'n8n didn\'t answer in 15 min':'Apply failed'));const ids=jobIds(j);S.applyIds=null;
    S.libErr={text:e,quiet:d.canForce===true||(d.canForce!==false&&(d.quietHours===true||d.code==='quiet_hours'||/quiet hours/i.test(e))),ids};toast(e);load('library',true);}}
function precheckLines(){
  const lib=libAgent(),L1={t:'Library agent online',v:lib&&['run','busy'].includes(aState(lib).c)?'go':'nogo'};
  return get(API.precheck).then(j=>{const p=(j&&j.precheck)||j||{};const L=[L1];
    if(p.playing==null)L.push({t:'Couldn\'t check Jellyfin',v:'caut',word:'UNKNOWN'});
    else if(p.playing)L.push({t:(p.sessions||arr(p.titles).length)+' playing'+(arr(p.titles).length?': '+arr(p.titles).join(', '):''),v:'caut',word:'SKIP',note:'Apply skips what\'s playing.'});
    else L.push({t:'Nothing playing on Jellyfin',v:'go'});
    if(p.busy)L.push({t:'Library agent is busy',v:'nogo',note:'Wait for its current run to finish.'});
    L.push({t:'Old files kept for undo',v:p.undo===false?'nogo':'go',note:p.undo_note||null});return L;},
    ()=>[L1,{t:'Couldn\'t check Jellyfin',v:'caut',word:'UNKNOWN'},{t:'Old files kept for undo',v:'caut',word:'UNKNOWN'}]);}
function startApply(ids,force){
  S.libErr=null;S.applyIds=ids.slice();const n=ids.length;
  post(API.apply,Object.assign({ids,confirm:true},force?{force:true,forceConfirm:true}:{})).then(j=>{const job=j&&(j.job||j);
    if(job&&job.id){try{sessionStorage.setItem('bridge-apply-'+job.id,JSON.stringify(ids));}catch(e){}D.jobs[job.id]=Object.assign({kind:'library.apply',state:'running'},D.jobs[job.id],job);if(['done','error','failed','refused'].includes(job.state))jobChanged(D.jobs[job.id]);}
    toast('Applying · '+n+(n===1?' fix':' fixes'));render();},
    e=>{S.applyIds=null;if(e.code==='quiet_hours'||/quiet hours/i.test(e.message)){S.libErr={text:e.message,quiet:true,ids};}else S.libErr={text:e.message,quiet:false,ids};toast(e.message);render();});
  render();}
function forceDlg(ids,back){guarded({title:'Apply in quiet hours',q:'It\'s evening quiet hours (17:00–01:00). Applying now swaps '+(ids.length===1?'a trailer':ids.length+' trailers')+' while someone may be watching.',
  checks:precheckLines,pending:['Library agent online','Checking Jellyfin…','Old files kept for undo'],act:ids.length===1?'Apply anyway':'Apply '+ids.length+' anyway',cancel:'Wait for 01:00',back,go:()=>startApply(ids,true)});}
function applyFlow(ids,back){if(!quietHours())return startApply(ids,false);forceDlg(ids,back);}
V.library=function(m){
  const LIB=need(m,'library','the library report',4);if(!LIB)return;use('crew');
  const FIND=FINDS(),wait=FIND.filter(f=>!f.applied),done=FIND.filter(f=>f.applied),safe=wait.filter(f=>f.severity==='low'&&f.fix==='replace_trailer'),q=quietHours(),J=applyJob(),A=J||(S.applyIds?{detail:{ids:S.applyIds}}:null);
  const fin=tm(LIB.finished),dt=tm(LIB.date)||fin,errs=arr(LIB.errors);
  m.append(ap(h('div','stack'),h('h1','hero',wait.length?wait.length+(wait.length===1?' fix waiting.':' fixes waiting.'):'All fixes applied.'),h('p','lead',[dt?'Report for '+dmon(dt):null,fin?'finished '+hm(fin):null,libTotal()+' findings',errs.length?null:'no errors'].filter(Boolean).join(' · '))));
  if(q)m.append(banner('info','○','Evening quiet hours until 01:00','Fixes wait so nothing changes while people watch. Apply anyway runs a check first.'));
  if(S.libErr&&!A){const E=S.libErr;
    if(E.quiet&&E.ids&&E.ids.length){const k=key('Apply anyway','guard');k.addEventListener('click',()=>forceDlg(E.ids,k));m.append(banner('caut','▲','n8n held the fixes',E.text,ap(h('div','keys'),k)));}
    else{m.append(banner('warn','◆','Apply failed',E.text,ap(h('div','keys'),E.ids&&E.ids.length?key('Try again','fix',()=>applyFlow(E.ids,null)):null,lnk('Dismiss',()=>{S.libErr=null;render();}))));}}
  const ids=A?jobIds(A):[],idSet=new Set(ids);
  if(A){const d=(J&&J.detail)||{},tot=d.total||ids.length||(typeof d.ids==="number"?d.ids:0)||1,dn=Math.min(tot,d.done!=null?d.done:FIND.filter(f=>idSet.has(f.id)&&f.applied).length),pct=Math.round(100*dn/tot);
    const pg=h('div','prog panel'),bar=h('div','bar20');for(let i=0;i<20;i++){const on=i<Math.floor(pct/5);bar.append(h('s',on?'on':i===Math.floor(pct/5)?'edge':null));}
    const left=tot-dn;ap(pg,ap(h('div','row'),ap(h('span',null),tag('busy','Applying…')),h('span','num',pct+'%')),bar,ap(h('div','row'),h('span','dim',dn+' of '+tot+' · about 25 s each'),h('span','num dim','Done about '+hm(now()+left*25000))));pg.querySelector('.row .num').style.color='var(--dat)';m.append(pg);}
  else if(safe.length&&!(S.libErr&&S.libErr.quiet)){const k=key('Apply all safe ('+safe.length+')',q?'guard':(S.libErr?'':'lit'));k.addEventListener('click',()=>applyFlow(safe.map(f=>f.id),k));
    const sb=gap(ap(h('div','stack'),ap(h('div','keys'),k),h('p','dim','Safe = low-severity trailer swaps. The old trailer is kept, so each one can be undone.')),'6px');sb.lastChild.style.fontSize='14px';m.append(sb);}
  const cur=A?FIND.find(f=>idSet.has(f.id)&&!f.applied):null;
  const groups=SEV.concat([...new Set(FIND.map(f=>f.severity))].filter(s=>!SEV.includes(s)));
  const F=gap(h('div','stack'),'0');let any=false;
  groups.forEach(sv=>{const all=FIND.filter(f=>(f.severity||'other')===sv);if(!all.length)return;any=true;
    const w=all.filter(f=>!f.applied),dn=all.filter(f=>f.applied),pk='lib:'+sv,shown=S.page[pk]||PAGE,L=h('div','list');
    w.slice(0,shown).forEach(f=>{const r=h('div','li find'),busy=idSet.has(f.id);ap(r,h('span','t',f.title),h('span','s wrap',f.detail));
      if(busy){r.append(f===cur?tag('busy','Applying…'):tag('','Queued'));r.lastChild.style.cssText='grid-row:1 / span 3;grid-column:2';}
      else{const k=key('Apply',q?'guard':'');k.setAttribute('aria-label','Apply: '+f.title);k.addEventListener('click',()=>applyFlow([f.id],k));if(A)k.disabled=true;r.append(k);}L.append(r);});
    if(w.length>shown){const mb=h('button','fold',(w.length-shown)+' more waiting · show '+Math.min(PAGE,w.length-shown));mb.type='button';mb.addEventListener('click',()=>{S.page[pk]=shown+PAGE;render();});L.append(mb);}
    if(dn.length){const ko='applied:'+sv,open=!!S.open[ko],fb=h('button','fold');fb.type='button';ap(fb,pip('done'),h('span',null,dn.length+' applied'));fb.setAttribute('aria-expanded',open);fb.addEventListener('click',()=>{S.open[ko]=!open;render();});L.append(fb);
      if(open){const ak='libdone:'+sv,ds=S.page[ak]||PAGE;dn.slice(0,ds).forEach(f=>{const r=h('div','li find');ap(r,h('span','t',f.title),h('span','s',f.at?'Applied '+hm(f.at):'Applied'));r.append(h('span'));L.append(r);});
        if(dn.length>ds){const mb=h('button','fold','Show '+Math.min(PAGE,dn.length-ds)+' more applied');mb.type='button';mb.addEventListener('click',()=>{S.page[ak]=ds+PAGE;render();});L.append(mb);}}}
    const kinds=[...new Set(all.map(f=>f.kind))],word=kinds.length===1&&kinds[0]==='trailer'?(all.length===1?' trailer':' trailers'):(all.length===1?' finding':' findings');
    F.append(grouphd(sv.replace(/^./,c=>c.toUpperCase())+' · '+all.length+word),L);});
  if(!any)F.append(ap(h('div','empty'),h('p','h','No findings.'),h('p',null,'The last nightly check found nothing to fix.')));
  if(libTotal()>FIND.length){const x=h('p','dim','The report lists '+FIND.length+' of '+libTotal()+' findings.');x.style.cssText='font-size:13px;padding-top:8px';F.append(x);}
  const N=gap(ap(h('div','stack'),grouphd('Notes'),arr(LIB.notes).length?ap(h('div','list'),...arr(LIB.notes).map(n=>ap(h('div','li'),h('span'),h('span','s wrap',n)))):h('p','dim','No notes.')),'0');
  N.querySelectorAll('.li .s').forEach(s=>{s.style.cssText='grid-column:2;color:var(--tx);font-size:15px;margin:0';});
  const E=gap(ap(h('div','stack'),grouphd('Errors'),h('p','dim',errs.length?errs.map(str).join(' · '):'No errors.')),'0');
  m.append(isDesk()?ap(h('div','cols two'),F,ap(h('div','stack'),N,E)):ap(h('div','stack'),F,N,E));
};

/* ---------- inbox (agent digests and alerts; the server keeps rows past n8n's last 50) ---------- */
const SRC={downloads:'Downloads',agent:'Agent Hub',security:'Security',library:'Library'};
let inboxReadAt=0;
V.inbox=function(m){
  m.append(ap(h('div','stack'),h('h1','hero','Inbox'),h('p','lead','Everything the crew sent your phone, kept here.')));
  const IB=need(m,'inbox','the inbox',4);if(!IB)return;
  if(now()-inboxReadAt>60000){inboxReadAt=now();post(API.read,{scope:'inbox',at:now()}).then(()=>{if(D.state&&D.state.unread){D.state.unread.inbox=0;renderNav();}},()=>{});}
  if(!IB.length){m.append(ap(h('div','empty'),h('p','h','Nothing yet.'),h('p',null,'Digests and alerts the agents send your phone land here too.')));return;}
  const by={},order=[];IB.slice().sort((a,b)=>tm(b.createdAt)-tm(a.createdAt)).forEach(n=>{const d=dayName(tm(n.createdAt));if(!by[d]){by[d]=[];order.push(d);}by[d].push(n);});
  order.forEach(d=>{const l=h('div','list');by[d].forEach(n=>{const open=S.inbox===n.id,b=h('div','li ib');b.tabIndex=0;b.setAttribute('role','button');b.setAttribute('aria-expanded',open);
    const tg=()=>{S.inbox=open?null:n.id;render();};b.addEventListener('click',e=>{if(e.target.closest('.lnk'))return;tg();});b.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&!e.target.closest('.lnk')){e.preventDefault();tg();}});
    ap(b,h('span','tm',hm(tm(n.createdAt))),h('span','t',n.title||'(no title)'),h('span','m',SRC[n.source]||n.source||''),h('span','s',str(n.message).replace(/\s+/g,' ')));
    if(open){const f=h('div','full');f.append(h('p','md',n.message));const ks=h('div','keys');
      if(/trailer|library/i.test(n.message+' '+n.title)||n.source==='library')ks.append(lnk('Open Library',()=>go('library')));
      if(n.source==='security'){const sa=AGENTS.find(a=>a.id==='svc:netaudit');if(sa)ks.append(lnk('Open '+short(sa.name),()=>go('crew',{agent:sa.id,crew:'agents'})));}
      if(ks.childNodes.length){ks.style.marginLeft='-6px';f.append(ks);}b.append(f);}
    l.append(b);});m.append(gap(ap(h('div','stack'),grouphd(d),l),'0'));});
  if(D.inboxMore){const b=lnk('Older messages',()=>{const old=IB.reduce((a,x)=>Math.min(a,+x.id),Infinity);get(API.inbox(old)).then(j=>{const L=PICK.inbox(j);const seen=new Set(D.inbox.map(x=>x.id));D.inbox=D.inbox.concat(L.filter(x=>!seen.has(x.id)));render();},e=>failToast('Couldn\'t load older',e));});b.style.justifySelf='start';m.append(b);}
};

/* ---------- more (phone) + settings ---------- */
const THEMES=[['ha','Follow HA'],['night','Night'],['day','Day']];
V.more=function(m){
  if(isDesk()){S.view='now';history.replaceState(null,'','#now');return V.now(m);}
  const c=counts(),l=h('div','list'),row=(v,t,s)=>{const b=h('button','li');b.type='button';ap(b,icon(v),h('span','t',t),h('span','m',''),h('span','go'),h('span','s',s));b.addEventListener('click',()=>go(v));l.append(b);};
  const pl=SESS.filter(s=>s.plan&&!s.plan.finished).length,T=D.tasks,r0=T&&arr(T.workflows).filter(a=>a.last_run).map(a=>({startedAt:a.last_run})).sort((a,b)=>tm(b.startedAt)-tm(a.startedAt))[0];
  row('tasks','Tasks',(D.crew?pl+' plan'+(pl===1?'':'s')+' running':'Claude plans')+(r0?' · last n8n run '+hm(tm(r0.startedAt)):' · n8n runs'));
  row('library','Library',D.library||c.library?(c.library?c.library+' fixes waiting':'All fixes applied'):'Nightly report and fixes');
  const IB=D.inbox,today=IB?IB.filter(n=>dayKey(tm(n.createdAt))===dayKey(now())):null;
  row('inbox','Inbox',IB?(today.length+' today'+(IB.length?' · latest '+hm(Math.max(...IB.map(n=>tm(n.createdAt)||0))):'')):(c.inbox?c.inbox+' new':'Digests and alerts'));
  row('settings','Settings','Theme · '+THEMES.find(t=>t[0]===S.theme)[1]+' · chats kept 90 days');
  const v=D.state&&D.state.version;ap(m,h('h1','hero','More'),l,h('p','dim','The Bridge'+(v?' '+v:'')));m.lastChild.style.fontSize='13px';
};
V.settings=function(m){
  const seg=h('div','segc full');seg.setAttribute('role','group');seg.setAttribute('aria-label','Theme');
  THEMES.forEach(([v,l])=>{const b=h('button',null,l);b.type='button';b.setAttribute('aria-pressed',S.theme===v);b.addEventListener('click',()=>{if(S.theme===v)return;const old=S.theme;S.theme=v;render();
    post(API.settings,{theme:TH_OUT[v]}).then(j=>{if(D.state){D.state.settings=Object.assign({},D.state.settings,(j&&(j.settings||j))||{theme:TH_OUT[v]});}},e=>{S.theme=old;failToast('Theme not saved',e);render();});});seg.append(b);});
  const sc=scheme()==='day'?'Day':'Night';
  const th=gap(ap(h('div','stack'),grouphd('Theme'),seg,h('p','dim',S.theme==='ha'?(haSource==='ha'?'Follows Home Assistant\'s theme · now '+sc+'.':'Follows this device (Home Assistant\'s theme isn\'t readable here) · now '+sc+'.'):'Stays '+sc+', whatever Home Assistant uses.')),'8px');
  const ch=gap(ap(h('div','stack'),grouphd('Chat'),h('p',null,'Chat history: kept 90 days')),'8px');
  const w=ap(h('div','stack'),h('h1','hero','Settings'),th,ch);w.style.maxWidth='520px';m.append(w);
};

/* ---------- render (drafts and focus survive the live re-renders) ---------- */
function bindDraft(el,k){el.dataset.k=k;if(S.drafts[k]!=null)el.value=S.drafts[k];el.addEventListener('input',()=>{S.drafts[k]=el.value;});}
let lastDesk=null,lastView=null,lastChat=null,lastMark=null,lampSeen=false;
try{lampSeen=sessionStorage.getItem('bridge-lamp')==='1';}catch(e){}
function render(nav){
  if(rq){cancelAnimationFrame(rq);rq=0;}
  derive();paintScheme();lastDesk=isDesk();
  const ae=document.activeElement,fk=ae&&ae.dataset&&ae.dataset.k,sel=fk&&typeof ae.selectionStart==='number'?[ae.selectionStart,ae.selectionEnd]:null;
  /* live updates must not move the page: the old height holds while main is rebuilt (layout reads in the views would
     otherwise clamp the scroll to the top), then the position comes back; chat pins to the bottom when it was there */
  const se=scroller(),sy=se.scrollTop,sh=se.scrollHeight,atBot=nearBottom(),mk0=lastMark,same=!nav&&S.view===lastView&&(S.view!=='chat'||S.chat===lastChat);
  main.style.minHeight=main.offsetHeight+'px';
  renderBand();renderNav();main.textContent='';
  const bn=S.view==='settings'||S.view==='more'?[]:setupBanners();bn.forEach(b=>main.append(b));
  (V[S.view]||V.now)(main);
  if(bn.length){const cv=main.querySelector('.chatv');if(cv)cv.style.marginTop='0';}
  if(fk){const el=main.querySelector('[data-k="'+(window.CSS&&CSS.escape?CSS.escape(fk):fk)+'"]');if(el&&el!==document.activeElement){el.focus({preventScroll:true});if(sel&&el.setSelectionRange)try{el.setSelectionRange(sel[0],sel[1]);}catch(e){}}}
  main.style.minHeight='';
  if(nav||S.view!==lastView){main.classList.remove('fade');void main.offsetWidth;main.classList.add('fade');}
  if(!same)S.newReply=null;
  lastView=S.view;lastChat=S.chat;
  root.style.setProperty('--tabh',$('tabbar').offsetHeight+'px');
  const mk1=threadMark();lastMark=mk1;   // what the screen shows now (the data may change before the next render)
  if(same){const grew=mk0&&mk1&&mk0.id===mk1.id&&(mk0.n!==mk1.n||mk0.last!==mk1.last||mk0.st!==mk1.st);
    if(grew&&mk0.n>0&&mk1.first!==mk0.first&&mk1.last===mk0.last)se.scrollTop=sy+(se.scrollHeight-sh);   // earlier messages loaded above
    else if(grew&&atBot){toBottom();S.newReply=null;}
    else{if(se.scrollTop!==sy)se.scrollTop=sy;if(grew&&mk1.last!==mk0.last&&mk1.role!=='user'&&mk0.n>0)S.newReply=mk1.id;}}
  if(S.stick&&S.view==='chat'&&main.querySelector('.convo')&&!main.querySelector('.convo .skel')){S.stick=false;S.newReply=null;toBottom();requestAnimationFrame(toBottom);}
  placeChip();convAfterRender();
  if(!lampSeen&&D.crew&&D.state){lampSeen=true;try{sessionStorage.setItem('bridge-lamp','1');}catch(e){}requestAnimationFrame(lampTest);}
}
function lampTest(){if(REDUCE)return;const cells=[...root.querySelectorAll('.ann')];const cols=isDesk()?4:3;let i=0;
  root.querySelectorAll('.board .ann').forEach(c=>{c.style.setProperty('--d',((i++%cols)*28*PACE())+'ms');});
  root.classList.add('testing');setTimeout(()=>{root.classList.remove('testing');cells.forEach(c=>c.style.removeProperty('--d'));},760*PACE());}
new ResizeObserver(()=>{if(isDesk()!==lastDesk)render();}).observe(frame);
setInterval(()=>{const c=$('clock');if(c)c.textContent=hm(now());const u=$('updated'),at=tm((health().n8n||{}).at);if(u&&at)u.textContent='Updated '+ago(at);
  const oe=$('oc-el');if(oe&&S.oc0){const el=Math.max(0,Math.round((now()-S.oc0)/1000));oe.textContent=Math.floor(el/60)+':'+p2(el%60);}
  const t=$('think-el');if(t&&S.think0){const el=Math.max(0,Math.round((now()-S.think0)/1000));t.textContent=Math.floor(el/60)+':'+p2(el%60);}
  if(S.sse==='down'&&S.sseDownAt&&now()-S.sseDownAt>3000&&!document.querySelector('.band .num.dim:not(#updated)'))renderBand();},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&S.view==='chat')schedule();if(!document.hidden)ocPoll(true);});

/* ---------- OpenCode agents (docs/BRIDGE.md §14): read through n8n, held in memory only; open pages poll while visible ---------- */
const ocTitle=s=>str(s&&s.title).replace(/^Bridge:\s*/,'')||'Untitled chat';   // Bridge-started chats are titled "Bridge: <text>"
const ocUrl=()=>{const u=D.state&&D.state.opencodeUrl;return typeof u==='string'&&/^https?:\/\//i.test(u)?u:null;};   // the add-on's opencode_url option
const callOf=a=>a.short_name||short(a.name);   // how sentences name it ("the Engineer")
const capF=s=>s.charAt(0).toUpperCase()+s.slice(1);
const ORIGIN={bridge:'From the Bridge',ticket:'From a ticket',opencode:'Started in OpenCode'};
const originW=s=>s?(s.origin==='ticket'&&s.ticketId?'From ticket #'+s.ticketId:ORIGIN[s.origin]||null):null;
const SPICK=j=>arr(j&&j.sessions),TPICK=j=>(j&&j.session)||null;
const ocSessions=a=>useSub(D.ocs,a.id,API.ocSessions(a.id),SPICK);
const ocSessionOf=sid=>useSub(D.oct,sid,API.ocSession(sid),TPICK);
/* refresh in place: the old copy stays on screen until the new one lands */
function ocFetch(map,id,url,pick){if(LOADING[url])return;LOADING[url]=true;
  get(url).then(j=>{map[id]=pick(j);delete ERR[url];},e=>{if(e.status===404)delete map[id];ERR[url]=e;}).finally(()=>{LOADING[url]=false;schedule();});}
const ocBusySince=a=>{if(a.busySince)return tm(a.busySince);const L=D.ocs[a.id],b=L&&L.find(s=>s.busy);return b?tm(b.busySince||b.updated):null;};
/* one Bridge-started chat per agent may run (n8n answers 409 agent_busy otherwise) */
function bridgeRun(a){const L=D.ocs[a.id],b=L?L.find(s=>s.origin==='bridge'&&s.busy):null;if(b)return b;const x=S.ocBusy;return x&&x.a===a.id&&x.s&&now()-x.at<30000?{id:x.s}:null;}
function ocWord(a){const st=aState(a);if(st.c==='busy'){const t=ocBusySince(a);return'Working'+(t?' · '+span(now()-t):'');}if(st.c==='caut')return'Needs your approval in OpenCode';return st.w;}
const ocNeeds=()=>AGENTS.filter(a=>isOC(a)&&a.needsApproval&&a.state!=='down');
function ocNeed(a){const L=ocSessions(a),s=arr(L).filter(x=>x.needsApproval).sort((x,y)=>(tm(x.busySince)||0)-(tm(y.busySince)||0))[0],p=h('section','panel need'),t=s&&tm(s.busySince);
  ap(p,ap(h('div','row sb'),plate('Needs you'),t?h('span','num dim','Since '+whenShort(t)):null),h('h2','vt',a.name),
    h('p',null,(s?'“'+ocTitle(s)+'” is':capF(callOf(a))+' is')+' waiting for you to allow a step in OpenCode.'),
    ap(h('div','keys'),key('Open the chat','',()=>go('crew',s?{oc:{a:a.id,s:s.id}}:{agent:a.id,crew:'agents'})),openOC('')));return p;}
function ocChats(){const out=[];AGENTS.filter(isOC).forEach(a=>arr(ocSessions(a)).filter(s=>s.origin==='bridge').forEach(s=>out.push({a,s,w:tm(s.updated)||tm(s.busySince)||0})));return out;}
function ocTalk(a){const b=bridgeRun(a);go('crew',{oc:{a:a.id,s:b?b.id:'new'}});}
function openOC(cls,label){const u=ocUrl();if(!u)return null;const k=h('a','key ext'+(cls?' '+cls:''),label||'Open OpenCode');k.href=u;k.target='_blank';k.rel='noopener noreferrer';return k;}
function scopeEl(a){const s=h('div','scope'+(a.role==='home'?' ha':''));
  s.append(ap(h('p','ok'),h('span','sym','●'),ap(h('span'),h('b',null,'From here: '),'talk, and work in its sandbox.')));
  if(a.role==='home')s.append(ap(h('p'),h('span','sym ha-b','▲'),ap(h('span'),h('b','ha-b','Every change to Home Assistant asks for approval in OpenCode.'))));
  s.append(ap(h('p'),h('span','sym','○'),h('span',null,'Going live asks for your approval in OpenCode. The Bridge never answers approvals.')));
  return s;}
/* approvals are answered in OpenCode only: a link when opencode_url is set, words when it isn't */
function approvalBanner(a,txt,what){const k=openOC('lit'),b=banner('caut','▲','Needs your approval in OpenCode',txt,k?ap(h('div','keys'),k):h('span'));
  if(what)b.children[1].append(h('p','apx',what));return b;}
const whenShort=ms=>dayKey(ms)===dayKey(now())?hm(ms):dayName(ms)==='Yesterday'?'Yesterday':dmon(ms);
function sessLi(a,s,cur){const b=h('button','li');b.type='button';b.addEventListener('click',()=>go('crew',{oc:{a:a.id,s:s.id}}));const t=tm(s.updated);
  const sub=h('span','s');if(s.needsApproval)sub.append(h('span','cw','Needs your approval'),' · ');sub.append([originW(s),s.busy&&!s.needsApproval?'Working · '+span(now()-(tm(s.busySince||s.updated)||now())):null].filter(Boolean).join(' · '));
  ap(b,pip(s.needsApproval?'caut':s.busy?'busy':'done'),h('span','t',ocTitle(s)),h('span','m',t?whenShort(t):'—'),h('span','go'),sub);if(cur===s.id)b.setAttribute('aria-current','true');return b;}
function sessList(a,cur,max){const L=ocSessions(a),box=gap(h('div','stack'),'0'),e=ERR[API.ocSessions(a.id)];box.append(grouphd('Sessions'));
  if(L===undefined){box.append(e?(e.status===404?h('p','dim','OpenCode doesn\'t know this agent.'):errState('the sessions',e)):skel(3));return box;}
  if(!L.length){box.append(h('p','dim','No sessions yet.'));return box;}
  box.append(ap(h('div','list'),...L.slice(0,max||8).map(s=>sessLi(a,s,cur))));
  const f=h('p','dim');f.style.cssText='font-size:13px;padding-top:6px';f.append('Newest first · from OpenCode on the NAS · checked every 30 s');box.append(f);return box;}
function busyLimit(a,run,lit){return ap(h('div','limit'),ap(h('p'),h('b',null,capF(callOf(a))+' is busy with another Bridge chat'),' · wait or continue that one.'),
  ap(h('div','keys'),key('Continue that chat',lit?'lit':'',()=>go('crew',{oc:{a:a.id,s:run.id}}))));}
/* the OpenCode agent page */
function ocAgent(m,a){
  const st=aState(a),desk=isDesk(),L=ocSessions(a),run=bridgeRun(a);
  m.append(crumb('Crew',()=>go('crew',{crew:'agents'}),a.name));
  m.append(ap(h('div','stack'),ap(h('div','row sb'),h('h1','vt',a.name),tag(pipOf(st.c),st.c==='busy'?ocWord(a):st.w,st.c==='caut'?'caut-t':'')),
    h('p','lead','OpenCode agent · '+(ROLE[a.role]||'Other'))));
  const appr=arr(L).find(s=>s.needsApproval);
  if(appr||a.needsApproval)m.append(approvalBanner(a,(appr?'“'+ocTitle(appr)+'” is':capF(callOf(a))+' is')+' waiting for you to allow a step. Answer it in OpenCode.'));
  const kv=h('dl','kv'),cur=arr(L).find(s=>s.busy)||null;
  [['State',ocWord(a)],['On',cur?ocTitle(cur):'Nothing right now'],['Runs in','OpenCode on the NAS'],['ID',a.id]].forEach(([k,v])=>ap(kv,h('dt',null,k),h('dd',null,v)));
  const pn=gap(ap(h('div','panel'),a.description?h('p','desc',a.description):null),'12px');pn.append(kv);
  const lit=!appr&&!a.needsApproval;
  if(a.state==='down')pn.append(h('p','dim','OpenCode isn\'t answering, so new chats can\'t start.'));
  else if(run){const lm=busyLimit(a,run,lit),nk=key('New chat','');nk.disabled=true;lm.lastChild.append(nk);pn.append(lm);}
  else pn.append(ap(h('div','keys'),key('New chat',lit?'lit':'',()=>go('crew',{oc:{a:a.id,s:'new'}})),h('span','dim','Talk to '+callOf(a)+' directly.')));
  pn.append(scopeEl(a));
  const R=sessList(a,null,8);
  if(desk)m.append(ap(h('div','cols ocp'),ap(h('div','ocd'),pn),R));else ap(m,pn,R);}
/* a session: steps are counted by tool name; the live one says what it's doing */
const TOOLW={read:['Read','files'],grep:['Searched','times'],glob:['Searched','times'],list:['Listed','folders'],edit:['Edited','files'],write:['Wrote','files'],patch:['Edited','files'],bash:['Ran','commands'],webfetch:['Fetched','pages'],task:['Started','helpers'],todowrite:['Updated','to-dos']};
const LIVEW={read:'Reading a file',grep:'Searching',glob:'Searching',edit:'Editing a file',write:'Writing a file',bash:'Running a command',webfetch:'Fetching a page',deploy:'Going live',homeassistant_write:'Changing Home Assistant',homeassistant:'Changing Home Assistant',shell:'Running a command'};
function stepsEl(T){const tl=arr(T.tools);if(!tl.length)return null;const done=tl.filter(t=>t.status==='done'||t.status==='error'),cnt={},order=[];
  done.forEach(t=>{const k=(TOOLW[t.name]||[t.name])[0];if(!(k in cnt)){cnt[k]=0;order.push(k);}cnt[k]++;});
  const w=h('div','ocsteps'),c=h('div','cnt');order.forEach(k=>c.append(ap(h('span'),k+' ',h('b',null,String(cnt[k])))));
  const bad=done.filter(t=>t.status==='error').length;
  if(done.length)w.append(ap(h('span','tool'),pip('done'),h('span',null,done.length+' step'+(done.length===1?'':'s')+(bad?' · '+bad+' failed':''))),c);
  const live=tl.find(t=>t.status==='pending');
  if(live&&T.needsApproval)w.append(ap(h('span','tool'),pip('caut'),h('span',null,'Waiting for approval · '+(LIVEW[live.name]||live.name))));
  return w.childNodes.length?w:null;}
function ocThread(a,sid,T){
  const box=gap(h('div','stack conv'),'0'),c=h('div','convo'),url=API.ocSession(sid);c.setAttribute('aria-live','polite');box.append(grouphd('Conversation'));
  const ld=LOADING[url]||LOADING[API.ocSession(sid,true)];
  const nt=h('div','fetchln');nt.append(ld?ap(h('span','tag'),pip('busy'),'Asking OpenCode…'):h('span',null,T&&T.busy?'Checks OpenCode every 15 s while open.':'From OpenCode on the NAS; not stored here.'),
    lnk('Refresh',()=>{ocFetch(D.oct,sid,API.ocSession(sid,true),TPICK);render();}));box.append(nt);
  if(!T){const e=ERR[url];c.append(e?errState('the chat',e):skel(3));box.append(c);return box;}
  if(T.clipped)c.append(h('p','st','Older messages are in OpenCode.'));
  const msgs=arr(T.messages),lastA=msgs.map(x=>x.role).lastIndexOf('assistant'),you=T.origin==='bridge'?'You':T.origin==='ticket'?'Ticket'+(T.ticketId?' #'+T.ticketId:''):'You in OpenCode';
  msgs.forEach((x,i)=>{if(x.role==='user'){c.append(maskInto(h('div','u'),x.text),h('span','by',[you,tm(x.at)?hm(tm(x.at)):null,i===msgs.length-1&&T.busy?'Delivered':null].filter(Boolean).join(' · ')));}
    else{const e=h('div','a');e.append(ap(h('span','who',a.name),h('span','num',tm(x.at)?hm(tm(x.at)):'')));str(x.text).split(/\n{2,}/).forEach(p=>e.append(maskInto(h('p','md'),p)));c.append(e);}
    if(i===lastA||(lastA<0&&i===msgs.length-1)){const s=stepsEl(T);if(s&&(!T.busy||T.needsApproval))c.append(s);}});
  if(!msgs.length&&!T.busy)c.append(h('p','st','No messages yet.'));
  if(T.busy&&!T.needsApproval){const lu=[...msgs].reverse().find(x=>x.role==='user'),t0=tm(lu&&lu.at)||tm(T.busySince)||now(),el=Math.max(0,Math.round((now()-t0)/1000));S.oc0=t0;
    const live=arr(T.tools).find(t=>t.status==='running'),s=stepsEl(T),clk=h('span','num',Math.floor(el/60)+':'+p2(el%60));clk.id='oc-el';
    const wk=ap(h('div','a work'),ap(h('span','who',a.name)),ap(h('span','tool busy'),pip('busy'),ap(h('span',null,'Working… '),clk,' · you can close the Bridge')));
    if(live)wk.append(ap(h('span','tool'),pip('done'),h('span',null,'Now: '+(LIVEW[live.name]||live.name))));
    if(s)wk.append(s);
    wk.append(h('p','dim','Replies take a few minutes on the local model.'));c.append(wk);}
  if(T.needsApproval)c.append(approvalBanner(a,capF(callOf(a))+' is waiting for you to allow this step. Answer it in OpenCode.',T.approvalText));
  box.append(c);
  if(T.origin==='bridge')box.append(ocComposer(a,sid,T));
  else if(T.origin==='ticket'&&T.ticketId!=null)box.append(ap(h('div','composer'),ap(h('div','limit'),ap(h('p'),h('b',null,'This chat belongs to ticket #'+T.ticketId),' · the Bridge only continues its own chats.'),
    ap(h('div','keys'),key('Reply in ticket #'+T.ticketId,'',()=>go('tickets',{ticket:T.ticketId}))))));
  else{const k=openOC('');box.append(ap(h('div','composer'),ap(h('div','limit'),ap(h('p'),h('b',null,'Started in OpenCode · continue there'),k?null:' · open OpenCode on the NAS.'),k?ap(h('div','keys'),k):null)));}
  return box;}
const OC_ERR={agent_busy:null,session_busy:'Still working on your last message · send once it replies',rate_limited:null,not_bridge_session:'Only chats started from the Bridge can be continued here',not_found:'That chat is gone in OpenCode'};
function ocComposer(a,sid,T){
  const dk='oc:'+a.id+'/'+sid,cp=h('div','composer'),pn=h('div','panel'),ta=h('textarea');ta.rows=2;ta.maxLength=4000;ta.placeholder='Message '+callOf(a);ta.setAttribute('aria-label','Message '+a.name);bindDraft(ta,dk);
  const busy=!!(T&&T.busy),lit=!busy&&!(T&&T.needsApproval);
  const send=()=>{const v=str(S.drafts[dk]).trim();if(!v||busy||S.ocSending)return;S.ocSending=true;
    post(API.ocSend(a.id),sid==='new'?{text:v}:{text:v,sessionId:sid}).then(j=>{delete S.drafts[dk];S.ocBusy=null;
        if(D.ocs[a.id]!==undefined)ocFetch(D.ocs,a.id,API.ocSessions(a.id),SPICK);delete D.oct[j.sessionId];if(D.oca)load('oca',true);
        toast('Sent · '+a.name);go('crew',{oc:{a:a.id,s:j.sessionId}});},
      e=>{const d=e.data||{};
        if(e.code==='agent_busy'&&d.sessionId){S.ocBusy={a:a.id,s:d.sessionId,at:now()};toast(capF(callOf(a))+' is busy with another Bridge chat');if(sid!=='new')go('crew',{oc:{a:a.id,s:'new'}});}
        else if(e.code==='rate_limited')toast('Wait '+Math.max(1,Math.ceil(+d.retryAfter||10))+' s before sending again');
        else if(OC_ERR[e.code]){toast(OC_ERR[e.code]);if(sid!=='new')ocFetch(D.oct,sid,API.ocSession(sid),TPICK);}
        else failToast('Not sent',e);}).finally(()=>{S.ocSending=false;render();});};
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
  const sk=key('Send',lit?'lit':'',send);sk.disabled=busy;
  ap(pn,ta,ap(h('div','row sb'),busy?h('span','wait','Send once it replies'):h('span','dim desk-only','Enter sends'),sk));
  cp.append(pn,h('p','cnote','Goes to '+callOf(a)+' as a message from you (sandbox only).'));return cp;}
function ocNew(a){const box=gap(h('div','stack conv'),'0'),c=h('div','convo');box.append(grouphd('New chat'));
  const run=bridgeRun(a);
  if(run)c.append(busyLimit(a,run,true));
  else c.append(ap(h('div','ocnew'),h('p','h','Ask '+callOf(a)+' anything.'),h('p',null,'It answers in a new OpenCode chat. Replies take a few minutes on the local model; you can close the Bridge.')));
  box.append(c,ocComposer(a,'new',run?{busy:true}:null));return box;}
function ocSession(m){
  const a=AGENTS.find(x=>x.id===S.oc.a);if(!a||!isOC(a)){m.append(ap(h('div','empty'),h('p','h','That agent is gone.'),key('Back to Crew','',()=>go('crew',{crew:'agents'}))));return;}
  const sid=S.oc.s,L=ocSessions(a),s=arr(L).find(x=>x.id===sid),st=aState(a);
  const T=sid==='new'?null:ocSessionOf(sid),e=sid==='new'?null:ERR[API.ocSession(sid)];
  if(sid!=='new'&&!T&&e&&e.status===404){m.append(crumb(a.name,()=>go('crew',{agent:a.id,crew:'agents'}),'Chat'),ap(h('div','empty'),h('p','h','That chat is gone.'),h('p',null,'OpenCode no longer has it.'),key('Back to '+a.name,'',()=>go('crew',{agent:a.id,crew:'agents'}))));return;}
  const S_=T||s,title=sid==='new'?'New chat':S_?ocTitle(S_):'Chat';
  m.append(crumb(a.name,()=>go('crew',{agent:a.id,crew:'agents'}),title));
  const sw=S_&&S_.needsApproval?['caut','Needs you']:S_&&S_.busy?['busy','Working']:['done','Answered'];
  m.append(ap(h('div','stack'),ap(h('div','row sb'),h('h1','vt',title),sid==='new'||!S_?null:tag(sw[0],sw[1],sw[0]==='caut'?'caut-t':'')),
    h('p','lead',[a.name,'OpenCode',sid==='new'?ORIGIN.bridge:originW(S_)].filter(Boolean).join(' · '))));
  const cv=sid==='new'?ocNew(a):ocThread(a,sid,T);
  if(isDesk()){const side=gap(ap(h('div','ocd ocside'),ap(h('div','panel'),ap(h('div','row sb'),h('b',null,a.name),tag(pipOf(st.c),st.c==='busy'?ocWord(a):st.w,st.c==='caut'?'caut-t':'')),a.description?h('p','dim',a.description):null,scopeEl(a)),sessList(a,sid,6)),'12px');
    m.append(ap(h('div','cols sessd'),side,cv));}
  else m.append(cv);}
/* SSE "opencode" (a send went through): refresh what's on screen */
function ocChanged(d){if(d.agent&&D.ocs[d.agent]!==undefined)ocFetch(D.ocs,d.agent,API.ocSessions(d.agent),SPICK);
  if(d.sessionId&&D.oct[d.sessionId]!==undefined)ocFetch(D.oct,d.sessionId,API.ocSession(d.sessionId),TPICK);if(D.oca)load('oca',true);}
/* polls only while the page is visible: an open chat every 15 s, the agents and session lists every 30 s */
let ocLast=0;
function ocPoll(force){if(document.hidden||!AGENTS.some(isOC))return;
  if(S.view==='crew'&&S.oc&&S.oc.s!=='new'&&D.oct[S.oc.s]!==undefined)ocFetch(D.oct,S.oc.s,API.ocSession(S.oc.s),TPICK);
  if(!force&&now()-ocLast<29000)return;ocLast=now();
  if(!['now','crew','chat'].includes(S.view))return;
  if(D.oca)load('oca',true);Object.keys(D.ocs).forEach(id=>ocFetch(D.ocs,id,API.ocSessions(id),SPICK));}
setInterval(()=>ocPoll(false),15000);

/* ---------- boot ---------- */
parseRoute();paintScheme();
load('state');load('crew');
render(true);
sseOpen();
})();
