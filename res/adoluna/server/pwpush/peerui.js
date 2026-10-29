/* Adoluna Support admin: compact device list + detail drawer + 密码推送 menu. Injected by adoluna-pwpush. */
(function(){
"use strict";
const ACL="/_admin/adoluna-api/",API="/api/admin",PW="/_admin/adoluna-api/password",tok=()=>localStorage.getItem("access_token")||"";
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(u,body){const r=await fetch(u.startsWith("/_")?u:API+u,{method:body?"POST":"GET",headers:{"api-token":tok(),"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||j.message||("HTTP "+r.status));if(j.code!==undefined&&j.code!==0)throw new Error(j.message||("code "+j.code));return j.data!==undefined?j.data:j}
function toast(t,err){const d=document.createElement("div");d.className="adp-toast"+(err?" e":"");d.textContent=t;document.body.appendChild(d);setTimeout(()=>d.remove(),2600)}
const fmtET=ts=>ts?new Date(ts*1000).toLocaleString("zh-CN",{timeZone:"America/New_York",hour12:false})+" ET":"—";
function rel(ts){if(!ts)return"从未";const s=Date.now()/1000-ts;if(s<3600)return Math.max(1,Math.round(s/60))+" 分钟前";if(s<86400)return Math.round(s/3600)+" 小时前";return Math.floor(s/86400)+" 天前"}
const ONLINE=300; // last_online_time is not bumped on every heartbeat
const isOn=p=>p.last_online_time&&Date.now()/1000-p.last_online_time<ONLINE;
const osName=o=>{o=String(o||"");const i=o.indexOf(" / ");return(i>=0?o.slice(i+3):o).trim()||"—"};
function stHtml(p){if(isOn(p))return'<span class="adp-dot adp-on"></span>在线';const d=(Date.now()/1000-p.last_online_time)/86400;
 return'<span title="'+esc(fmtET(p.last_online_time))+'"><span class="adp-dot adp-off"></span><span style="color:#6B7280">'+rel(p.last_online_time)+'</span></span>'+(p.last_online_time&&d>30?'<span class="adp-amb">长期离线</span>':'')}
function dialog(html,onOk,okText,needId){const m=document.createElement("div");m.className="adp-dlgm";const d=document.createElement("div");d.className="adp-dlg";
 d.innerHTML=html+(needId?'<input id="adp-cid" placeholder="输入设备 ID 确认">':'')+'<div style="text-align:right;margin-top:20px"><button class="btn" data-c>取消</button> <button class="btn pri" data-o'+(needId?' disabled':'')+'>'+okText+'</button></div>';
 document.body.append(m,d);const close=()=>{m.remove();d.remove()};d.querySelector("[data-c]").onclick=close;m.onclick=close;const ok=d.querySelector("[data-o]");
 if(needId){const i=d.querySelector("#adp-cid");i.oninput=()=>ok.disabled=i.value.trim()!==needId;i.focus()}
 ok.onclick=async()=>{ok.disabled=true;try{await onOk();close()}catch(e){toast("失败："+e.message,1);ok.disabled=false}}}
let S={acl:null,list:[],pw:null,users:[],groups:[],q:"",u:""},root=null;
async function load(){const d=await api("/peer/list?page=1&page_size=1000");S.list=(d.list||[]);
 try{S.pw=await api(PW)}catch(e){S.pw=null}
 try{S.acl=await api(ACL+"acl")}catch(e){S.acl=null}
 try{S.users=(await api("/user/list?page=1&page_size=1000")).list||[]}catch(e){S.users=[]}
 try{S.groups=(await api("/device_group/list?page=1&page_size=1000")).list||[]}catch(e){S.groups=[]}}
function rows(){const q=S.q.toLowerCase(),u=S.u.toLowerCase();return S.list.filter(p=>(!q||(p.id+" "+p.hostname+" "+p.alias).toLowerCase().includes(q))&&(!u||String(p.username).toLowerCase().includes(u)))
 .sort((a,b)=>(isOn(b)-isOn(a))||(b.last_online_time-a.last_online_time))}
function renderList(){const r=rows();root.querySelector("#adp-tb").innerHTML=r.map(p=>'<tr class="r" data-id="'+esc(p.row_id)+'"><td class="adp-mono">'+esc(p.id)+'</td><td>'+esc(p.alias||p.hostname)+'</td><td>'+esc(osName(p.os))+'</td><td>'+stHtml(p)+'</td><td>'+esc(p.username||"—")+'</td></tr>').join("")||'<tr><td colspan=5 style="text-align:center;color:#6B7280">没有设备</td></tr>';
 root.querySelector("#adp-n").textContent="共 "+r.length+" 台";root.querySelectorAll("tr.r").forEach(t=>t.onclick=()=>openD(S.list.find(p=>String(p.row_id)===t.dataset.id)))}
function pwBox(p){const s=S.pw;if(!s)return'<span class="adp-hint">无法读取推送状态（需在内网用管理员登录）</span>';
 const inAll=!s.targets.length,inT=inAll||s.targets.includes(String(p.id));
 return'后台当前密码版本：<b>v'+esc(s.version||0)+'</b>　<span class="adp-hint">'+esc(s.updated_at||"还没有设置过")+'</span><br>这台设备：'+(inT?'<span class="adp-tag">'+(inAll?'已在推送范围（全部设备）':'已在推送范围')+'</span>':'<span class="adp-tag i">不在推送范围</span>')+
 '<br><span class="adp-hint">「重置密码」会把后台当前的固定密码推送到这台设备（需 76 版及以后）。</span>'}
function uName(id){const u=S.acl&&S.acl.users.find(x=>String(x.id)===String(id));return u?(u.nickname||u.username):"#"+id}
function gName(id,dg){const l=S.acl?(dg?S.acl.device_groups:S.acl.groups):[];const g=l.find(x=>String(x.id)===String(id));return g?g.name:"#"+id}
function aclBox(p){const a=S.acl;if(!a)return'<span class="adp-hint">无法读取免密设置（需在内网用管理员登录）</span>';
 const r=a.roles.find(x=>x.peer_id===String(p.id))||{accept:0,ctrl_only:0};
 const gs=a.grants.filter(g=>(g.tgt_kind==="peer"&&g.tgt===String(p.id))||(g.tgt_kind==="dgroup"&&String(p.group_id)===g.tgt));
 return'<label class="adp-ck"><input type="checkbox" id="adp-acc"'+(r.accept?" checked":"")+'> 允许已授权的员工账号免密码连接这台设备</label>'+
 '<label class="adp-ck"><input type="checkbox" id="adp-co"'+(r.ctrl_only?" checked":"")+'> 仅控制端（拒绝所有连入，适用于员工电脑）</label>'+
 '<div style="margin:10px 0 4px"><b>已授权</b></div>'+(gs.length?gs.map(g=>'<div class="adp-gr">'+(g.subj_kind==="user"?"员工 ":"用户组 ")+esc(g.subj_kind==="user"?uName(g.subj):gName(g.subj))+
 (g.tgt_kind==="dgroup"?' <span class="adp-hint">（通过设备分组「'+esc(gName(g.tgt,1))+'」）</span>':'')+' <a href="#" data-gdel="'+g.id+'">移除</a></div>').join(""):'<div class="adp-hint">还没有授权任何员工</div>')+
 '<div style="margin-top:8px;display:flex;gap:6px"><select id="adp-gs">'+a.users.filter(u=>u.status===1).map(u=>'<option value="user:'+u.id+'">员工 '+esc(u.nickname||u.username)+'</option>').join("")+
 a.groups.map(g=>'<option value="group:'+g.id+'">用户组 '+esc(g.name)+'</option>').join("")+'</select><button class="btn" id="adp-ga">授权</button></div>'+
 '<div style="margin:12px 0 4px"><b>最近免密记录</b> <a href="#" id="adp-al">查看</a></div><div id="adp-au"></div>'}
async function auditHtml(q){const d=await api(ACL+"audit?limit=200"+(q||""));return d.items.length?'<table class="adp-at"><tr><th>时间</th><th>账号</th><th>控制端 ID</th><th>设备 ID</th><th>IP</th><th>结果</th></tr>'+
 d.items.map(i=>'<tr><td>'+esc(fmtET(i.ts))+'</td><td>'+esc(i.user)+'</td><td class="adp-mono">'+esc(i.my_id)+'</td><td class="adp-mono">'+esc(i.peer_id)+'</td><td>'+esc(i.ip)+'</td><td>'+esc(i.result)+'</td></tr>').join("")+'</table>':'<span class="adp-hint">没有记录</span>'}
function wireAcl(w,p){const box=w.querySelector("#adp-acl");if(!S.acl)return;
 const role=async()=>{try{await api(ACL+"role",{peer_id:String(p.id),accept:box.querySelector("#adp-acc").checked?1:0,ctrl_only:box.querySelector("#adp-co").checked?1:0});S.acl=await api(ACL+"acl");toast("已保存，设备下次心跳（约 15 秒）生效")}catch(e){toast("保存失败："+e.message,1)}};
 box.querySelector("#adp-acc").onchange=role;box.querySelector("#adp-co").onchange=role;
 const re=async()=>{S.acl=await api(ACL+"acl");box.innerHTML=aclBox(p);wireAcl(w,p)};
 box.querySelector("#adp-ga").onclick=async()=>{const [k,v]=box.querySelector("#adp-gs").value.split(":");try{await api(ACL+"grant",{subj_kind:k,subj:v,tgt_kind:"peer",tgt:String(p.id)});toast("已授权");re()}catch(e){toast("失败："+e.message,1)}};
 box.querySelectorAll("[data-gdel]").forEach(a=>a.onclick=async e=>{e.preventDefault();try{await api(ACL+"grant",{delete:Number(a.dataset.gdel)});toast("已移除");re()}catch(x){toast("失败："+x.message,1)}});
 box.querySelector("#adp-al").onclick=async e=>{e.preventDefault();box.querySelector("#adp-au").innerHTML=await auditHtml("&peer_id="+encodeURIComponent(p.id))}}
function sel(name,cur,opts){return'<select data-k="'+name+'"><option value="0">—</option>'+opts.map(o=>'<option value="'+o.id+'"'+(Number(cur)===Number(o.id)?" selected":"")+'>'+esc(o.n)+'</option>').join("")+'</select>'}
function openD(p){if(!p)return;const w=document.createElement("div");w.id="adp-d";
 const inp=(k,l)=>'<label>'+l+'</label><input data-k="'+k+'" value="'+esc(p[k])+'">';
 const ro=(l,v)=>'<label>'+l+'</label><span>'+esc(v||"—")+'</span>';
 w.innerHTML='<div class="mask"></div><div class="drw"><div class="dh"><h2><span><span class="adp-mono">'+esc(p.id)+'</span> · '+esc(p.alias||p.hostname)+'</span><span class="x">✕</span></h2><div class="sub">'+stHtml(p)+' · '+esc(osName(p.os))+'</div></div>'+
 '<div class="db"><div class="sec"><h3>基本信息</h3><div class="f">'+inp("hostname","主机名")+inp("username","用户名")+inp("alias","别名")+
 '<label>所属用户</label>'+(S.users.length?sel("user_id",p.user_id,S.users.map(u=>({id:u.id,n:u.nickname||u.username}))):'<input data-k="user_id" value="'+esc(p.user_id)+'">')+
 '<label>分组</label>'+(S.groups.length?sel("group_id",p.group_id,S.groups.map(g=>({id:g.id,n:g.name}))):'<input data-k="group_id" value="'+esc(p.group_id)+'">')+'</div></div>'+
 '<div class="sec"><h3>设备信息</h3><div class="f">'+ro("设备 ID",p.id)+ro("系统",p.os)+ro("处理器",p.cpu)+ro("内存",p.memory)+ro("最后 IP",p.last_online_ip)+ro("客户端版本",p.version)+ro("UUID",p.uuid)+ro("最后在线",fmtET(p.last_online_time))+'</div></div>'+
 '<div class="sec"><h3>免密登录</h3><div class="pc" id="adp-acl">'+aclBox(p)+'</div></div><div class="sec"><h3>固定密码</h3><div class="pc" id="adp-pw">'+pwBox(p)+'</div></div></div>'+
 '<div class="df"><button class="btn del" data-a="del">删除</button><span style="flex:1"></span><button class="btn rr" data-a="pw">重置密码</button><a class="btn" data-a="link" href="adolunasupport://'+encodeURIComponent(p.id)+'" style="display:inline-flex;align-items:center;text-decoration:none">远程连接</a><button class="btn" data-a="fav">☆ 收藏</button><button class="btn pri" data-a="save">保存修改</button></div></div>';
 root.appendChild(w);const close=()=>w.remove();w.querySelector(".mask").onclick=close;w.querySelector(".x").onclick=close;
 const A=a=>w.querySelector('[data-a="'+a+'"]');wireAcl(w,p);
 A("save").onclick=async()=>{const b={};["row_id","id","cpu","hostname","memory","os","username","uuid","version","user_id","group_id","alias"].forEach(k=>b[k]=p[k]);
  w.querySelectorAll("[data-k]").forEach(e=>{const k=e.dataset.k;b[k]=(k==="user_id"||k==="group_id")?Number(e.value)||0:e.value.trim()});
  try{await api("/peer/update",b);toast("已保存");Object.assign(p,b);close();renderList()}catch(e){toast("保存失败："+e.message,1)}};
 A("pw").onclick=()=>dialog('<h4>重置密码</h4><div style="line-height:1.7">把后台当前的固定密码（v'+esc(S.pw?S.pw.version:"?")+'）推送到设备 <b class="adp-mono">'+esc(p.id)+'</b>（'+esc(p.alias||p.hostname)+'）？</div><div class="adp-hint" style="margin-top:8px">在线设备约 15 秒内生效；离线设备下次上线时生效。</div>',
  async()=>{const r=await api("/_admin/adoluna-api/reset",{id:String(p.id)});S.pw=r;w.querySelector("#adp-pw").innerHTML=pwBox(p);toast(r.note||"已加入推送范围")},"推送密码");
 A("fav").onclick=async()=>{try{await api("/address_book/create",{id:p.id,hostname:p.hostname,username:p.username,alias:p.alias,platform:osName(p.os),user_id:p.user_id||0,tags:[]});toast("已收藏到地址簿")}catch(e){toast("收藏失败："+e.message,1)}};
 A("del").onclick=()=>dialog('<h4>删除设备</h4><div style="line-height:1.7">确定从后台删除设备 <b class="adp-mono">'+esc(p.id)+'</b>（'+esc(p.hostname)+'）？<br><span class="adp-hint">请输入设备 ID 以确认。设备下次上线会重新出现在列表。</span></div>',
  async()=>{await api("/peer/delete",{row_id:p.row_id});toast("已删除");close();S.list=S.list.filter(x=>x!==p);renderList()},"删除",String(p.id))}
const isPeerPage=()=>/\/user\/peer(\?|$)/.test(location.hash||"")||/\/user\/peer\/?$/.test(location.pathname);
function mount(){const main=document.querySelector(".app-main")||document.querySelector(".el-main");if(!main)return;
 if(!isPeerPage()){const o=document.getElementById("adp");if(o){o.remove();[...main.children].forEach(c=>c.classList.remove("adp-hidden"))}return}
 [...main.children].forEach(c=>{if(c.id!=="adp")c.classList.add("adp-hidden")});if(document.getElementById("adp"))return;
 root=document.createElement("div");root.id="adp";root.innerHTML='<div class="card"><div class="bar"><input id="adp-q" placeholder="设备 ID / 主机名" style="width:200px"><input id="adp-u" placeholder="用户名" style="width:150px"><button class="btn pri" id="adp-s">搜索</button><button class="btn txt" id="adp-r">重置</button></div><table><thead><tr><th>设备 ID</th><th>主机名</th><th>系统</th><th>状态</th><th>用户名</th></tr></thead><tbody id="adp-tb"><tr><td colspan=5>读取中…</td></tr></tbody></table><div id="adp-n" style="margin-top:14px;color:#6B7280;font-size:13px"></div></div>';
 main.appendChild(root);const q=root.querySelector("#adp-q"),u=root.querySelector("#adp-u");
 root.querySelector("#adp-s").onclick=()=>{S.q=q.value.trim();S.u=u.value.trim();renderList()};q.onkeydown=u.onkeydown=e=>{if(e.key==="Enter")root.querySelector("#adp-s").click()};
 root.querySelector("#adp-r").onclick=()=>{q.value=u.value="";S.q=S.u="";renderList()};
 load().then(renderList).catch(e=>{root.querySelector("#adp-tb").innerHTML='<tr><td colspan=5 style="color:#C62828">读取失败：'+esc(e.message)+'</td></tr>'})}
function menu(){const m=document.querySelector(".el-menu");if(!m||document.getElementById("adp-menu"))return;
 const li=document.createElement("li");li.id="adp-menu";li.className="el-menu-item";li.setAttribute("role","menuitem");
 li.innerHTML='<span>密码推送</span><span class="adp-new">新</span>';li.onclick=()=>window.open("/_admin/adoluna-password","_blank");m.appendChild(li);
 const l2=document.createElement("li");l2.id="adp-menu2";l2.className="el-menu-item";l2.setAttribute("role","menuitem");l2.innerHTML='<span>免密审计</span>';
 l2.onclick=async()=>{const m=document.createElement("div");m.className="adp-dlgm";m.innerHTML='<div class="adp-dlg" style="width:880px;max-height:80vh;overflow:auto"><h4>免密登录审计（最近 200 条）</h4><div id="adp-aud">读取中…</div><div style="text-align:right;margin-top:10px"><button class="btn">关闭</button></div></div>';
  document.body.appendChild(m);m.querySelector("button").onclick=()=>m.remove();try{m.querySelector("#adp-aud").innerHTML=await auditHtml("")}catch(e){m.querySelector("#adp-aud").textContent="读取失败（需在内网用管理员登录）"}};m.appendChild(l2)}
let t=0;new MutationObserver(()=>{clearTimeout(t);t=setTimeout(()=>{menu();mount()},60)}).observe(document.documentElement,{childList:true,subtree:true});
window.addEventListener("hashchange",()=>setTimeout(mount,100));window.addEventListener("popstate",()=>setTimeout(mount,100));
})();
