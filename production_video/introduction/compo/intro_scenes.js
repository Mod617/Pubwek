// ---------- préparation (vidéo d'introduction) ----------
const X=window.EXTRA||{};
const PICS=['img_burger.jpg','img_adjara.jpg','img_jus.jpg','img_lea.jpg','img_tech.jpg','img_beaute.jpg','img_electro.jpg','img_wheke.jpg','img_plomb.jpg'];
// mur de statuts
let wh='';for(let i=0;i<24;i++){const im=PICS[i%PICS.length];const v=150+((i*397)%2400);
 wh+=`<div style="position:relative;width:330px;height:587px;border-radius:28px;overflow:hidden;box-shadow:0 14px 30px rgba(0,0,0,.5);background:#111">
  <img src="${im}" style="width:100%;height:100%;object-fit:cover">
  <div style="position:absolute;left:14px;right:14px;top:12px;height:5px;border-radius:3px;background:rgba(255,255,255,.4)"><div style="width:${30+(i*23)%60}%;height:100%;background:#fff;border-radius:3px"></div></div>
  <div style="position:absolute;left:0;right:0;bottom:0;height:90px;background:linear-gradient(0deg,rgba(0,0,0,.75),rgba(0,0,0,0))"></div>
  <div class="vw" data-v="${v}" style="position:absolute;left:18px;bottom:16px;display:flex;align-items:center;gap:8px;color:#fff;font:700 26px Inter"><svg width="30" height="30" viewBox="0 0 24 24"><path d="M12 5C6 5 2 12 2 12s4 7 10 7 10-7 10-7-4-7-10-7z" fill="none" stroke="#fff" stroke-width="2"/><circle cx="12" cy="12" r="3.2" fill="#fff"/></svg><span>0</span></div></div>`;}
$('s1wall').innerHTML=wh;
// billets qui jaillissent (« de l'argent ? »)
const NOTES=[];for(let i=0;i<14;i++){const d=document.createElement('div');d.style.cssText='position:absolute;width:360px;height:170px;margin:-85px 0 0 -180px;opacity:0;filter:drop-shadow(0 14px 18px rgba(0,0,0,.45))';
  d.innerHTML=BILLET(i%3===2?500:1000);$('s1notes').appendChild(d);
  const a=(i/14)*Math.PI*2+.3;NOTES.push({d,a,v:900+(i*83)%400,ph:i*1.3,sp:(i%2?1:-1)*(30+(i*11)%25),sc:.7+((i*7)%10)/30});}
// carrousel de commerces (scène 3)
$('s3car').innerHTML=PICS.map((p,i)=>`<img class="car" src="${p}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0">`).join('');
// +10 F (scène 4)
const PL=[];[[230,1060],[850,1120],[200,1330],[880,1380]].forEach(([x,y])=>{const p=document.createElement('div');p.className='plus';p.textContent='+10 F';p.style.left=(x-70)+'px';p.style.top=y+'px';p.style.fontSize='70px';$('s4fx').appendChild(p);PL.push(p);});
// mur final
$('s8wall').innerHTML=PICS.map(p=>`<div style="overflow:hidden"><img src="${p}" style="width:100%;height:100%;object-fit:cover"></div>`).join('');

// ---------- scènes ----------
function S1(t){
  const Z=X.money||4.9;
  const hk=P(t,0,.18);const hshake=t<.6?Math.sin(t*80)*14*(1-P(t,0,.6)):0;const hout=P(t,.85,.25);
  st('s1hey',{o:(1-hout),s:lerp(1.6,1,eo(hk))*lerp(1,.6,hout),x:hshake,y:-hout*200});
  const w=P(t,.7,.5);$('s1wall').style.opacity=w;$('s1wall').style.transform=`rotate(-8deg) translateY(${-t*90}px) scale(${lerp(1.15,1,eo(w))})`;
  document.querySelectorAll('#s1wall .vw').forEach(e=>{e.lastChild.textContent=Math.floor(+e.dataset.v*eo(P(t,.9,3.2))).toLocaleString('fr-FR');});
  const a0=P(t,1.0,.3),b0=P(t,X.t2||3.6,.25);st('s1t0',{o:a0*(1-b0),y:(1-eo(a0))*40-b0*40});
  const a1=P(t,(X.t2||3.6)+.1,.3);st('s1t1',{o:a1,y:(1-eo(a1))*40});
  $('s1wall').style.filter=t>Z?`saturate(${1-P(t,Z,.4)*.6}) brightness(${1-P(t,Z,.4)*.35})`:'none';
  NOTES.forEach(n=>{const k=t-Z;if(k<0){n.d.style.opacity=0;return}
    const drag=1-Math.exp(-k*2.4);const x=540+Math.cos(n.a)*n.v*drag/2.4*1.6+Math.sin(k*3+n.ph)*40, y=1000+Math.sin(n.a)*n.v*drag/2.4*1.6+180*k*k;
    n.d.style.left=x+'px';n.d.style.top=y+'px';n.d.style.opacity=cl(k/.15);
    n.d.style.transform=`scale(${n.sc*lerp(.2,1,eo(P(k,0,.4)))}) rotateZ(${n.sp*k+Math.sin(k*4+n.ph)*15}deg) rotateX(${Math.sin(k*5+n.ph)*50}deg) rotateY(${Math.cos(k*3.5+n.ph)*30}deg)`;});
}
function S2(t){
  const C=X.s2||{logo:.1,nodes:2.0,l1:2.6,l2:3.3,l3:4.0};const NT=C.nt||[C.nodes,C.nodes+.25,C.nodes+.5];
  const l=P(t,C.logo,.5);st('s2logo',{o:cl(l*3),s:lerp(.3,1.35,back(l))*lerp(1,.74,eio(P(t,C.nodes-.4,.5)))});
  popIn($('s2word'),t,C.logo+.25,{dy:30});
  st('s2glow',{s:1+.06*Math.sin(t*3)});
  ['n1','n2','n3'].forEach((n,i)=>popIn($(n),t,NT[i],{dy:40}));
  vid('v2n',4.5+t*.8);vid('v2c',1.5+t*.8);
  [['l1',C.l1],['l2',C.l2],['l3',C.l3]].forEach(([id,a])=>{const k=eio(P(t,a,.6));$(id).setAttribute('stroke-dashoffset',600*(1-k));$(id).setAttribute('marker-end',k>.92?'url(#ah)':'none');$(id).style.opacity=k>0?1:0;});
}
function S3(t){
  stepTitle('s3',t);
  const C=X.s3||{com:2.2,clic:4.2};
  const cars=document.querySelectorAll('#s3car .car');const idx=Math.floor(t/0.7)%cars.length;
  cars.forEach((c,i)=>{c.style.opacity=i===idx?1:0;c.style.transform=`scale(${1.08-((t%0.7)/0.7)*.05})`;});
  popIn(document.querySelectorAll('#s3 .c3')[0],t,C.com,{dy:30});popIn(document.querySelectorAll('#s3 .c3')[1],t,C.clic,{dy:30});
  const ph=$('s3ph');st(ph,{y:(1-eo(P(t,.1,.6)))*700});
  const b=P(t,C.com-.3,.3),c=P(t,C.clic-.4,.3);
  $('s3imgA').style.opacity=1-b;$('s3imgB').style.opacity=b*(1-c);$('s3imgC').style.opacity=c;
  if(c>0)focus($('s3imgC'),$('s3ring'),[50,318,340,377],1,P(t,C.clic,.25));
  else if(b>0)focus($('s3imgB'),$('s3ring'),[40,265,350,303],1,P(t,C.com,.25));
  else focus($('s3imgA'),$('s3ring'),[40,140,350,259],1,P(t,.9,.25));
}
function S4(t){
  vid('v4',3.5+t);st('v4',{s:lerp(1.1,1.0,eo(P(t,0,4)))});
  stepTitle('s4',t);
  const C=X.s4||{gain:2.6,ret:3.6,plus:[2.8,3.2,3.6,4.0]};
  popIn(document.querySelectorAll('#s4 .c4')[0],t,C.gain,{dy:30});popIn(document.querySelectorAll('#s4 .c4')[1],t,C.ret,{dy:30});
  PL.forEach((p,i)=>{const u=P(t,C.plus[i],1.0);p.style.opacity=u>0&&u<1?1-u*u:0;p.style.transform=`translateY(${-u*150}px) scale(${lerp(.6,1.1,eo(P(t,C.plus[i],.25)))})`;});
}
function S5(t){
  stepTitle('s5',t);
  const C=X.s5||{phone:2.0,msg:2.4,rep:4.2};
  vid('v5',2.0+t);
  const ph=$('s5ph');const pin=eo(P(t,C.phone,.5));st(ph,{y:(1-pin)*1400});
  $('s5shade').style.opacity=1;$('v5').style.opacity=1-P(t,C.phone,.5)*.7;
  const m=P(t,C.msg,.35);$('s5msg').style.opacity=m;$('s5msg').style.transform=`translateY(${(1-eo(m))*40}px) scale(${lerp(.9,1,eo(m))})`;
  const r=P(t,C.rep,.35);$('s5rep').style.opacity=r;$('s5rep').style.transform=`translateY(${(1-eo(r))*40}px)`;
  popIn($('s5lbl'),t,C.msg+.3,{dy:-20});$('s5lbl').style.transform+=' translateX(-50%)';
}
function S6(t){
  const C=X.s6||[.3,1.9,3.3];
  ['s6a','s6b','s6c'].forEach((id,i)=>{const k=P(t,C[i],.45);st(id,{o:cl(k*2.5),x:(1-eo(k))*-200,s:lerp(.9,1,back(k))});});
}
function S7(t){
  const C=X.s7||[.2,1.6,3.0,4.0];
  ['st1','st2','st3'].forEach((id,i)=>{const k=P(t,C[i]-.2,.4);$(id).style.opacity=k;$(id).style.transform=`translateY(${(1-eo(k))*120}px)`;});
  vid('v7a',5.0+t*.8);vid('v7b',4.5+t*.8);vid('v7c',2.0+t*.8);
  document.querySelectorAll('.g7').forEach((g,i)=>popIn(g,t,C[i],{dy:40}));
  const w=P(t,C[3],.5);st('s7win',{o:cl(w*3),s:lerp(.5,1,back(w))});
}
function S8(t){
  const C=X.s8||{url:.4,type:.55,b1:1.6,b2:1.9,free:2.4,tap:3.6};
  const l=P(t,.1,.5);st('s8logo',{o:cl(l*3),s:lerp(.3,1,back(l))});popIn($('s8word'),t,.3,{dy:30});
  popIn($('s8url'),t,C.url,{dy:40});
  const txt='pubwek.com';const nch=Math.floor(cl((t-C.type)/0.9)*txt.length+.0001);$('s8typed').textContent=txt.slice(0,nch);
  $('s8cur').style.opacity=(Math.floor(t*2.5)%2)?1:.1;
  const p1=t>C.b1+.6?1+.03*Math.sin((t-C.b1)*6):1;
  popIn($('s8b1'),t,C.b1,{dy:40});$('s8b1').style.transform+=` scale(${p1})`;
  popIn($('s8b2'),t,C.b2,{dy:40});$('s8b2').style.transform+=` scale(${t>C.b2+.6?1+.03*Math.sin((t-C.b2)*6+1.5):1})`;
  popIn($('s8free'),t,C.free,{dy:20});
  tap($('s8tap'),t,C.tap,975,1145);
}
