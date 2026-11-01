/* Adoluna Support admin: compact device list + detail drawer + 密码推送 menu. Injected by adoluna-pwpush. */
(function(){
"use strict";
const LG=()=>String(localStorage.getItem("lang")||navigator.language||"zh-CN"),EN=()=>!/^zh/i.test(LG()),T=(z,e)=>EN()?e:z;
const ACL="/_admin/adoluna-api/",API="/api/admin",PW="/_admin/adoluna-api/password",tok=()=>localStorage.getItem("access_token")||"";
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
let S={acl:null,list:[],pw:null,users:[],groups:[],q:"",u:""},root=null;
async function load(){const d=await api("/peer/list?page=1&page_size=1000");S.list=(d.list||[]);
 try{S.pw=await api(PW)}catch(e){S.pw=null}
 try{S.acl=await api(ACL+"acl")}catch(e){S.acl=null}
 try{S.dev=await api(ACL+"dev-list")}catch(e){S.dev={}}
 try{S.users=(await api("/user/list?page=1&page_size=1000")).list||[]}catch(e){S.users=[]}
 try{S.groups=(await api("/device_group/list?page=1&page_size=1000")).list||[]}catch(e){S.groups=[]}}
function rows(){const q=S.q.toLowerCase(),u=S.u.toLowerCase();return S.list.filter(p=>(!q||(p.id+" "+p.hostname+" "+p.alias).toLowerCase().includes(q))&&(!u||String(p.username).toLowerCase().includes(u)))
 .sort((a,b)=>(isOn(b)-isOn(a))||(b.last_online_time-a.last_online_time))}
function renderList(){const r=rows();root.querySelector("#adp-tb").innerHTML=r.map(p=>'<tr class="r" data-id="'+esc(p.row_id)+'"><td class="adp-mono">'+esc(p.id)+'</td><td>'+esc(p.alias||p.hostname)+'</td><td>'+esc(((S.dev||{})[p.id]||{}).os_label||osName(p.os))+devBadge(p.id)+'</td><td>'+stHtml(p)+'</td><td>'+esc(p.username||"—")+'</td></tr>').join("")||'<tr><td colspan=5 style="text-align:center;color:#6B7280">'+T("没有设备","No devices")+'</td></tr>';
 root.querySelector("#adp-n").textContent=T("共 "+r.length+" 台",r.length+(r.length===1?" device":" devices"));root.querySelectorAll("tr.r").forEach(t=>t.onclick=()=>openD(S.list.find(p=>String(p.row_id)===t.dataset.id)))}
function pwBox(p){const s=S.pw;if(!s)return'<span class="adp-hint">'+T("无法读取推送状态（需在内网用管理员登录）","Can't read push status (sign in as admin on the internal network)")+'</span>';
 const id=String(p.id),c=(s.custom||{})[id],x=(s.devices||{})[id];
 const src=c?'<span class="adp-cust">🔒 '+T("单独密码","Own password")+' v'+esc(c.v)+'</span>　<span class="adp-hint">'+esc(c.at||"")+'</span>　<a href="#" id="adp-pwclr">'+T("改回批量密码","Use batch password")+'</a>'
  :(x?'<span class="adp-tag">'+T("批量密码","Batch password")+' v'+esc(s.version||0)+'</span>':'<span class="adp-tag i">'+T("不在批量推送对象里","Not in batch targets")+'</span>');
 return T('这台设备：','This device: ')+src+'<br>'+T('推送状态：','Push status: ')+pwState(p,x)+
 '<br><span class="adp-hint">'+(c?T('批量推送会跳过这台设备。','Batch pushes skip this device.'):T('「重置密码」可以给这台设备单独设一个密码，以后批量推送会跳过它。','"Reset password" gives this device its own password; batch pushes then skip it.'))+'</span>'}
/* bilingual names like "测试设备 / Test Devices": show only the current language (Jay 10/30) */
const LN=n=>{n=String(n==null?"":n);const m=n.split(/\s+\/\s+/);if(m.length!==2)return n;const cj=/[\u3400-\u9fff]/;const[z,e]=cj.test(m[0])&&!cj.test(m[1])?m:cj.test(m[1])&&!cj.test(m[0])?[m[1],m[0]]:[null,null];return z===null?n:T(z,e)};
const AUD={"签发":["签发","Ticket issued"],"账号不可用":["账号不可用","Account disabled"],"目标设备为仅控制端":["目标设备为仅控制端","Target is controller-only"],"目标设备未开启账号免密登录":["目标设备未开启账号免密登录","Passwordless is off on the target"],"未知设备":["未知设备","Unknown device"],"未授权该设备":["未授权该设备","Not authorized for this device"]};
function audR(r){r=String(r||"");let m;if(AUD[r])return T(...AUD[r]);if((m=r.match(/^拒绝: (.*)$/)))return T("拒绝：","Denied: ")+(AUD[m[1]]?T(...AUD[m[1]]):m[1]);
 if((m=r.match(/^设置角色 免密=(\d) 仅控制端=(\d)$/)))return T(r,"Role set: passwordless="+m[1]+", controller-only="+m[2]);if((m=r.match(/^删除授权 (.*)$/)))return T(r,"Grant removed "+m[1]);if((m=r.match(/^添加授权 (.*)$/)))return T(r,"Grant added "+m[1]);return r}
function aclU(id){const u=S.acl&&S.acl.users.find(x=>String(x.id)===String(id));return u?(u.nickname||u.username):"#"+id}
function aclG(id,dg){const l=S.acl?(dg?S.acl.device_groups:S.acl.groups):[];const g=l.find(x=>String(x.id)===String(id));return g?LN(g.name):"#"+id}
const SUBJ=k=>k==="user"?T("员工","Staff"):T("用户组","User group");
function aclBox(p){const a=S.acl;if(!a)return'<span class="adp-hint">'+T("无法读取免密设置（需在内网用管理员登录）","Can't read passwordless settings (sign in as admin on the internal network)")+'</span>';
 const r=a.roles.find(x=>x.peer_id===String(p.id))||{accept:0,ctrl_only:0};
 const gs=a.grants.filter(g=>(g.tgt_kind==="peer"&&g.tgt===String(p.id))||(g.tgt_kind==="dgroup"&&String(p.group_id)===g.tgt));
 return'<label class="adp-ck"><input type="checkbox" id="adp-acc"'+(r.accept?" checked":"")+'> '+T("允许已授权的员工账号免密码连接这台设备","Let authorized staff accounts connect to this device without a password")+'</label>'+
 '<label class="adp-ck"><input type="checkbox" id="adp-co"'+(r.ctrl_only?" checked":"")+'> '+T("仅控制端（拒绝所有连入，适用于员工电脑）","Controller only (refuse all incoming connections; for staff computers)")+'</label>'+
 '<div style="margin:10px 0 4px"><b>'+T("已授权","Authorized")+'</b></div>'+(gs.length?gs.map(g=>'<div class="adp-gr">'+SUBJ(g.subj_kind)+' '+esc(g.subj_kind==="user"?aclU(g.subj):aclG(g.subj))+
 (g.tgt_kind==="dgroup"?' <span class="adp-hint">'+T("（通过设备分组「"+esc(aclG(g.tgt,1))+"」）","(via device group \""+esc(aclG(g.tgt,1))+"\")")+'</span>':'')+' <a href="#" data-gdel="'+g.id+'">'+T("移除","Remove")+'</a></div>').join(""):'<div class="adp-hint">'+T("还没有授权任何员工","No staff authorized yet")+'</div>')+
 '<div style="margin-top:8px;display:flex;gap:6px"><select id="adp-gs">'+a.users.filter(u=>u.status===1).map(u=>'<option value="user:'+u.id+'">'+SUBJ("user")+' '+esc(u.nickname||u.username)+'</option>').join("")+
 a.groups.map(g=>'<option value="group:'+g.id+'">'+SUBJ("group")+' '+esc(LN(g.name))+'</option>').join("")+'</select><button class="btn" id="adp-ga">'+T("授权","Authorize")+'</button></div>'+
 '<div style="margin:12px 0 4px"><b>'+T("最近免密记录","Recent passwordless activity")+'</b> <a href="#" id="adp-al">'+T("查看","View")+'</a></div><div id="adp-au"></div>'}
async function auditHtml(q){const d=await api(ACL+"audit?limit=200"+(q||""));return d.items.length?'<table class="adp-at"><tr><th>'+T("时间","Time")+'</th><th>'+T("账号","Account")+'</th><th>'+T("控制端 ID","Controller ID")+'</th><th>'+T("设备 ID","Device ID")+'</th><th>IP</th><th>'+T("结果","Result")+'</th></tr>'+
 d.items.map(i=>'<tr><td>'+esc(fmtET(i.ts))+'</td><td>'+esc(i.user==="(后台)"?T("(后台)","(admin)"):i.user)+'</td><td class="adp-mono">'+esc(i.my_id)+'</td><td class="adp-mono">'+esc(i.peer_id)+'</td><td>'+esc(i.ip)+'</td><td>'+esc(audR(i.result))+'</td></tr>').join("")+'</table>':'<span class="adp-hint">'+T("没有记录","No records")+'</span>'}
function wireAcl(w,p){const box=w.querySelector("#adp-acl");if(!S.acl)return;
 const role=async()=>{try{await api(ACL+"role",{peer_id:String(p.id),accept:box.querySelector("#adp-acc").checked?1:0,ctrl_only:box.querySelector("#adp-co").checked?1:0});S.acl=await api(ACL+"acl");toast(T("已保存，设备下次心跳（约 15 秒）生效","Saved. Takes effect on the device's next heartbeat (about 15 seconds)"))}catch(e){toast(T("保存失败：","Save failed: ")+e.message,1)}};
 box.querySelector("#adp-acc").onchange=role;box.querySelector("#adp-co").onchange=role;
 const re=async()=>{S.acl=await api(ACL+"acl");box.innerHTML=aclBox(p);wireAcl(w,p)};
 box.querySelector("#adp-ga").onclick=async()=>{const [k,v]=box.querySelector("#adp-gs").value.split(":");try{await api(ACL+"grant",{subj_kind:k,subj:v,tgt_kind:"peer",tgt:String(p.id)});toast(T("已授权","Authorized"));re()}catch(e){toast(T("失败：","Failed: ")+e.message,1)}};
 box.querySelectorAll("[data-gdel]").forEach(a=>a.onclick=async e=>{e.preventDefault();try{await api(ACL+"grant",{delete:Number(a.dataset.gdel)});toast(T("已移除","Removed"));re()}catch(x){toast(T("失败：","Failed: ")+x.message,1)}});
 box.querySelector("#adp-al").onclick=async e=>{e.preventDefault();box.querySelector("#adp-au").innerHTML=await auditHtml("&peer_id="+encodeURIComponent(p.id))}}
function sel(name,cur,opts){return'<select data-k="'+name+'"><option value="0">—</option>'+opts.map(o=>'<option value="'+o.id+'"'+(Number(cur)===Number(o.id)?" selected":"")+'>'+esc(o.n)+'</option>').join("")+'</select>'}
function openD(p){if(!p)return;const w=document.createElement("div");w.id="adp-d";
 const inp=(k,l)=>'<label>'+l+'</label><input data-k="'+k+'" value="'+esc(p[k])+'">';
 const ro=(l,v)=>'<label>'+l+'</label><span>'+esc(v||"—")+'</span>';
 w.innerHTML='<div class="mask"></div><div class="drw"><div class="dh"><h2><span><span class="adp-mono">'+esc(p.id)+'</span> · '+esc(p.alias||p.hostname)+'</span><span class="x">✕</span></h2><div class="sub">'+stHtml(p)+' · '+esc(osName(p.os))+devBadge(p.id)+'</div></div>'+
 '<div class="db"><div class="sec"><h3>'+T('基本信息','Basic info')+'</h3><div class="f">'+inp("hostname",T("主机名","Hostname"))+inp("username",T("用户名","Username"))+inp("alias",T("别名","Alias"))+
 '<label>'+T('所属用户','Owner')+'</label>'+(S.users.length?sel("user_id",p.user_id,S.users.map(u=>({id:u.id,n:u.nickname||u.username}))):'<input data-k="user_id" value="'+esc(p.user_id)+'">')+
 '<label>'+T('分组','Group')+'</label>'+(S.groups.length?sel("group_id",p.group_id,S.groups.map(g=>({id:g.id,n:LN(g.name)}))):'<input data-k="group_id" value="'+esc(p.group_id)+'">')+'</div></div>'+
 '<div class="sec"><h3>'+T('设备信息','Device info')+'</h3><div class="f">'+ro(T("设备 ID","Device ID"),p.id)+ro(T("系统","OS"),p.os)+ro(T("处理器","CPU"),p.cpu)+ro(T("内存","Memory"),p.memory)+ro(T("最后 IP","Last IP"),p.last_online_ip)+ro(T("客户端版本","Client version"),p.version)+ro("UUID",p.uuid)+ro(T("最后在线","Last online"),fmtET(p.last_online_time))+'</div></div>'+
 '<div class="sec" id="adp-devsec"><h3>'+T('设备管理','Device management')+'</h3><div class="pc" id="adp-dev"><span class="adp-hint">'+T('读取中…','Loading…')+'</span></div></div>'+
 '<div class="sec"><h3>'+T('免密登录','Passwordless')+'</h3><div class="pc" id="adp-acl">'+aclBox(p)+'</div></div><div class="sec"><h3>'+T('固定密码','Permanent password')+'</h3><div class="pc" id="adp-pw">'+pwBox(p)+'</div></div></div>'+
 '<div class="df"><button class="btn del" data-a="del">'+T('删除','Delete')+'</button><span style="flex:1"></span><button class="btn rr" data-a="pw">'+T('重置密码','Reset password')+'</button><a class="btn" data-a="link" href="adolunasupport://'+encodeURIComponent(p.id)+'" style="display:inline-flex;align-items:center;text-decoration:none">'+T('远程连接','Connect')+'</a><button class="btn" data-a="fav">'+T('☆ 收藏','☆ Favorite')+'</button><button class="btn pri" data-a="save">'+T('保存修改','Save')+'</button></div></div>';
 root.appendChild(w);const close=()=>w.remove();w.querySelector(".mask").onclick=close;w.querySelector(".x").onclick=close;
 const A=a=>w.querySelector('[data-a="'+a+'"]');wireAcl(w,p);devLoad(w,p);
 A("save").onclick=async()=>{const b={};["row_id","id","cpu","hostname","memory","os","username","uuid","version","user_id","group_id","alias"].forEach(k=>b[k]=p[k]);
  w.querySelectorAll("[data-k]").forEach(e=>{const k=e.dataset.k;b[k]=(k==="user_id"||k==="group_id")?Number(e.value)||0:e.value.trim()});
  try{await api("/peer/update",b);toast(T("已保存","Saved"));Object.assign(p,b);close();renderList()}catch(e){toast(T("保存失败：","Save failed: ")+e.message,1)}};
 const pwRe=()=>{const b=w.querySelector("#adp-pw");b.innerHTML=pwBox(p);const l=b.querySelector("#adp-pwclr");if(l)l.onclick=e=>{e.preventDefault();pwClear(String(p.id),pwRe)}};pwRe();
 A("pw").onclick=()=>ownPwDialog(p.id,p.alias||p.hostname,pwRe);
 A("fav").onclick=async()=>{try{await api("/address_book/create",{id:p.id,hostname:p.hostname,username:p.username,alias:p.alias,platform:osName(p.os),user_id:p.user_id||0,tags:[]});toast(T("已收藏到地址簿","Added to address book"))}catch(e){toast(T("收藏失败：","Favorite failed: ")+e.message,1)}};
 A("del").onclick=()=>dialog('<h4>'+T('删除设备','Delete device')+'</h4><div style="line-height:1.7">'+T('确定从后台删除设备 <b class="adp-mono">'+esc(p.id)+'</b>（'+esc(p.hostname)+'）？','Delete device <b class="adp-mono">'+esc(p.id)+'</b> ('+esc(p.hostname)+') from the admin?')+'<br><span class="adp-hint">'+T('请输入设备 ID 以确认。设备下次上线会重新出现在列表。','Type the device ID to confirm. The device reappears in the list next time it comes online.')+'</span></div>',
  async()=>{await api("/peer/delete",{row_id:p.row_id});toast(T("已删除","Deleted"));close();S.list=S.list.filter(x=>x!==p);renderList()},T("删除","Delete"),String(p.id))}
/* ---- b79: Device Owner / Windows device management (commands go out on the next heartbeat, ~15 s) ---- */
const CAPR={os_unsupported:["系统过旧（Win7/8 不支持）","OS too old (Win 7/8 unsupported)"],needs_pro:["家庭版不支持锁定，需升级专业版","Home edition: needs Pro upgrade for lock"]};
const capTag=r=>r&&CAPR[r]?'<span class="adp-amb">'+T(...CAPR[r])+'</span>':"";
function devBadge(id){const d=(S.dev||{})[id];if(!d)return"";const r=d.cap&&d.cap.reason;return(r==="os_unsupported"?' '+capTag(r):"")+(d.do? ' <span class="adp-do" style="display:inline-block;padding:0 5px;margin-left:4px;border-radius:4px;background:#1F4E79;color:#fff;font-size:11px;font-weight:600;line-height:16px" title="'+T("设备所有者模式（DO）","Device Owner mode")+'">DO</span>':"")+(d.kiosk?' <span class="adp-kq" title="'+T("已锁定到收银 App","Locked to the POS app")+'">🔒</span>':"")}
const CMDN={ping:["连通测试","Ping"],info:["刷新信息","Refresh info"],reboot:["重启","Reboot"],install:["安装","Install"],update:["更新","Update"],kiosk_lock:["锁定到 App","Lock to app"],kiosk_unlock:["解除锁定","Unlock"],policy:["设备策略","Device policy"],unlock_code:["设置解锁码","Set unlock code"],otp_secret:["开启一次性码","Enable one-time codes"]};
function cmdSt(c){if(c.acked_at)return c.ok?'<span class="adp-ok">✓ '+esc(c.msg||"")+'</span>':'<span class="adp-err">✕ '+esc(c.msg||"")+'</span>';if(c.e<Date.now()/1000)return'<span class="adp-hint">'+T("已过期","Expired")+'</span>';return c.delivered_at?'<span class="adp-amb">'+T("已下发，等待结果","Sent, waiting")+'</span>':'<span class="adp-amb">'+T("排队中（设备上线后下发）","Queued (sent when online)")+'</span>'}
async function devLoad(w,p){const box=w.querySelector("#adp-dev");if(!box)return;let d;try{d=await api(ACL+"dev?id="+encodeURIComponent(p.id))}catch(e){box.innerHTML='<span class="adp-hint">'+T("无法读取（需在内网用管理员登录）","Can't read (sign in as admin on the internal network)")+'</span>';return}
 const i=d.info||{},and="sdk" in i||/android/i.test(p.os||""),win=/windows/i.test(i.platform||p.os||""),mac=/mac/i.test(i.platform||p.os||""),own=!!i.is_owner,pol=(()=>{try{return JSON.parse(i.policy||"{}")}catch(e){return{}}})();
 const kv=(l,v)=>'<label>'+l+'</label><span>'+esc(v==null||v===""?"—":v)+'</span>';
 const yes=v=>v?T("是","Yes"):T("否","No");
 let h='<div class="f">'+(d.info?'':'<span class="adp-hint" style="grid-column:1/-1">'+T("设备还没上报（需要 b79 及以上版本客户端并在线）","Not reported yet (needs a b79+ client online)")+'</span>');
 if(d.info){h+=kv(T("型号","Model"),i.model)+kv(T("系统","OS"),i.os)+kv(T("序列号","Serial"),i.serial)+(and?kv(T("电量","Battery"),i.battery!=null?i.battery+"%"+(i.charging?T(" 充电中"," charging"):""):"")+kv(T("设备所有者（DO）","Device Owner"),yes(own))+kv(T("锁定状态","Lock state"),i.kiosk?T("已锁定","Locked")+(i.lock_state?" ("+i.lock_state+")":""):(i.unlocked_until?T("临时解锁至 ","Unlocked until ")+fmtET(Math.round(i.unlocked_until/1000)):T("未锁定","Not locked")))+kv(T("解锁码 / 一次性码","Unlock code / OTP"),yes(i.unlock_code_set)+" / "+yes(i.otp_set)):"")+(win?(()=>{const wv=i.win||{},cp=i.cap||{},km=cp.kiosk;const KM={full:T("完整锁定（分配访问）","Full lock (Assigned Access)"),basic:T("基础锁定（自动启动+退出重开+隐藏任务栏+屏蔽快捷键）","Basic lock (auto-start, relaunch on exit, hidden taskbar, hotkeys blocked)"),none:T("不支持","Not supported")};
   return kv(T("系统","Windows"),"Windows "+(wv.major||"?")+" "+(wv.edition||"")+(wv.edition_id?" ("+wv.edition_id+")":""))+kv(T("版本 / 内部版本","Version / build"),(wv.display_version||"—")+" / "+(wv.build||"?")+(wv.ubr?"."+wv.ubr:""))+
   '<label>'+T("推送安装","Push install")+'</label><span>'+(cp.install?T("支持","Supported"):T("不支持","Not supported")+" "+capTag(cp.reason))+'</span>'+
   '<label>'+T("单软件锁定","Single-app lock")+'</label><span>'+esc(KM[km]||"—")+(km==="none"?" "+capTag(cp.reason):"")+'</span>'+
   kv(T("锁定状态","Lock state"),i.kiosk?T("已锁定","Locked")+(i.kiosk_mode==="basic"?T("（基础）"," (basic)"):""):T("未锁定","Not locked"))})():"")+kv(T("磁盘可用","Disk free"),i.disk)+kv(T("上报时间","Reported"),rel(d.ts))}
 let whyTag="";h+='</div><div class="adp-dbtn">';
 const B=(a,l,dis)=>'<button class="btn" data-dc="'+a+'"'+(dis?' disabled style="opacity:.4;cursor:not-allowed" title="'+esc(dis)+'"':'')+'>'+l+'</button>';
 const nd=T("需要设备所有者（DO）模式","Needs Device Owner mode");
 if(and){h+=B("install",T("推送安装 APK","Push APK"))+B("reboot",T("重启","Reboot"),own?0:nd)+B("kiosk_lock",T("锁定到收银 App","Lock to POS app"),own?0:nd)+B("kiosk_unlock",T("解除锁定","Unlock"),own?0:nd)+B("policy",T("设备策略…","Device policy…"),own?0:nd)+B("unlock_code",T("生成解锁码","New unlock code"))+B("otp_secret",d.otp?T("重置一次性码密钥","Reset OTP secret"):T("开启一次性码","Enable one-time codes"))+(d.otp?B("otp",T("查看当前一次性码","Show current one-time code")):"")}
 else if(win){const cp=i.cap||{},old=cp.reason==="os_unsupported",why=old?T(...CAPR.os_unsupported):cp.reason==="needs_pro"?T(...CAPR.needs_pro):!d.info?T("设备未上报","Not reported yet"):0;
  whyTag=cp.reason?capTag(cp.reason):"";const inOk=d.info&&cp.install,kOk=d.info&&cp.kiosk&&cp.kiosk!=="none";
  h+=B("install",T("推送安装 exe/msi","Push exe/msi"),inOk?0:(why||T("不支持","Not supported")))+B("update",T("静默更新客户端","Silent client update"),old?why:0)+B("reboot",T("重启","Reboot"),old?why:0)+B("kiosk_lock",cp.kiosk==="basic"?T("开启基础锁定","Basic lock"):T("开启单软件锁定","Single-app lock"),kOk?0:(why||T("不支持","Not supported")))+B("kiosk_unlock",T("解除锁定","Unlock"),kOk||i.kiosk?0:(why||T("不支持","Not supported")))}
 else if(mac){h+=B("update",T("后台更新客户端","Background client update"))+B("reboot",T("重启","Reboot"))}
 else h+=B("update",T("更新客户端","Update client"))+B("reboot",T("重启","Reboot"));
 h+=B("info",T("刷新信息","Refresh info"))+'</div>'+(whyTag?'<div style="margin-top:6px">'+whyTag+'</div>':'');
 if(d.cmds.length)h+='<table class="adp-at" style="margin-top:8px"><tr><th>#</th><th>'+T("命令","Command")+'</th><th>'+T("时间","Time")+'</th><th>'+T("结果","Result")+'</th></tr>'+d.cmds.slice(0,8).map(c=>'<tr><td>'+c.n+'</td><td>'+esc(T(...(CMDN[c.k]||[c.k,c.k])))+'</td><td>'+esc(fmtET(c.created_at))+'</td><td>'+cmdSt(c)+'</td></tr>').join("")+'</table>';
 box.innerHTML=h;
 const send=async(k,pl,okMsg)=>{try{const r=await api(ACL+"dev/cmd",{id:String(p.id),k:k,p:pl||{}});toast(okMsg||T("已排队，设备约 15 秒内收到","Queued — the device picks it up within ~15 s"));setTimeout(()=>devLoad(w,p),400);return r}catch(e){toast(e.message,1)}};
 const inst=k=>dialog('<h4>'+(and?T("推送安装 APK","Push APK"):T("推送安装","Push install"))+'</h4><div class="f2"><label>'+T("下载地址","Download URL")+'</label><input id="adp-u" placeholder="https://…" style="width:100%"><label>SHA-256</label><input id="adp-h" class="adp-mono" style="width:100%">'+(k==="update"?'<label>'+T("内部版本号（如 80）","Build number (e.g. 80)")+'</label><input id="adp-b" type="number" min="1" style="width:120px">'+(win?'<div class="adp-hint">'+T("Windows 更新只能用 .msi","Windows updates need the .msi")+'</div>':''):'')+(win&&k==="install"?'<label>'+T("静默参数（exe，可选）","Silent args (exe, optional)")+'</label><input id="adp-a" placeholder="/S" style="width:100%">':'')+'</div><div class="adp-hint" style="margin-top:8px">'+(and?(own?T("DO 设备会静默安装。","Device Owner devices install silently."):T("非 DO 设备会弹出安装确认，需要店员点“安装”。","Non-DO devices show an install prompt the merchant must accept.")):T("文件校验 SHA-256 后才会执行。","The file runs only if its SHA-256 matches."))+'</div>',
  async()=>{const pl={url:document.getElementById("adp-u").value.trim(),sha256:document.getElementById("adp-h").value.trim().toLowerCase()};const a=document.getElementById("adp-a");if(a&&a.value.trim())pl.args=a.value.trim();const bn=document.getElementById("adp-b");if(bn)pl.build=Number(bn.value);await send(k,pl)},T("下发","Send"));
 box.querySelectorAll("[data-dc]").forEach(b=>b.onclick=()=>{const k=b.dataset.dc;
  if(k==="install"||(k==="update"&&!and))return inst(k);
  if(k==="reboot")return dialog('<h4>'+T("重启设备","Reboot device")+'</h4><div>'+T("设备会在约 20 秒后重启，远程连接会断开。","The device reboots in ~20 s; remote sessions disconnect.")+'</div>',()=>send("reboot",{}),T("重启","Reboot"));
  if(k==="kiosk_lock"){const dflt=and?(pol.pkg||""):"";return dialog('<h4>'+T("锁定到 App","Lock to app")+'</h4><div class="f2"><label>'+(and?T("App 包名","App package"):T("App（AUMID 或 exe 路径）","App (AUMID or exe path)"))+'</label><input id="adp-pk" value="'+esc(dflt)+'" style="width:100%"></div><div class="adp-hint" style="margin-top:8px">'+(and?T("锁定后店员只能使用该 App 和远程支持；可用 7 次点击左上角 + 解锁码 / 一次性码临时解锁 30 分钟。","Only that app and remote support stay usable; tap the top-left corner 7× and enter the unlock / one-time code for 30 minutes."):(((i.cap||{}).kiosk==="basic")?T("Win10 专业版：基础锁定（开机自动启动、退出自动重开、隐藏任务栏、屏蔽 Win 键/任务管理器）。远程支持不受影响。","Win10 Pro: basic lock (auto-start, relaunch on exit, hidden taskbar, Win key / Task Manager blocked). Remote support keeps working."):T("Win11：配置分配访问并重启，远程支持保持可用。","Win11: sets up Assigned Access and reboots; remote support keeps working.")))+'</div>',
   ()=>send("kiosk_lock",and?{pkg:document.getElementById("adp-pk").value.trim()}:{app:document.getElementById("adp-pk").value.trim()}),T("锁定","Lock"))}
  if(k==="policy"){const c=(f,l,def)=>'<label class="adp-ck"><input type="checkbox" data-pf="'+f+'"'+((pol[f]!==undefined?pol[f]:def)?" checked":"")+'>'+l+'</label>';
   return dialog('<h4>'+T("设备策略","Device policy")+'</h4><div style="display:grid;gap:6px">'+c("no_uninstall",T("禁止卸载远程支持","Block uninstalling remote support"),true)+c("block_apps",T("禁止安装 / 卸载其他 App","Block installing / removing other apps"))+c("block_factory_reset",T("禁止恢复出厂设置","Block factory reset"))+c("block_settings",T("锁定 Wi-Fi / 蓝牙 / 日期 / 安全模式","Lock Wi-Fi / Bluetooth / date / safe boot"))+c("block_debug",T("禁用 USB 调试（会失去 adb 兜底）","Disable USB debugging (removes the adb fallback)"))+c("screen_on",T("插电时屏幕常亮","Keep screen on while plugged in"))+c("no_screensaver",T("关闭屏保","Disable screensaver"))+
    '<label>'+T("亮度（-1 = 不管，0–255）","Brightness (-1 = leave, 0–255)")+' <input id="adp-br" type="number" min="-1" max="255" value="'+esc(pol.brightness!=null?pol.brightness:-1)+'" style="width:70px"></label><label>'+T("系统更新","System updates")+' <select id="adp-su"><option value="">'+T("不管","Leave")+'</option><option value="postpone"'+(pol.sysupdate==="postpone"?" selected":"")+'>'+T("推迟 30 天","Postpone 30 days")+'</option><option value="auto"'+(pol.sysupdate==="auto"?" selected":"")+'>'+T("自动安装","Install automatically")+'</option><option value="window:120-300"'+(String(pol.sysupdate||"").startsWith("window")?" selected":"")+'>'+T("凌晨 2–5 点安装","Install 2–5 am")+'</option></select></label></div>',
    ()=>{const pl={};document.querySelectorAll("[data-pf]").forEach(e=>pl[e.dataset.pf]=e.checked);pl.brightness=Number(document.getElementById("adp-br").value);const su=document.getElementById("adp-su").value;if(su)pl.sysupdate=su;return send("policy",pl)},T("下发","Send"))}
  if(k==="unlock_code")return dialog('<h4>'+T("生成解锁码","New unlock code")+'</h4><div>'+T("生成一个新的 6 位解锁码（旧的作废）。只显示一次，请记下后交给店员。","Creates a new 6-digit unlock code (the old one stops working). Shown once — note it down for the merchant.")+'</div>',async()=>{const r=await send("unlock_code",{},T("已生成","Created"));if(r&&r.unlock_code)dialog('<h4>'+T("解锁码","Unlock code")+'</h4><div style="font-size:28px;letter-spacing:4px" class="adp-mono">'+esc(r.unlock_code)+'</div><div class="adp-hint">'+T("设备上线后生效。","Takes effect when the device is online.")+'</div>',()=>{},T("我已记下","Done"))},T("生成","Create"));
  if(k==="otp")return api(ACL+"dev/otp?id="+encodeURIComponent(p.id)).then(o=>dialog('<h4>'+T("当前一次性码","Current one-time code")+'</h4><div style="font-size:28px;letter-spacing:4px" class="adp-mono">'+esc(o.otp)+'</div><div class="adp-hint">'+T("有效至 ","Valid until ")+esc(fmtET(o.valid_until))+T("，只能用一次，离线也能验证。","; single use, works offline.")+'</div>',()=>{},T("关闭","Close"))).catch(e=>toast(e.message,1));
  if(k==="kiosk_unlock")return send("kiosk_unlock",{});
  return send(k,{})})}
const isPeerPage=()=>/\/user\/peer(\?|$)/.test(location.hash||"")||/\/user\/peer\/?$/.test(location.pathname);
const abView=()=>{const h=location.hash||"";return /^#\/user\/addressBook(\?|$)/.test(h)?"absys":/^#\/my\/address_book(\?|$)/.test(h)?"abmy":null};
const curView=()=>(/[?&]adm=/.test(location.hash||"")||(/^#\/?$/.test(location.hash||"#/")&&(document.documentElement.dataset.adm||(history.state&&history.state.adm)||sessionStorage.getItem("adoluna_adm"))))?null:isPwPage()?"pw":isPeerPage()?"peer":/^#\/(\?.*)?$/.test(location.hash||"")?"mypeer":abView();
const deb=(f,ms)=>{let k=0;return(...a)=>{clearTimeout(k);k=setTimeout(()=>f(...a),ms||300)}};
function mount(){const main=document.querySelector(".app-main")||document.querySelector(".el-main");if(!main)return;const v=curView();if(v!=="pw"){const mi=document.getElementById("adp-menu");if(mi)mi.classList.remove("is-active")}
 const o=document.getElementById("adp");if(o&&o.dataset.v!==v){o.remove();[...main.children].forEach(c=>c.classList.remove("adp-hidden"))}
 if(!v)return;[...main.children].forEach(c=>{if(c.id!=="adp")c.classList.add("adp-hidden")});if(document.getElementById("adp"))return;
 if(v==="pw")return pwMount(main);if(v==="mypeer")return myMount(main);if(v!=="peer")return abMount(main,v);
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
const colName=e=>{const c=Number(e.collection_id)||0;if(!c)return DEF_AB();if(e.collection&&e.collection.name)return LN(e.collection.name);const x=AB.cols.find(k=>Number(k.id)===c);return x?LN(x.name):"#"+c};
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
 return(withAll?'<option value="">'+T("全部地址簿","All address books")+'</option>':'')+'<option value="0"'+(String(cur)==="0"?" selected":"")+'>'+esc(DEF_AB())+'</option>'+own.map(c=>'<option value="'+c.id+'"'+(String(cur)===String(c.id)?" selected":"")+'>'+esc(LN(c.name))+(AB.v==="absys"&&uid==null?' ('+esc(uName(c.user_id))+')':'')+'</option>').join("")}
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
 '<div class="sec"><h3>'+T("设备信息","Device info")+'</h3><div class="f">'+ro(T("设备 ID","Device ID"),e.id)+ro(T("主机名","Hostname"),d?d.hostname:e.hostname)+ro(T("门店","Store"),d&&LN(d.group))+ro(T("系统","OS"),d?d.os:e.platform)+ro(T("最后在线","Last online"),d?fmtET(d.last_online_time):"")+'</div>'+(d?'':'<div class="adp-hint" style="margin-top:10px">'+T("这个 ID 不在系统设备列表里（可能是旧记录）。","This ID isn't in the device list (probably an old entry).")+'</div>')+'</div></div>'+
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
 const show=()=>{const r=devSearch(i.value);l.innerHTML=r.map(d=>'<div class="adp-pi" data-id="'+esc(d.id)+'"><span class="adp-mono">'+esc(d.id)+'</span><span class="n">'+esc(devName(d)||"—")+'</span><span class="g">'+esc(LN(d.group)||T("未分组","No store"))+'</span><span class="s">'+stHtml(d)+'</span></div>').join("")||'<div class="adp-hint" style="padding:10px">'+(AB.devs.length?T("没有匹配的设备","No matching devices"):T("读不到设备列表","Couldn't load the device list"))+'</div>';
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
 picker(dl.querySelector("#adp-pk"),d=>{pick=d;ok.disabled=false;dl.querySelector("#adp-asel").innerHTML=T("已选：","Selected: ")+'<b class="adp-mono">'+esc(d.id)+'</b> · '+esc(devName(d))+(d.group?' · '+esc(LN(d.group)):'')})}
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

/* ---- 密码推送 inside the admin (Jay 10/30 batch 4): batch password + targets by device / device group / address book; per-device passwords are skipped ---- */
const isPwPage=()=>/^#\/\?(.*&)?adp=pw(&|$)/.test(location.hash||"");
let PWV={st:null,opt:null,q:"",f:""};
const pwAb=a=>a.default?T(a.owner+" 的默认地址簿",a.owner+"'s default book"):LN(a.name)+(a.owner?" ("+a.owner+")":"");
function pwScopeCount(sc,opt){if(!opt)return 0;if(sc.all)return opt.devices.length;const P=new Set(sc.peers),G=new Set(sc.dgroups),B=new Set(sc.abs);
 return opt.devices.filter(d=>P.has(d.id)||G.has(d.group_id)||d.abs.some(k=>B.has(k))).length}
function pwScopeHtml(sc,opt){if(sc.all)return'<span class="adp-chip">'+T("全部设备","All devices")+'</span>';const o=opt||{dgroups:[],abs:[]};
 const ch=[...sc.dgroups.map(g=>{const x=o.dgroups.find(k=>k.id===g);return'🏬 '+(x?LN(x.name):"#"+g)}),...sc.abs.map(a=>{const x=o.abs.find(k=>k.key===a);return'📒 '+(x?pwAb(x):a)}),...sc.peers.map(p=>'🖥 '+p)];
 return ch.length?ch.map(c=>'<span class="adp-chip">'+esc(c)+'</span>').join(" "):'<span class="adp-tag i">'+T("还没选推送对象","No targets yet")+'</span>'}
function pwState(d,x){if(!x)return'—';if(x.have==null)return'<span class="adp-hint">'+T("未上报（旧版或未上线）","Not reported (old build or offline)")+'</span>';
 return x.want&&x.have>=x.want?'<span class="adp-tag">'+T("已生效","Applied")+' v'+esc(x.have)+'</span>':'<span class="adp-amb" style="margin-left:0">'+T("待生效","Pending")+(x.want?' v'+esc(x.want):'')+'</span>'}
function pwRows(){const st=PWV.st,opt=PWV.opt;if(!st||!opt)return[];const D=st.devices||{},q=PWV.q;
 return opt.devices.filter(d=>D[d.id]).filter(d=>!q||qMatch(q,hay([d.id,d.name,d.group]))).filter(d=>{const x=D[d.id];
  return!PWV.f||(PWV.f==="custom"?x.custom:PWV.f==="pending"?!x.custom&&!(x.have!=null&&x.have>=x.want):!x.custom&&x.have!=null&&x.have>=x.want)})
  .sort((a,b)=>(D[b.id].custom-D[a.id].custom)||(isOn(b)-isOn(a))||a.id.localeCompare(b.id))}
function pwRender(){const R=document.getElementById("adp");if(!R||R.dataset.v!=="pw")return;const st=PWV.st;if(!st)return;
 R.querySelector("#adp-sum").innerHTML='<div class="adp-kv"><span>'+T("批量密码","Batch password")+'</span><b>'+(st.version?'v'+esc(st.version):T("还没设置过","Never set"))+'</b><span class="adp-hint">'+esc(st.updated_at||"")+'</span></div>'+
  '<div class="adp-kv"><span>'+T("推送对象","Targets")+'</span><div>'+pwScopeHtml(st.scope,PWV.opt)+'</div></div>'+
  '<div class="adp-kv"><span>'+T("覆盖","Coverage")+'</span><div>'+T("批量推送 <b>"+st.target_count+"</b> 台","Batch goes to <b>"+st.target_count+"</b> devices")+(st.skipped_custom.length?'　<span class="adp-cust">'+T("跳过 "+st.skipped_custom.length+" 台（有单独密码）","Skips "+st.skipped_custom.length+" (own password)")+'</span>':'')+
  '　<span class="adp-hint">'+T("单独密码共 "+Object.keys(st.custom).length+" 台","Own password: "+Object.keys(st.custom).length+" devices")+'</span></div></div>';
 const r=pwRows(),D=st.devices;
 R.querySelector("#adp-tb").innerHTML=r.map(d=>{const x=D[d.id],c=st.custom[d.id];return'<tr class="r" data-id="'+esc(d.id)+'"><td class="adp-mono">'+esc(d.id)+'</td><td>'+esc(d.name||"—")+'</td><td>'+esc(LN(d.group)||T("未分组","No store"))+'</td><td>'+stHtml(d)+'</td><td>'+
  (x.custom?'<span class="adp-cust">🔒 '+T("单独密码","Own password")+' v'+esc(c.v)+'</span>':'<span class="adp-chip">'+T("批量密码","Batch")+(st.version?' v'+esc(st.version):'')+'</span>')+'</td><td>'+pwState(d,x)+'</td><td class="act">'+
  (x.custom?'<button class="btn sm" data-clr="'+esc(d.id)+'">'+T("改回批量密码","Use batch password")+'</button>':'<button class="btn sm" data-own="'+esc(d.id)+'">'+T("设单独密码","Set own password")+'</button>')+'</td></tr>'}).join("")||
  '<tr><td colspan=7 style="text-align:center;color:#6B7280">'+(PWV.q||PWV.f?T("没有匹配的设备","No matching devices"):T("还没有推送对象，点「选择推送对象」","No targets yet. Click Choose targets."))+'</td></tr>';
 R.querySelector("#adp-n").textContent=T("共 "+r.length+" 台",r.length+(r.length===1?" device":" devices"));
 R.querySelectorAll("[data-clr]").forEach(b=>b.onclick=e=>{e.stopPropagation();pwClear(b.dataset.clr)});
 R.querySelectorAll("[data-own]").forEach(b=>b.onclick=e=>{e.stopPropagation();const d=PWV.opt.devices.find(k=>k.id===b.dataset.own);ownPwDialog(d.id,d.name,s=>{PWV.st=s;pwRender()})})}
async function pwLoad(){const[a,b]=await Promise.all([api(PW),api(ACL+"pw-options")]);PWV.st=a;PWV.opt=b;S.pw=a}
function pwMount(main){document.querySelectorAll(".el-menu .is-active").forEach(e=>e.classList.remove("is-active"));const mi=document.getElementById("adp-menu");if(mi)mi.classList.add("is-active");PWV.q=PWV.f="";const R=document.createElement("div");R.id="adp";R.dataset.v="pw";
 R.innerHTML='<div class="card"><div class="adp-pwh"><div><h3>'+T("密码推送","Password push")+'</h3><div class="adp-hint">'+T("给一批设备统一设一个固定密码。在线设备约 15 秒内生效，离线设备下次上线时生效。服务器只保存密码的哈希。需要 76 版及以后。设了单独密码的设备不会被批量推送覆盖。","Set one permanent password for a batch of devices. Online devices update in about 15 seconds, offline ones when they next come online. The server stores only a hash. Needs build 76 or later. Devices with their own password are never overwritten by a batch push.")+'</div></div></div><div id="adp-sum" class="adp-sum">'+T("加载中…","Loading…")+'</div></div>'+
 '<div class="card" style="margin-top:12px"><div class="bar"><input id="adp-q" placeholder="'+T("搜索设备 ID / 名称 / 门店","Search ID / name / store")+'"><select id="adp-f"><option value="">'+T("全部状态","All statuses")+'</option><option value="pending">'+T("待生效","Pending")+'</option><option value="ok">'+T("已生效","Applied")+'</option><option value="custom">'+T("单独密码","Own password")+'</option></select><button class="btn txt" id="adp-r">'+T("重置","Reset")+'</button><span style="flex:1"></span><span id="adp-upd" class="adp-hint"></span><span id="adp-n" class="adp-hint"></span>'+
 '<button class="btn" id="adp-tg">'+T("选择推送对象","Choose targets")+'</button><button class="btn pri" id="adp-push">'+T("设置新密码并推送","Set new password & push")+'</button></div>'+
 '<table><thead><tr><th>'+T("设备 ID","Device ID")+'</th><th>'+T("名称","Name")+'</th><th>'+T("门店","Store")+'</th><th>'+T("在线","Online")+'</th><th>'+T("密码来源","Password")+'</th><th>'+T("推送状态","Push status")+'</th><th class="act">'+T("操作","Actions")+'</th></tr></thead><tbody id="adp-tb"><tr><td colspan=7 style="text-align:center;color:#6B7280">'+T("加载中…","Loading…")+'</td></tr></tbody></table></div>';
 main.appendChild(R);const q=R.querySelector("#adp-q"),f=R.querySelector("#adp-f");q.oninput=deb(()=>{PWV.q=q.value.trim();pwRender()},200);f.onchange=()=>{PWV.f=f.value;pwRender()};
 R.querySelector("#adp-r").onclick=()=>{q.value="";f.value="";PWV.q=PWV.f="";pwRender()};R.querySelector("#adp-tg").onclick=pwPicker;R.querySelector("#adp-push").onclick=pwPush;
 const iv=setInterval(()=>{if(!R.isConnected)return clearInterval(iv);if(document.querySelector(".adp-dlg"))return;pwLoad().then(()=>{pwRender();const u=R.querySelector("#adp-upd");if(u)u.textContent=T("自动刷新 · ","Auto-refresh · ")+new Date().toLocaleTimeString(EN()?"en-US":"zh-CN",{timeZone:"America/New_York",hour12:false})+" ET"}).catch(()=>{})},10000);
 pwLoad().then(pwRender).catch(e=>{R.querySelector("#adp-sum").innerHTML='<span style="color:#C62828">'+T("读取失败（需在内网用管理员登录）：","Failed to load (sign in as admin on the internal network): ")+esc(e.message)+'</span>';R.querySelector("#adp-tb").innerHTML=""})}
function pwPicker(){const opt=PWV.opt;if(!opt)return;const sc=JSON.parse(JSON.stringify(PWV.st.scope));let dq="";
 const cnt=(key,fn)=>opt.devices.filter(fn).length;
 const body=()=>'<label class="adp-ck adp-allck"><input type="checkbox" id="adp-pall"'+(sc.all?" checked":"")+'> <b>'+T("全部设备","All devices")+'</b> <span class="adp-hint">（'+opt.devices.length+'）</span></label>'+
  '<div class="adp-tgs'+(sc.all?' off':'')+'"><div class="adp-tgc"><h5>🏬 '+T("设备组（门店）","Device groups (stores)")+'</h5>'+(opt.dgroups.map(g=>'<label class="adp-ck"><input type="checkbox" data-g="'+esc(g.id)+'"'+(sc.dgroups.includes(g.id)?" checked":"")+'> '+esc(LN(g.name))+' <span class="adp-hint">（'+cnt(0,d=>d.group_id===g.id)+'）</span></label>').join("")||'<span class="adp-hint">'+T("没有设备组","No device groups")+'</span>')+'</div>'+
  '<div class="adp-tgc"><h5>📒 '+T("地址簿","Address books")+'</h5>'+(opt.abs.map(a=>'<label class="adp-ck"><input type="checkbox" data-b="'+esc(a.key)+'"'+(sc.abs.includes(a.key)?" checked":"")+'> '+esc(pwAb(a))+' <span class="adp-hint">（'+cnt(0,d=>d.abs.includes(a.key))+'）</span></label>').join("")||'<span class="adp-hint">'+T("没有地址簿","No address books")+'</span>')+'</div>'+
  '<div class="adp-tgc wide"><h5>🖥 '+T("单台设备","Individual devices")+' <span class="adp-hint" id="adp-pdn"></span></h5><input class="adp-pq" id="adp-pdq" placeholder="'+T("输入设备 ID、名称或门店搜索","Search by device ID, name or store")+'" autocomplete="off"><div class="adp-pl" id="adp-pdl"></div></div></div>'+
  '<div class="adp-tot" id="adp-ptot"></div>';
 dialog('<h4>'+T("选择推送对象","Choose push targets")+'</h4><div class="adp-hint" style="margin-bottom:10px">'+T("可以同时勾选设备组、地址簿和单台设备，推送对象是它们的合集。设备组和地址簿按推送时的成员计算，以后新加入的设备也会收到。","Tick any mix of device groups, address books and single devices; the targets are their union. Groups and books are evaluated live, so devices added later get the password too.")+'</div><div id="adp-pb"></div>',
  async()=>{const s=await api(PW,{scope:sc});PWV.st=s;S.pw=s;pwRender();toast(T("推送对象已保存","Targets saved"))},T("保存推送对象","Save targets"));
 const dl=[...document.querySelectorAll(".adp-dlg")].pop();dl.classList.add("wide","xl");const pb=dl.querySelector("#adp-pb");
 const tot=()=>{const n=pwScopeCount(sc,opt),cu=PWV.st.custom||{};const sk=opt.devices.filter(d=>cu[d.id]&&(sc.all||sc.peers.includes(d.id)||sc.dgroups.includes(d.group_id)||d.abs.some(k=>sc.abs.includes(k)))).length;
  dl.querySelector("#adp-ptot").innerHTML=T("将推送到 <b>"+(n-sk)+"</b> 台设备","Will push to <b>"+(n-sk)+"</b> devices")+(sk?'　<span class="adp-cust">'+T("跳过 "+sk+" 台（有单独密码）","skipping "+sk+" with their own password")+'</span>':'');
  const pdn=dl.querySelector("#adp-pdn");if(pdn)pdn.textContent=sc.peers.length?T("已选 "+sc.peers.length+" 台","("+sc.peers.length+" selected)"):""};
 const list=()=>{const l=dl.querySelector("#adp-pdl");if(!l)return;const r=opt.devices.filter(d=>!dq||qMatch(dq,hay([d.id,d.name,d.group]))).sort((a,b)=>(sc.peers.includes(b.id)-sc.peers.includes(a.id))||(isOn(b)-isOn(a))||a.id.localeCompare(b.id)).slice(0,40);
  l.innerHTML=r.map(d=>'<label class="adp-pi ck"><input type="checkbox" data-p="'+esc(d.id)+'"'+(sc.peers.includes(d.id)?" checked":"")+'><span class="adp-mono">'+esc(d.id)+'</span><span class="n">'+esc(d.name||"—")+(PWV.st.custom[d.id]?' <span class="adp-cust">🔒</span>':'')+'</span><span class="g">'+esc(LN(d.group)||T("未分组","No store"))+'</span><span class="s">'+stHtml(d)+'</span></label>').join("")||'<div class="adp-hint" style="padding:10px">'+T("没有匹配的设备","No matching devices")+'</div>';
  l.querySelectorAll("[data-p]").forEach(i=>i.onchange=()=>{const k=i.dataset.p;sc.peers=i.checked?[...new Set(sc.peers.concat(k))]:sc.peers.filter(x=>x!==k);tot()})};
 const draw=()=>{pb.innerHTML=body();pb.querySelector("#adp-pall").onchange=e=>{sc.all=e.target.checked;draw()};
  pb.querySelectorAll("[data-g]").forEach(i=>i.onchange=()=>{const k=i.dataset.g;sc.dgroups=i.checked?[...new Set(sc.dgroups.concat(k))]:sc.dgroups.filter(x=>x!==k);tot()});
  pb.querySelectorAll("[data-b]").forEach(i=>i.onchange=()=>{const k=i.dataset.b;sc.abs=i.checked?[...new Set(sc.abs.concat(k))]:sc.abs.filter(x=>x!==k);tot()});
  const qi=pb.querySelector("#adp-pdq");qi.value=dq;qi.oninput=deb(()=>{dq=qi.value.trim();list()},150);list();tot()};draw()}
function pwPush(){const st=PWV.st;if(!st)return;
 dialog('<h4>'+T("设置新的批量密码并推送","Set a new batch password and push")+'</h4><div class="adp-hint" style="line-height:1.7">'+T("推送对象：","Targets: ")+pwScopeHtml(st.scope,PWV.opt)+'<br>'+T("将推送到 <b>"+st.target_count+"</b> 台设备","Goes to <b>"+st.target_count+"</b> devices")+(st.skipped_custom.length?'，<span class="adp-cust">'+T("跳过 "+st.skipped_custom.length+" 台（有单独密码）","skipping "+st.skipped_custom.length+" with their own password")+'</span>':'')+'</div>'+
  '<input id="adp-np1" type="password" autocomplete="new-password" placeholder="'+T("新密码（8–64 位）","New password (8–64 characters)")+'"><input id="adp-np2" type="password" autocomplete="new-password" placeholder="'+T("再输入一次","Type it again")+'"><div class="adp-hint" id="adp-npm" style="margin-top:8px"></div>',
  async()=>{const a=document.getElementById("adp-np1").value,b=document.getElementById("adp-np2").value;if(a.length<8||a.length>64)throw new Error(T("密码长度需 8–64 位","Password must be 8–64 characters"));if(a!==b)throw new Error(T("两次输入不一致","The two passwords don't match"));
   if(!st.target_count)throw new Error(T("还没有推送对象","No targets yet"));const s=await api(PW,{password:a});PWV.st=s;S.pw=s;pwRender();toast(T("已推送，在线设备约 15 秒内更新","Pushed. Online devices update in about 15 seconds"))},T("推送","Push"))}
function ownPwDialog(id,name,done){
 dialog('<h4>'+T("设置这台设备的单独密码","Set this device's own password")+'</h4><div style="line-height:1.7">'+T('设备 <b class="adp-mono">'+esc(id)+'</b>'+(name?'（'+esc(name)+'）':''),'Device <b class="adp-mono">'+esc(id)+'</b>'+(name?' ('+esc(name)+')':''))+'</div>'+
  '<input id="adp-op1" type="password" autocomplete="new-password" placeholder="'+T("新密码（8–64 位）","New password (8–64 characters)")+'"><input id="adp-op2" type="password" autocomplete="new-password" placeholder="'+T("再输入一次","Type it again")+'">'+
  '<div class="adp-hint" style="margin-top:10px;line-height:1.7">'+T("只改这一台。设好后这台设备会标记为「单独密码」，以后的批量推送会跳过它，直到你点「改回批量密码」。在线设备约 15 秒内生效（需 76 版及以后）。","Only this device changes. It is marked \"Own password\" and batch pushes skip it until you click \"Use batch password\". Online devices update in about 15 seconds (build 76 or later).")+'</div>',
  async()=>{const a=document.getElementById("adp-op1").value,b=document.getElementById("adp-op2").value;if(a.length<8||a.length>64)throw new Error(T("密码长度需 8–64 位","Password must be 8–64 characters"));if(a!==b)throw new Error(T("两次输入不一致","The two passwords don't match"));
   const s=await api(ACL+"device-pw",{id:String(id),password:a,confirm:b});S.pw=s;toast(T("已设置单独密码","Own password set"));done&&done(s)},T("保存密码","Save password"))}
function pwClear(id,done){const st=S.pw||PWV.st;
 dialog('<h4>'+T("改回批量密码","Use the batch password")+'</h4><div style="line-height:1.7">'+T('清除设备 <b class="adp-mono">'+esc(id)+'</b> 的单独密码？','Clear the own password of device <b class="adp-mono">'+esc(id)+'</b>?')+'<br><span class="adp-hint">'+
  (st&&st.version?T("如果它在推送对象里，会改用批量密码 v"+st.version+"，在线约 15 秒内生效；不在推送对象里的话，设备上的密码保持不变。","If it's in the targets it switches to batch password v"+st.version+" within about 15 seconds; otherwise the password on the device stays as it is."):T("还没有批量密码，设备上的密码保持不变。","There is no batch password yet, so the password on the device stays as it is."))+'</span></div>',
  async()=>{const s=await api(ACL+"device-pw",{id:String(id),clear:true});S.pw=s;if(PWV.st)PWV.st=s;toast(T("已改回批量密码","Switched to batch password"));pwRender();done&&done(s)},T("改回批量密码","Use batch password"))}
function menu(){const m=document.querySelector(".el-menu");if(!m||document.getElementById("adp-menu"))return;
 const li=document.createElement("li");li.id="adp-menu";li.className="el-menu-item";li.setAttribute("role","menuitem");
 li.innerHTML='<span>'+T('密码推送','Password push')+'</span><span class="adp-new">'+T('新','New')+'</span>';li.onclick=()=>{location.hash="#/?adp=pw"};m.appendChild(li)
 const l2=document.createElement("li");l2.id="adp-menu2";l2.className="el-menu-item";l2.setAttribute("role","menuitem");l2.innerHTML='<span>'+T('免密审计','Passwordless audit')+'</span>';
 l2.onclick=async()=>{const m=document.createElement("div");m.className="adp-dlgm";m.innerHTML='<div class="adp-dlg" style="width:880px;max-height:80vh;overflow:auto"><h4>'+T('免密登录审计（最近 200 条）','Passwordless audit (latest 200)')+'</h4><div id="adp-aud">'+T('读取中…','Loading…')+'</div><div style="text-align:right;margin-top:10px"><button class="btn">'+T('关闭','Close')+'</button></div></div>';
  document.body.appendChild(m);m.querySelector("button").onclick=()=>m.remove();try{m.querySelector("#adp-aud").innerHTML=await auditHtml("")}catch(e){m.querySelector("#adp-aud").textContent=T("读取失败（需在内网用管理员登录）","Failed to load (sign in as admin on the internal network)")}};m.appendChild(l2)}
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
let t=0,curLang=LG();function langCheck(){const l=LG();if(l===curLang)return;curLang=l;["adp-forgot","adp-ur","adp-menu2"].forEach(i=>{const x=document.getElementById(i);if(x)x.remove()});const a=document.getElementById("adp-menu");if(a)a.remove();const d=document.getElementById("adp-d");if(d)d.remove();const o=document.getElementById("adp");if(o)o.remove()}
new MutationObserver(()=>{clearTimeout(t);t=setTimeout(()=>{langCheck();menu();mount();relabel();autoFilter();tables();loginLink();mustCheck();userReset()},60)}).observe(document.documentElement,{childList:true,subtree:true});
window.addEventListener("hashchange",()=>setTimeout(mount,100));window.addEventListener("popstate",()=>setTimeout(mount,100));
/* b80: shared with the merchant drawer (merchui.js) so both show the same 设备管理 */
window.ADP_devLoad=devLoad;
})();
