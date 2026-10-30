/* Adoluna Support admin: compact device list + detail drawer + 密码推送 menu. Injected by adoluna-pwpush. */
(function(){
"use strict";
const LG=()=>String(localStorage.getItem("lang")||navigator.language||"zh-CN"),EN=()=>!/^zh/i.test(LG()),T=(z,e)=>EN()?e:z;
const API="/api/admin",PW="/_admin/adoluna-api/password",tok=()=>localStorage.getItem("access_token")||"";
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(u,body){const r=await fetch(u.startsWith("/_")?u:API+u,{method:body?"POST":"GET",headers:{"api-token":tok(),"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
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
function mount(){const main=document.querySelector(".app-main")||document.querySelector(".el-main");if(!main)return;
 if(!isPeerPage()){const o=document.getElementById("adp");if(o){o.remove();[...main.children].forEach(c=>c.classList.remove("adp-hidden"))}return}
 [...main.children].forEach(c=>{if(c.id!=="adp")c.classList.add("adp-hidden")});if(document.getElementById("adp"))return;
 root=document.createElement("div");root.id="adp";root.innerHTML='<div class="card"><div class="bar"><input id="adp-q" placeholder="'+T('设备 ID / 主机名','Device ID / hostname')+'" style="width:200px"><input id="adp-u" placeholder="'+T('用户名','Username')+'" style="width:150px"><button class="btn pri" id="adp-s">'+T('搜索','Search')+'</button><button class="btn txt" id="adp-r">'+T('重置','Reset')+'</button></div><table><thead><tr><th>'+T('设备 ID','Device ID')+'</th><th>'+T('主机名','Hostname')+'</th><th>'+T('系统','OS')+'</th><th>'+T('状态','Status')+'</th><th>'+T('用户名','Username')+'</th></tr></thead><tbody id="adp-tb"><tr><td colspan=5>'+T('读取中…','Loading…')+'</td></tr></tbody></table><div id="adp-n" style="margin-top:14px;color:#6B7280;font-size:13px"></div></div>';
 main.appendChild(root);const q=root.querySelector("#adp-q"),u=root.querySelector("#adp-u");
 root.querySelector("#adp-s").onclick=()=>{S.q=q.value.trim();S.u=u.value.trim();renderList()};q.onkeydown=u.onkeydown=e=>{if(e.key==="Enter")root.querySelector("#adp-s").click()};
 root.querySelector("#adp-r").onclick=()=>{q.value=u.value="";S.q=S.u="";renderList()};
 load().then(renderList).catch(e=>{root.querySelector("#adp-tb").innerHTML='<tr><td colspan=5 style="color:#C62828">'+T('读取失败：','Failed to load: ')+esc(e.message)+'</td></tr>'})}
function menu(){const m=document.querySelector(".el-menu");if(!m||document.getElementById("adp-menu"))return;
 const li=document.createElement("li");li.id="adp-menu";li.className="el-menu-item";li.setAttribute("role","menuitem");
 li.innerHTML='<span>'+T('密码推送','Password push')+'</span><span class="adp-new">'+T('新','New')+'</span>';li.onclick=()=>window.open("/_admin/adoluna-password","_blank");m.appendChild(li)}
/* ---- accounts: forgot-password link, forced change of a temporary password, admin reset (Jay 10/29) ---- */
const ACCT="/_admin/adoluna-api/acct/";const onLogin=()=>/^#\/login/.test(location.hash||"");
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
new MutationObserver(()=>{clearTimeout(t);t=setTimeout(()=>{langCheck();menu();mount();relabel();loginLink();mustCheck();userReset()},60)}).observe(document.documentElement,{childList:true,subtree:true});
window.addEventListener("hashchange",()=>setTimeout(mount,100));window.addEventListener("popstate",()=>setTimeout(mount,100));
})();
