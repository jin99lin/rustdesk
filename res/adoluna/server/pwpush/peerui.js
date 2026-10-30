/* Adoluna Support admin: compact device list + detail drawer + 密码推送 menu. Injected by adoluna-pwpush. */
(function(){
"use strict";
const LG=()=>String(localStorage.getItem("lang")||navigator.language||"zh-CN"),EN=()=>!/^zh/i.test(LG()),T=(z,e)=>EN()?e:z;
const API="/api/admin",PW="/_admin/adoluna-api/password",tok=()=>localStorage.getItem("access_token")||"";
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(u,body){const r=await fetch(u.startsWith("/_")?u:API+u,{method:body?"POST":"GET",headers:{"api-token":tok(),"Content-Type":"application/json","Accept-Language":EN()?"en":"zh-CN"},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||j.message||("HTTP "+r.status));if(j.code!==undefined&&j.code!==0)throw new Error(j.message||("code "+j.code));return j.data!==undefined?j.data:j}
function toast(t,err){const d=document.createElement("div");d.className="adp-toast"+(err?" e":"");d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.remove(),2600)}
const fmtET=ts=>ts?new Date(ts*1000).toLocaleString(EN()?"en-US":"zh-CN",{timeZone:"America/New_York",hour12:false})+" ET":"—";
function rel(ts){if(!ts)return T("从未","Never");const s=Date.now()/1000-ts;if(s<3600)return Math.max(1,Math.round(s/60))+T(" 分钟前"," min ago");if(s<86400)return Math.round(s/3600)+T(" 小时前"," hr ago");const d=Math.floor(s/86400);return d+T(" 天前",d===1?" day ago":" days ago")}
const ONLINE=300; // last_online_time is not bumped on every heartbeat
const isOn=p=>p.last_online_time&&Date.now()/1000-p.last_online_time<ONLINE;
const osName=o=>{o=String(o||"");const i=o.indexOf(" / ");return(i>=0?o.slice(i+3):o).trim()||"—"};
function stHtml(p){if(isOn(p))return'<span class="adp-dot adp-on"></span>'+T("在线","Online");const d=(Date.now()/1000-p.last_online_time)/86400;
 return'<span title="'+esc(fmtET(p.last_online_time))+'"><span class="adp-dot adp-off"></span><span style="color:#6B7280">'+rel(p.last_online_time)+'</span></span>'+(p.last_online_time&&d>30?'<span class="adp-amb">'+T("长期离线","Offline 30+ days")+'</span>':'')}
function dialog(html,onOk,okText,needId){const m=document.createElement("div");m.className="adp-dlgm";const d=document.createElement("div");d.className="adp-dlg";
 d.innerHTML=html+(needId?'<input id="adp-cid" placeholder="'+T("输入设备 ID 确认","Type the device ID to confirm")+'">':'')+'<div style="text-align:right;margin-top:20px"><button class="btn" data-c>'+T("取消","Cancel")+'</button> <button class="btn pri" data-o'+(needId?' disabled':'')+'>'+okText+'</button></div>';
 document.body.append(m,d);const close=()=>{m.remove();d.remove()};d.querySelector("[data-c]").onclick=close;m.onclick=close;const ok=d.querySelector("[data-o]");
 if(needId){const i=d.querySelector("#adp-cid");i.oninput=()=>ok.disabled=i.value.trim()!==needId;i.focus()}
 ok.onclick=async()=>{ok.disabled=true;try{await onOk();close()}catch(e){toast(T("失败：","Failed: ")+e.message,1);ok.disabled=false}}}
let S={list:[],pw:null,users:[],groups:[],q:"",u:""},root=null;
async function load(){const d=await api("/peer/list?page=1&page_size=1000");S.list=(d.list||[]);
 try{S.pw=await api(PW)}catch(e){S.pw=null}
 try{S.users=(await api("/user/list?page=1&page_size=1000")).list||[]}catch(e){S.users=[]}
 try{S.groups=(await api("/device_group/list?page=1&page_size=1000")).list||[]}catch(e){S.groups=[]}}
function rows(){const q=S.q.toLowerCase(),u=S.u.toLowerCase();return S.list.filter(p=>(!q||(p.id+" "+p.hostname+" "+p.alias).toLowerCase().includes(q))&&(!u||String(p.username).toLowerCase().includes(u)))
 .sort((a,b)=>(isOn(b)-isOn(a))||(b.last_online_time-a.last_online_time))}
function renderList(){const r=rows();root.querySelector("#adp-tb").innerHTML=r.map(p=>'<tr class="r" data-id="'+esc(p.row_id)+'"><td class="adp-mono">'+esc(p.id)+'</td><td>'+esc(p.alias||p.hostname)+'</td><td>'+esc(osName(p.os))+'</td><td>'+stHtml(p)+'</td><td>'+esc(p.username||"—")+'</td></tr>').join("")||'<tr><td colspan=5 style="text-align:center;color:#6B7280">'+T("没有设备","No devices")+'</td></tr>';
 root.querySelector("#adp-n").textContent=T("共 "+r.length+" 台",r.length+(r.length===1?" device":" devices"));root.querySelectorAll("tr.r").forEach(t=>t.onclick=()=>openD(S.list.find(p=>String(p.row_id)===t.dataset.id)))}
function pwBox(p){const s=S.pw;if(!s)return'<span class="adp-hint">'+T("无法读取推送状态（需在内网用管理员登录）","Can't read push status (sign in as admin on the internal network)")+'</span>';
 const inAll=!s.targets.length,inT=inAll||s.targets.includes(String(p.id));
 return T('后台当前密码版本：','Current password version: ')+'<b>v'+esc(s.version||0)+'</b>　<span class="adp-hint">'+esc(s.updated_at||T("还没有设置过","Never set"))+'</span><br>'+T('这台设备：','This device: ')+(inT?'<span class="adp-tag">'+(inAll?T('已在推送范围（全部设备）','In push scope (all devices)'):T('已在推送范围','In push scope'))+'</span>':'<span class="adp-tag i">'+T('不在推送范围','Not in push scope')+'</span>')+
 '<br><span class="adp-hint">'+T('「重置密码」会把后台当前的固定密码推送到这台设备（需 76 版及以后）。','"Reset password" pushes the current permanent password to this device (build 76 or later).')+'</span>'}
function sel(name,cur,opts){return'<select data-k="'+name+'"><option value="0">—</option>'+opts.map(o=>'<option value="'+o.id+'"'+(Number(cur)===Number(o.id)?" selected":"")+'>'+esc(o.n)+'</option>').join("")+'</select>'}
function openD(p){if(!p)return;const w=document.createElement("div");w.id="adp-d";
 const inp=(k,l)=>'<label>'+l+'</label><input data-k="'+k+'" value="'+esc(p[k])+'">';
 const ro=(l,v)=>'<label>'+l+'</label><span>'+esc(v||"—")+'</span>';
 w.innerHTML='<div class="mask"></div><div class="drw"><div class="dh"><h2><span><span class="adp-mono">'+esc(p.id)+'</span> · '+esc(p.alias||p.hostname)+'</span><span class="x">✕</span></h2><div class="sub">'+stHtml(p)+' · '+esc(osName(p.os))+'</div></div>'+
 '<div class="db"><div class="sec"><h3>'+T('基本信息','Basic info')+'</h3><div class="f">'+inp("hostname",T("主机名","Hostname"))+inp("username",T("用户名","Username"))+inp("alias",T("别名","Alias"))+
 '<label>'+T('所属用户','Owner')+'</label>'+(S.users.length?sel("user_id",p.user_id,S.users.map(u=>({id:u.id,n:u.nickname||u.username}))):'<input data-k="user_id" value="'+esc(p.user_id)+'">')+
 '<label>'+T('分组','Group')+'</label>'+(S.groups.length?sel("group_id",p.group_id,S.groups.map(g=>({id:g.id,n:g.name}))):'<input data-k="group_id" value="'+esc(p.group_id)+'">')+'</div></div>'+
 '<div class="sec"><h3>'+T('设备信息','Device info')+'</h3><div class="f">'+ro(T("设备 ID","Device ID"),p.id)+ro(T("系统","OS"),p.os)+ro(T("处理器","CPU"),p.cpu)+ro(T("内存","Memory"),p.memory)+ro(T("最后 IP","Last IP"),p.last_online_ip)+ro(T("客户端版本","Client version"),p.version)+ro("UUID",p.uuid)+ro(T("最后在线","Last online"),fmtET(p.last_online_time))+'</div></div>'+
 '<div class="sec"><h3>'+T('固定密码','Permanent password')+'</h3><div class="pc" id="adp-pw">'+pwBox(p)+'</div></div></div>'+
 '<div class="df"><button class="btn del" data-a="del">'+T('删除','Delete')+'</button><span style="flex:1"></span><button class="btn rr" data-a="pw">'+T('重置密码','Reset password')+'</button><a class="btn" data-a="link" href="adolunasupport://'+encodeURIComponent(p.id)+'" style="display:inline-flex;align-items:center;text-decoration:none">'+T('远程连接','Connect')+'</a><button class="btn" data-a="fav">'+T('☆ 收藏','☆ Favorite')+'</button><button class="btn pri" data-a="save">'+T('保存修改','Save')+'</button></div></div>';
 root.appendChild(w);const close=()=>w.remove();w.querySelector(".mask").onclick=close;w.querySelector(".x").onclick=close;
 const A=a=>w.querySelector('[data-a="'+a+'"]');
 A("save").onclick=async()=>{const b={};["row_id","id","cpu","hostname","memory","os","username","uuid","version","user_id","group_id","alias"].forEach(k=>b[k]=p[k]);
  w.querySelectorAll("[data-k]").forEach(e=>{const k=e.dataset.k;b[k]=(k==="user_id"||k==="group_id")?Number(e.value)||0:e.value.trim()});
  try{await api("/peer/update",b);toast(T("已保存","Saved"));Object.assign(p,b);close();renderList()}catch(e){toast(T("保存失败：","Save failed: ")+e.message,1)}};
 A("pw").onclick=()=>dialog('<h4>'+T('重置密码','Reset password')+'</h4><div style="line-height:1.7">'+T('把后台当前的固定密码（v'+esc(S.pw?S.pw.version:"?")+'）推送到设备 <b class="adp-mono">'+esc(p.id)+'</b>（'+esc(p.alias||p.hostname)+'）？','Push the current permanent password (v'+esc(S.pw?S.pw.version:"?")+') to device <b class="adp-mono">'+esc(p.id)+'</b> ('+esc(p.alias||p.hostname)+')?')+'</div><div class="adp-hint" style="margin-top:8px">'+T('在线设备约 15 秒内生效；离线设备下次上线时生效。','Online devices update in about 15 seconds; offline devices update next time they come online.')+'</div>',
  async()=>{const r=await api("/_admin/adoluna-api/reset",{id:String(p.id)});S.pw=r;w.querySelector("#adp-pw").innerHTML=pwBox(p);toast(EN()?"Added to push scope":(r.note||"已加入推送范围"))},T("推送密码","Push password"));
 A("fav").onclick=async()=>{try{await api("/address_book/create",{id:p.id,hostname:p.hostname,username:p.username,alias:p.alias,platform:osName(p.os),user_id:p.user_id||0,tags:[]});toast(T("已收藏到地址簿","Added to address book"))}catch(e){toast(T("收藏失败：","Favorite failed: ")+e.message,1)}};
 A("del").onclick=()=>dialog('<h4>'+T('删除设备','Delete device')+'</h4><div style="line-height:1.7">'+T('确定从后台删除设备 <b class="adp-mono">'+esc(p.id)+'</b>（'+esc(p.hostname)+'）？','Delete device <b class="adp-mono">'+esc(p.id)+'</b> ('+esc(p.hostname)+') from the admin?')+'<br><span class="adp-hint">'+T('请输入设备 ID 以确认。设备下次上线会重新出现在列表。','Type the device ID to confirm. The device reappears in the list next time it comes online.')+'</span></div>',
  async()=>{await api("/peer/delete",{row_id:p.row_id});toast(T("已删除","Deleted"));close();S.list=S.list.filter(x=>x!==p);renderList()},T("删除","Delete"),String(p.id))}
const isPeerPage=()=>/\/user\/peer(\?|$)/.test(location.hash||"")||/\/user\/peer\/?$/.test(location.pathname);
const abView=()=>{const h=location.hash||"";return /^#\/user\/addressBook(\?|$)/.test(h)?"absys":/^#\/my\/address_book(\?|$)/.test(h)?"abmy":null};
const curView=()=>isPeerPage()?"peer":/^#\/(\?.*)?$/.test(location.hash||"")?"mypeer":abView();
const deb=(f,ms)=>{let k=0;return(...a)=>{clearTimeout(k);k=setTimeout(()=>f(...a),ms||300)}};
function mount(){const main=document.querySelector(".app-main")||document.querySelector(".el-main");if(!main)return;const v=curView();
 const o=document.getElementById("adp");if(o&&o.dataset.v!==v){o.remove();[...main.children].forEach(c=>c.classList.remove("adp-hidden"))}
 if(!v)return;[...main.children].forEach(c=>{if(c.id!=="adp")c.classList.add("adp-hidden")});if(document.getElementById("adp"))return;
 if(v==="mypeer")return myMount(main);if(v!=="peer")return abMount(main,v);
 root=document.createElement("div");root.id="adp";root.dataset.v="peer";root.innerHTML='<div class="card"><div class="bar"><input id="adp-q" placeholder="'+T('设备 ID / 主机名','Device ID / hostname')+'" style="width:200px"><input id="adp-u" placeholder="'+T('用户名','Username')+'" style="width:150px"><button class="btn txt" id="adp-r">'+T('重置','Reset')+'</button></div><table><thead><tr><th>'+T('设备 ID','Device ID')+'</th><th>'+T('主机名','Hostname')+'</th><th>'+T('系统','OS')+'</th><th>'+T('状态','Status')+'</th><th>'+T('用户名','Username')+'</th></tr></thead><tbody id="adp-tb"><tr><td colspan=5>'+T('读取中…','Loading…')+'</td></tr></tbody></table><div id="adp-n" style="margin-top:14px;color:#6B7280;font-size:13px"></div></div>';
 main.appendChild(root);const q=root.querySelector("#adp-q"),u=root.querySelector("#adp-u");
 const go=deb(()=>{S.q=q.value.trim();S.u=u.value.trim();renderList()},250);q.oninput=u.oninput=go;
 root.querySelector("#adp-r").onclick=()=>{q.value=u.value="";S.q=S.u="";renderList()};
 load().then(renderList).catch(e=>{root.querySelector("#adp-tb").innerHTML='<tr><td colspan=5 style="color:#C62828">'+T('读取失败：','Failed to load: ')+esc(e.message)+'</td></tr>'})}
/* ---- address books (Jay 10/29): same look as Devices, live search, searchable device picker, only existing device IDs ---- */
let AB={v:null,list:[],dmap:{},devs:[],cols:[],tags:[],users:[],me:null,q:"",c:"",o:""};
const PS="?page=1&page_size=1000";
const abA=v=>v==="absys"?{list:"/address_book/list",create:"/address_book/create",update:"/address_book/update",del:"/address_book/delete",cols:"/address_book_collection/list",tags:"/tag/list"}
 :{list:"/my/address_book/list",create:"/my/address_book/create",update:"/my/address_book/update",del:"/my/address_book/delete",cols:"/my/address_book_collection/list",tags:"/my/tag/list"};
const DEF_AB=()=>T("我的地址簿","My Address Book");
const tagsOf=e=>Array.isArray(e.tags)?e.tags:(()=>{try{return JSON.parse(e.tags||"[]")||[]}catch(x){return[]}})();
const colName=e=>{const c=Number(e.collection_id)||0;if(!c)return DEF_AB();if(e.collection&&e.collection.name)return e.collection.name;const x=AB.cols.find(k=>Number(k.id)===c);return x?x.name:"#"+c};
const uName=id=>{const u=AB.users.find(x=>Number(x.id)===Number(id));return u?(u.nickname||u.username):(AB.me&&Number(AB.me.id)===Number(id)?AB.me.username:"#"+id)};
const devName=d=>d?(d.alias||d.hostname||""):"";
async function abLoad(){const A=abA(AB.v),E={list:[]};
 const r=await Promise.all([api(A.list+PS),api(A.cols+PS).catch(()=>E),api(A.tags+PS).catch(()=>E),api("/_admin/adoluna-api/devices").catch(()=>E),api("/user/current").catch(()=>null)]);
 AB.list=r[0].list||[];AB.cols=r[1].list||[];AB.tags=r[2].list||[];AB.devs=r[3].list||[];AB.me=r[4];AB.dmap={};AB.devs.forEach(d=>AB.dmap[d.id]=d);
 if(AB.v==="absys"){try{AB.users=(await api("/user/list"+PS)).list||[]}catch(e){AB.users=[]}}}
function hay(parts){return parts.filter(Boolean).join(" ").toLowerCase()}
const qMatch=(q,h)=>q.toLowerCase().split(/\s+/).filter(Boolean).every(t=>h.includes(t));
function abRows(){return AB.list.filter(e=>{const d=AB.dmap[e.id];
  if(AB.c!==""&&Number(e.collection_id||0)!==Number(AB.c))return false;if(AB.o!==""&&Number(e.user_id)!==Number(AB.o))return false;
  return!AB.q||qMatch(AB.q,hay([e.id,e.alias,e.hostname,e.username,tagsOf(e).join(" "),d&&d.hostname,d&&d.alias,d&&d.group,d&&d.tags.join(" ")]))})
 .sort((a,b)=>{const x=AB.dmap[a.id]||{},y=AB.dmap[b.id]||{};return(isOn(y)-isOn(x))||((y.last_online_time||0)-(x.last_online_time||0))})}
const tagChips=a=>a.map(t=>'<span class="adp-chip">'+esc(t)+'</span>').join(" ")||'<span style="color:#9CA3AF">—</span>';
function abStatus(e){const d=AB.dmap[e.id];return d?stHtml(d):'<span class="adp-amb">'+T("不在系统中","Not in system")+'</span>'}
function abRender(){const R=document.getElementById("adp");if(!R)return;const r=abRows(),sys=AB.v==="absys",n=sys?7:6;
 R.querySelector("#adp-tb").innerHTML=r.map(e=>{const d=AB.dmap[e.id];return'<tr class="r" data-r="'+esc(e.row_id)+'"><td class="adp-mono">'+esc(e.id)+'</td><td>'+esc(e.alias||e.hostname||devName(d)||"—")+'</td><td>'+esc(d&&d.group||"—")+'</td><td>'+tagChips(tagsOf(e))+'</td><td>'+abStatus(e)+'</td><td>'+esc(colName(e))+'</td>'+(sys?'<td>'+esc(uName(e.user_id))+'</td>':'')+'</tr>'}).join("")
  ||'<tr><td colspan='+n+' style="text-align:center;color:#6B7280">'+(AB.list.length?T("没有匹配的设备","No matching devices"):T("地址簿里还没有设备，点「添加」从已有设备里选","No devices yet. Click Add to pick from existing devices."))+'</td></tr>';
 R.querySelector("#adp-n").textContent=T("共 "+r.length+" 台",r.length+(r.length===1?" device":" devices"));
 R.querySelectorAll("tr.r").forEach(t=>t.onclick=()=>abDrawer(AB.list.find(e=>String(e.row_id)===t.dataset.r)))}
function colOpts(uid,cur,withAll){const own=AB.cols.filter(c=>AB.v!=="absys"||uid==null||Number(c.user_id)===Number(uid));
 return(withAll?'<option value="">'+T("全部地址簿","All address books")+'</option>':'')+'<option value="0"'+(String(cur)==="0"?" selected":"")+'>'+esc(DEF_AB())+'</option>'+own.map(c=>'<option value="'+c.id+'"'+(String(cur)===String(c.id)?" selected":"")+'>'+esc(c.name)+(AB.v==="absys"&&uid==null?' ('+esc(uName(c.user_id))+')':'')+'</option>').join("")}
function abMount(main,v){AB.v=v;AB.q=AB.c=AB.o="";const R=document.createElement("div");R.id="adp";R.dataset.v=v;const sys=v==="absys";
 R.innerHTML='<div class="card"><div class="bar"><input id="adp-q" placeholder="'+T("搜索设备 ID / 名称 / 门店 / 标签","Search ID / name / store / tag")+'" style="width:280px">'+
 '<select id="adp-c"></select>'+(sys?'<select id="adp-o"><option value="">'+T("全部所属用户","All owners")+'</option></select>':'')+'<button class="btn txt" id="adp-r">'+T("重置","Reset")+'</button><span style="flex:1"></span><span id="adp-n" class="adp-hint"></span><button class="btn pri" id="adp-add">'+T("+ 添加设备","+ Add device")+'</button></div>'+
 '<table><thead><tr><th>'+T("设备 ID","Device ID")+'</th><th>'+T("名称","Name")+'</th><th>'+T("门店","Store")+'</th><th>'+T("标签","Tags")+'</th><th>'+T("状态","Status")+'</th><th>'+T("地址簿","Address book")+'</th>'+(sys?'<th>'+T("所属用户","Owner")+'</th>':'')+'</tr></thead><tbody id="adp-tb"><tr><td colspan=7 style="text-align:center;color:#6B7280">'+T("加载中…","Loading…")+'</td></tr></tbody></table></div>';
 main.appendChild(R);const q=R.querySelector("#adp-q"),c=R.querySelector("#adp-c"),o=R.querySelector("#adp-o");
 q.oninput=deb(()=>{AB.q=q.value.trim();abRender()},200);c.onchange=()=>{AB.c=c.value;abRender()};if(o)o.onchange=()=>{AB.o=o.value;abRender()};
 R.querySelector("#adp-r").onclick=()=>{q.value="";c.value="";if(o)o.value="";AB.q=AB.c=AB.o="";abRender()};R.querySelector("#adp-add").onclick=abAdd;
 const fill=()=>{c.innerHTML=colOpts(null,"",true);if(o)o.innerHTML='<option value="">'+T("全部所属用户","All owners")+'</option>'+AB.users.map(u=>'<option value="'+u.id+'">'+esc(u.nickname||u.username)+'</option>').join("")};
 abLoad().then(()=>{fill();abRender()}).catch(e=>{R.querySelector("#adp-tb").innerHTML='<tr><td colspan=7 style="color:#C62828">'+T("读取失败：","Failed to load: ")+esc(e.message)+'</td></tr>'})}
function tagBox(e,cid,uid){const cur=new Set(tagsOf(e));const av=AB.tags.filter(t=>Number(t.collection_id||0)===Number(cid)&&(AB.v!=="absys"||Number(t.user_id)===Number(uid))).map(t=>t.name);
 const all=[...new Set(av.concat([...cur]))];return all.length?all.map(t=>'<label class="adp-ck"><input type="checkbox" value="'+esc(t)+'"'+(cur.has(t)?" checked":"")+'> '+esc(t)+'</label>').join(""):'<span class="adp-hint">'+T("这个地址簿还没有标签（在「标签」页添加）","No tags in this address book yet (add them on the Tags page)")+'</span>'}
function abDrawer(e){if(!e)return;const R=document.getElementById("adp");const d=AB.dmap[e.id],sys=AB.v==="absys",w=document.createElement("div");w.id="adp-d";
 const ro=(l,v)=>'<label>'+l+'</label><span>'+esc(v||"—")+'</span>';
 w.innerHTML='<div class="mask"></div><div class="drw"><div class="dh"><h2><span><span class="adp-mono">'+esc(e.id)+'</span> · '+esc(e.alias||e.hostname||devName(d))+'</span><span class="x">✕</span></h2><div class="sub">'+abStatus(e)+(d?' · '+esc(osName(d.os)):'')+'</div></div>'+
 '<div class="db"><div class="sec"><h3>'+T("地址簿信息","Address book entry")+'</h3><div class="f"><label>'+T("别名","Alias")+'</label><input data-k="alias" value="'+esc(e.alias)+'" placeholder="'+esc(devName(d))+'">'+
 '<label>'+T("地址簿","Address book")+'</label><select data-k="collection_id">'+colOpts(e.user_id,Number(e.collection_id||0),false)+'</select><label>'+T("标签","Tags")+'</label><div id="adp-tags">'+tagBox(e,e.collection_id||0,e.user_id)+'</div>'+(sys?ro(T("所属用户","Owner"),uName(e.user_id)):'')+'</div></div>'+
 '<div class="sec"><h3>'+T("设备信息","Device info")+'</h3><div class="f">'+ro(T("设备 ID","Device ID"),e.id)+ro(T("主机名","Hostname"),d?d.hostname:e.hostname)+ro(T("门店","Store"),d&&d.group)+ro(T("系统","OS"),d?d.os:e.platform)+ro(T("最后在线","Last online"),d?fmtET(d.last_online_time):"")+'</div>'+(d?'':'<div class="adp-hint" style="margin-top:10px">'+T("这个 ID 不在系统设备列表里（可能是旧记录）。","This ID isn't in the device list (probably an old entry).")+'</div>')+'</div></div>'+
 '<div class="df"><button class="btn del" data-a="del">'+T("从地址簿移除","Remove")+'</button><span style="flex:1"></span><a class="btn" href="adolunasupport://'+encodeURIComponent(e.id)+'" style="display:inline-flex;align-items:center;text-decoration:none">'+T("远程连接","Connect")+'</a><button class="btn pri" data-a="save">'+T("保存修改","Save")+'</button></div></div>';
 R.appendChild(w);const close=()=>w.remove();w.querySelector(".mask").onclick=close;w.querySelector(".x").onclick=close;const cs=w.querySelector('[data-k="collection_id"]');
 cs.onchange=()=>{w.querySelector("#adp-tags").innerHTML=tagBox({tags:[]},cs.value,e.user_id)};
 w.querySelector('[data-a="save"]').onclick=async()=>{const b=Object.assign({},e);delete b.collection;delete b.created_at;delete b.updated_at;b.alias=w.querySelector('[data-k="alias"]').value.trim();b.collection_id=Number(cs.value)||0;
  b.tags=[...w.querySelectorAll("#adp-tags input:checked")].map(i=>i.value);try{await api(abA(AB.v).update,b);Object.assign(e,b);if(e.collection&&Number(e.collection.id)!==b.collection_id)e.collection=null;toast(T("已保存","Saved"));close();abRender()}catch(x){toast(T("保存失败：","Save failed: ")+x.message,1)}};
 w.querySelector('[data-a="del"]').onclick=()=>dialog('<h4>'+T("从地址簿移除","Remove from address book")+'</h4><div style="line-height:1.7">'+T('把设备 <b class="adp-mono">'+esc(e.id)+'</b> 从「'+esc(colName(e))+'」移除？设备本身不会被删除。','Remove device <b class="adp-mono">'+esc(e.id)+'</b> from "'+esc(colName(e))+'"? The device itself is not deleted.')+'</div>',
  async()=>{await api(abA(AB.v).del,{row_id:e.row_id});AB.list=AB.list.filter(x=>x!==e);toast(T("已移除","Removed"));close();abRender()},T("移除","Remove"))}
/* searchable device picker: ID, hostname, alias, store (device group), tags; top 15; only existing devices */
function devSearch(q){const L=AB.devs.filter(d=>!q||qMatch(q,hay([d.id,d.hostname,d.alias,d.group,d.tags.join(" ")])));
 const ql=q.trim().toLowerCase();return L.sort((a,b)=>((ql&&b.id.startsWith(ql))-(ql&&a.id.startsWith(ql)))||(isOn(b)-isOn(a))||(b.last_online_time-a.last_online_time)).slice(0,15)}
function picker(box,onPick){box.innerHTML='<input class="adp-pq" placeholder="'+T("输入设备 ID、名称、门店或标签搜索","Search by device ID, name, store or tag")+'" autocomplete="off"><div class="adp-pl"></div>';
 const i=box.querySelector("input"),l=box.querySelector(".adp-pl");
 const show=()=>{const r=devSearch(i.value);l.innerHTML=r.map(d=>'<div class="adp-pi" data-id="'+esc(d.id)+'"><span class="adp-mono">'+esc(d.id)+'</span><span class="n">'+esc(devName(d)||"—")+'</span><span class="g">'+esc(d.group||T("未分组","No store"))+'</span><span class="s">'+stHtml(d)+'</span></div>').join("")||'<div class="adp-hint" style="padding:10px">'+(AB.devs.length?T("没有匹配的设备","No matching devices"):T("读不到设备列表","Couldn't load the device list"))+'</div>';
  l.querySelectorAll(".adp-pi").forEach(x=>x.onclick=()=>{l.querySelectorAll(".adp-pi").forEach(y=>y.classList.remove("on"));x.classList.add("on");onPick(AB.dmap[x.dataset.id])})};
 i.oninput=deb(show,150);show();setTimeout(()=>i.focus(),30)}
function abAdd(){const sys=AB.v==="absys",me=AB.me||{};let pick=null;
 const uSel=sys?'<label>'+T("所属用户","Owner")+'</label><select id="adp-au">'+AB.users.map(u=>'<option value="'+u.id+'"'+(Number(u.id)===Number(me.id)?" selected":"")+'>'+esc(u.nickname||u.username)+'</option>').join("")+'</select>':'';
 dialog('<h4>'+T("添加设备到地址簿","Add a device to the address book")+'</h4><div id="adp-pk"></div><div class="adp-f2">'+uSel+'<label>'+T("地址簿","Address book")+'</label><select id="adp-ac"></select><label>'+T("别名","Alias")+'</label><input id="adp-aa" placeholder="'+T("可选","Optional")+'"></div><div class="adp-hint" id="adp-asel" style="margin-top:8px">'+T("只能选择系统里已有的设备。","Only devices already in the system can be added.")+'</div>',
  async()=>{if(!pick)throw new Error(T("请先选择设备","Pick a device first"));const cid=Number(document.getElementById("adp-ac").value)||0,au=document.getElementById("adp-au");
   const b={id:pick.id,hostname:pick.hostname,alias:document.getElementById("adp-aa").value.trim(),platform:osName(pick.os),username:"",tags:[],collection_id:cid};if(sys)b.user_id=Number(au&&au.value)||Number(me.id)||0;
   await api(abA(AB.v).create,b);toast(T("已添加","Added"));await abLoad();abRender()},T("添加","Add"));
 const dl=[...document.querySelectorAll(".adp-dlg")].pop();dl.classList.add("wide");const ok=dl.querySelector("[data-o]");ok.disabled=true;
 const ac=dl.querySelector("#adp-ac"),au=dl.querySelector("#adp-au");const fc=()=>{ac.innerHTML=colOpts(sys?(au?au.value:me.id):null,AB.c!==""?AB.c:"0",false)};fc();if(au)au.onchange=fc;
 picker(dl.querySelector("#adp-pk"),d=>{pick=d;ok.disabled=false;dl.querySelector("#adp-asel").innerHTML=T("已选：","Selected: ")+'<b class="adp-mono">'+esc(d.id)+'</b> · '+esc(devName(d))+(d.group?' · '+esc(d.group):'')})}
/* ---- My Devices (Jay 10/29 batch 2): same look as System › Devices; Export + batch add to address book kept ---- */
let MY={list:[],q:"",sel:new Set(),cols:[]};
function myRows(){return MY.list.filter(p=>!MY.q||qMatch(MY.q,hay([p.id,p.hostname,p.alias,p.username,osName(p.os)]))).sort((a,b)=>(isOn(b)-isOn(a))||(b.last_online_time-a.last_online_time))}
function myRender(){const R=document.getElementById("adp");if(!R)return;const r=myRows();
 R.querySelector("#adp-tb").innerHTML=r.map(p=>'<tr class="r" data-id="'+esc(p.row_id)+'"><td class="ck"><input type="checkbox"'+(MY.sel.has(p.row_id)?" checked":"")+'></td><td class="adp-mono">'+esc(p.id)+'</td><td>'+esc(p.alias||p.hostname)+'</td><td>'+esc(osName(p.os))+'</td><td>'+stHtml(p)+'</td><td>'+esc(p.username||"—")+'</td></tr>').join("")||'<tr><td colspan=6 style="text-align:center;color:#6B7280">'+T("没有设备","No devices")+'</td></tr>';
 R.querySelector("#adp-n").textContent=T("共 "+r.length+" 台",r.length+(r.length===1?" device":" devices"));const bb=R.querySelector("#adp-bat");bb.disabled=!MY.sel.size;bb.textContent=T("批量加入地址簿","Add to address book")+(MY.sel.size?" ("+MY.sel.size+")":"");
 R.querySelectorAll("tr.r").forEach(t=>{const id=Number(t.dataset.id);const c=t.querySelector("input");c.onclick=e=>{e.stopPropagation();c.checked?MY.sel.add(id):MY.sel.delete(id);myRender()};t.onclick=()=>myDrawer(MY.list.find(p=>p.row_id===id))})}
function myDrawer(p){if(!p)return;const R=document.getElementById("adp"),w=document.createElement("div");w.id="adp-d";const ro=(l,v)=>'<label>'+l+'</label><span>'+esc(v||"—")+'</span>';
 w.innerHTML='<div class="mask"></div><div class="drw"><div class="dh"><h2><span><span class="adp-mono">'+esc(p.id)+'</span> · '+esc(p.alias||p.hostname)+'</span><span class="x">✕</span></h2><div class="sub">'+stHtml(p)+' · '+esc(osName(p.os))+'</div></div>'+
 '<div class="db"><div class="sec"><h3>'+T("设备信息","Device info")+'</h3><div class="f">'+ro(T("设备 ID","Device ID"),p.id)+ro(T("主机名","Hostname"),p.hostname)+ro(T("别名","Alias"),p.alias)+ro(T("用户名","Username"),p.username)+ro(T("系统","OS"),p.os)+ro(T("处理器","CPU"),p.cpu)+ro(T("内存","Memory"),p.memory)+ro(T("最后 IP","Last IP"),p.last_online_ip)+ro(T("客户端版本","Client version"),p.version)+ro(T("最后在线","Last online"),fmtET(p.last_online_time))+'</div></div></div>'+
 '<div class="df"><span style="flex:1"></span><a class="btn" href="adolunasupport://'+encodeURIComponent(p.id)+'" style="display:inline-flex;align-items:center;text-decoration:none">'+T("远程连接","Connect")+'</a><button class="btn pri" data-a="fav">'+T("加入地址簿","Add to address book")+'</button></div></div>';
 R.appendChild(w);const close=()=>w.remove();w.querySelector(".mask").onclick=close;w.querySelector(".x").onclick=close;w.querySelector('[data-a="fav"]').onclick=()=>{close();myBatch([p.row_id])}}
function myBatch(ids){dialog('<h4>'+T("加入地址簿","Add to address book")+'</h4><div style="line-height:1.7">'+T("把 "+ids.length+" 台设备加入：","Add "+ids.length+(ids.length===1?" device":" devices")+" to:")+'</div><select id="adp-bc" style="width:100%;height:32px;margin-top:8px"><option value="0">'+esc(DEF_AB())+'</option>'+MY.cols.map(c=>'<option value="'+c.id+'">'+esc(c.name)+'</option>').join("")+'</select><div class="adp-hint" style="margin-top:8px">'+T("已在该地址簿里的设备会跳过。","Devices already in that address book are skipped.")+'</div>',
 async()=>{await api("/my/address_book/batchCreateFromPeers",{peer_ids:ids,collection_id:Number(document.getElementById("adp-bc").value)||0,tags:[]});toast(T("已加入地址簿","Added to address book"));MY.sel.clear();myRender()},T("加入","Add"))}
function myExport(){const r=myRows(),h=["ID","Hostname","Alias","Username","OS","Version","Last IP","Last online (ET)"];const q=v=>'"'+String(v==null?"":v).replace(/"/g,'""')+'"';
 const csv="\ufeff"+[h].concat(r.map(p=>[p.id,p.hostname,p.alias,p.username,p.os,p.version,p.last_online_ip,fmtET(p.last_online_time)])).map(x=>x.map(q).join(",")).join("\r\n");
 const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="my-devices.csv";a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000)}
function myMount(main){MY.q="";MY.sel=new Set();const R=document.createElement("div");R.id="adp";R.dataset.v="mypeer";
 R.innerHTML='<div class="card"><div class="bar"><input id="adp-q" placeholder="'+T("设备 ID / 主机名 / 用户名","Device ID / hostname / username")+'" style="flex:0 1 280px;min-width:180px"><button class="btn txt" id="adp-r">'+T("重置","Reset")+'</button><span style="flex:1"></span><span id="adp-n" class="adp-hint"></span><button class="btn" id="adp-exp">'+T("导出","Export")+'</button><button class="btn pri" id="adp-bat" disabled></button></div>'+
 '<table><thead><tr><th class="ck"><input type="checkbox" id="adp-all"></th><th>'+T("设备 ID","Device ID")+'</th><th>'+T("主机名","Hostname")+'</th><th>'+T("系统","OS")+'</th><th>'+T("状态","Status")+'</th><th>'+T("用户名","Username")+'</th></tr></thead><tbody id="adp-tb"><tr><td colspan=6 style="text-align:center;color:#6B7280">'+T("加载中…","Loading…")+'</td></tr></tbody></table></div>';
 main.appendChild(R);const q=R.querySelector("#adp-q");q.oninput=deb(()=>{MY.q=q.value.trim();myRender()},200);R.querySelector("#adp-r").onclick=()=>{q.value="";MY.q="";myRender()};
 R.querySelector("#adp-exp").onclick=myExport;R.querySelector("#adp-bat").onclick=()=>MY.sel.size&&myBatch([...MY.sel]);
 R.querySelector("#adp-all").onclick=e=>{myRows().forEach(p=>e.target.checked?MY.sel.add(p.row_id):MY.sel.delete(p.row_id));myRender()};
 Promise.all([api("/my/peer/list"+PS),api("/my/address_book_collection/list"+PS).catch(()=>({list:[]}))]).then(([a,b])=>{MY.list=a.list||[];MY.cols=b.list||[];myRender()}).catch(e=>{R.querySelector("#adp-tb").innerHTML='<tr><td colspan=6 style="color:#C62828">'+T("读取失败：","Failed to load: ")+esc(e.message)+'</td></tr>'})}
/* ---- tables (batch 2): compact action buttons, icons + tooltip when they don't fit, hide Web Client buttons ---- */
const ICO={"Edit":"✎","编辑":"✎","Delete":"🗑","删除":"🗑","Link":"🔗","连接":"🔗","Share Rules":"⇄","共享规则":"⇄","UserTags":"🏷","User Tags":"🏷","用户标签":"🏷","User Address Book":"📒","用户地址簿":"📒","Reset password":"🔑","Reset Password":"🔑","重设密码":"🔑","重置密码":"🔑","Disable":"⏸","Enable":"▶","禁用":"⏸","启用":"▶"};
function tables(){document.querySelectorAll(".app-main .el-table__cell .cell .el-button").forEach(b=>{if(b.dataset.adpT)return;const t=b.textContent.trim();b.dataset.adpT=t;b.title=t;
  if(/^(Web Client|Share By Web Client|Web客户端|通过Web客户端分享|Web 客户端)$/i.test(t)||/web\s*client|web客户端/i.test(t))b.classList.add("adp-hidden");
  const s=b.querySelector("span");if(s&&!s.querySelector(".adp-i"))s.insertAdjacentHTML("afterbegin",'<i class="adp-i">'+(ICO[t]||t.charAt(0))+'</i>')});
 document.querySelectorAll(".app-main td.adp-act .cell").forEach(c=>{c.classList.remove("adp-ico");if(c.scrollWidth>c.clientWidth+1)c.classList.add("adp-ico")});
 document.querySelectorAll(".app-main .list-query").forEach(q=>{const n=q.nextElementSibling;if(n&&n.classList.contains("el-card"))n.classList.add("adp-joined");q.classList.add("adp-tbar")})}
window.addEventListener("resize",deb(tables,150));
/* other list pages: no Filter button, search as you type, tidy buttons */
function autoFilter(){const f=document.querySelector(".app-main .list-query .el-form");if(!f||f.closest(".adp-hidden"))return;
 const b=[...f.querySelectorAll("button")].find(x=>/^(筛选|Filter)$/.test(x.textContent.trim()));if(!b)return;b.classList.add("adp-hidden");f.classList.add("adp-af");
 if(f.dataset.adpAf)return;f.dataset.adpAf="1";
 const sig=()=>[...f.querySelectorAll("input")].map(i=>i.value).join("\u0001")+"|"+[...f.querySelectorAll(".el-select__selected-item:not(.el-select__input-wrapper),.el-select__tags-text")].map(x=>x.textContent).join("\u0001");
 let last=sig();const fire=deb(()=>{if(!document.body.contains(f))return;const s=sig();if(s!==last){last=s;b.click()}},450);
 f.addEventListener("input",fire,true);f.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();last=sig();b.click()}},true);
 const iv=setInterval(()=>{if(!document.body.contains(f))return clearInterval(iv);fire()},600)}

function menu(){const m=document.querySelector(".el-menu");if(!m||document.getElementById("adp-menu"))return;
 const li=document.createElement("li");li.id="adp-menu";li.className="el-menu-item";li.setAttribute("role","menuitem");
 li.innerHTML='<span>'+T('密码推送','Password push')+'</span><span class="adp-new">'+T('新','New')+'</span>';li.onclick=()=>window.open("/_admin/adoluna-password","_blank");m.appendChild(li)}
/* ---- accounts: forgot-password link, forced change of a temporary password, admin reset (Jay 10/29) ---- */
const ACCT="/_admin/adoluna-api/acct/";const onLogin=()=>/^#\/login(\?|$)/.test(location.hash||"");
function loginLink(){if(!onLogin()||document.getElementById("adp-forgot"))return;const f=document.querySelector(".el-form");if(!f)return;
 const d=document.createElement("div");d.id="adp-forgot";d.innerHTML='<a href="/_admin/adoluna-forgot">'+T("忘记密码？","Forgot password?")+'</a>';f.appendChild(d)}
let mcTok=null;
async function mustCheck(){const t=tok();if(!t||onLogin()||t===mcTok)return;mcTok=t;
 let r;try{r=await api(ACCT+"me")}catch(e){return}if(!r.must_change||document.getElementById("adp-mc"))return;
 const w=document.createElement("div");w.id="adp-mc";w.innerHTML='<div class="adp-dlgm" style="z-index:5000"></div><div class="adp-dlg" style="z-index:5001;width:420px"><h4>'+T("请先修改临时密码","Change your temporary password")+'</h4><div class="adp-hint" style="line-height:1.6">'+T("你正在用管理员给的临时密码。设置自己的新密码后才能继续使用后台。新密码 8–32 位，需有大写、小写字母和数字。","You signed in with a temporary password from an administrator. Set your own password to continue. 8-32 characters with upper- and lower-case letters and a number.")+'</div>'+
 '<input id="mc0" type="password" autocomplete="current-password" placeholder="'+T("临时密码","Temporary password")+'"><input id="mc1" type="password" autocomplete="new-password" placeholder="'+T("新密码","New password")+'"><input id="mc2" type="password" autocomplete="new-password" placeholder="'+T("再输入一次新密码","Confirm new password")+'"><div id="mce" style="color:#C62828;font-size:13px;min-height:18px;margin-top:6px"></div>'+
 '<div style="text-align:right;margin-top:12px"><button class="btn" data-x>'+T("退出登录","Sign out")+'</button> <button class="btn pri" data-o>'+T("保存新密码","Save new password")+'</button></div></div>';document.body.appendChild(w);
 const q=s=>w.querySelector(s),err=m=>q("#mce").textContent=m,out=()=>{localStorage.removeItem("access_token");location.hash="#/login";location.reload()};
 q("[data-x]").onclick=out;q("[data-o]").onclick=async()=>{const a=q("#mc0").value,b=q("#mc1").value,c=q("#mc2").value;if(b!==c)return err(T("两次输入的新密码不一致","The two new passwords don't match."));if(a===b)return err(T("新密码不能和临时密码一样","Choose a password different from the temporary one."));
  const r=await fetch(API+"/user/changeCurPwd",{method:"POST",headers:{"api-token":tok(),"Content-Type":"application/json","Accept-Language":EN()?"en":"zh"},body:JSON.stringify({old_password:a,new_password:b})});const j=await r.json().catch(()=>({}));
  if(j.code!==0)return err(j.message||("HTTP "+r.status));alert(T("密码已修改，请用新密码重新登录。","Password changed. Please sign in again with your new password."));out()}}
const onUsers=()=>/^#\/user\/index/.test(location.hash||"");
function userReset(){if(!onUsers()||document.getElementById("adp-ur"))return;const card=document.querySelector(".app-main .el-card .el-form, .app-main .el-card");if(!card)return;
 const b=document.createElement("button");b.id="adp-ur";b.className="el-button el-button--warning";b.style.margin="0 0 12px";b.textContent=T("重设密码","Reset password");card.parentNode.insertBefore(b,card);
 b.onclick=async()=>{let us=[];try{us=(await api("/user/list?page=1&page_size=1000")).list||[]}catch(e){return toast(e.message,1)}
  dialog('<h4>'+T("重设员工密码","Reset a staff password")+'</h4><div class="adp-hint" style="line-height:1.6">'+T("会生成一个临时密码（只显示一次），对方用它登录后必须先改成自己的密码。对方在所有设备上的登录会被退出。","Creates a temporary password (shown once). The person must change it after signing in. They are signed out on every device.")+'</div><select id="adp-uu" style="width:100%;margin-top:12px">'+us.map(u=>'<option value="'+u.id+'">'+esc(u.username)+(u.nickname?' ('+esc(u.nickname)+')':'')+(u.email?' · '+esc(u.email):'')+'</option>').join("")+'</select>',
  async()=>{const id=Number(document.getElementById("adp-uu").value);const r=await fetch(ACCT+"admin-reset",{method:"POST",headers:{"api-token":tok(),"Content-Type":"application/json"},body:JSON.stringify({user_id:id,lang:LG()})});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("HTTP "+r.status));
   const u=us.find(x=>x.id===id)||{};dialog('<h4>'+T("临时密码","Temporary password")+'</h4><div style="line-height:1.7">'+T("账号","Account")+' <b>'+esc(u.username)+'</b><br><code id="adp-tp" style="font-size:18px;user-select:all">'+esc(j.temp_password)+'</code></div><div class="adp-hint" style="margin-top:8px">'+T("只显示这一次。请当面或用电话告诉对方，不要发在群里。","Shown only this once. Tell the person in person or by phone; don't post it in a chat.")+'</div>',
    async()=>{try{await navigator.clipboard.writeText(j.temp_password)}catch(e){}},T("复制并关闭","Copy and close"))},T("生成临时密码","Create temporary password"))}}
const EN_FIX={"MyPeer":"My Devices","Groups":"User Groups","Oauth":"OAuth","Peers":"Devices","Address Book Name":"Address Book Groups","Device Group Manage":"Device Groups","Server Cmd":"Server Commands","Share Record":"Share Records","User Token":"User Tokens","Login Log":"Login Logs","Connection Log":"Connection Logs","File Log":"File Logs"};
function relabel(){if(!EN())return;document.querySelectorAll(".el-menu span,.el-menu .el-sub-menu__title,.tags-view-item,.el-breadcrumb__inner,.el-tag__content,.el-tabs__item").forEach(e=>{if(e.children.length)return;const v=EN_FIX[e.textContent.trim()];if(v)e.textContent=v})}
let t=0,curLang=LG();function langCheck(){const l=LG();if(l===curLang)return;curLang=l;["adp-forgot","adp-ur"].forEach(i=>{const x=document.getElementById(i);if(x)x.remove()});const a=document.getElementById("adp-menu");if(a)a.remove();const d=document.getElementById("adp-d");if(d)d.remove();const o=document.getElementById("adp");if(o)o.remove()}
new MutationObserver(()=>{clearTimeout(t);t=setTimeout(()=>{langCheck();menu();mount();relabel();autoFilter();tables();loginLink();mustCheck();userReset()},60)}).observe(document.documentElement,{childList:true,subtree:true});
window.addEventListener("hashchange",()=>setTimeout(mount,100));window.addEventListener("popstate",()=>setTimeout(mount,100));
})();
