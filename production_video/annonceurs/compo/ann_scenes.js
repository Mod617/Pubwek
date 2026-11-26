// ---------- préparation (vidéo annonceurs) ----------
const X=window.EXTRA||{};
// flyers qui tombent (accroche)
const FLY=[];for(let i=0;i<16;i++){const d=document.createElement('div');
  const col=['#EF4444','#F59E0B','#3B82F6','#10B981'][i%4];
  d.style.cssText='position:absolute;width:190px;height:260px;margin:-130px 0 0 -95px;background:#FFFDF7;border-radius:6px;box-shadow:0 10px 24px rgba(0,0,0,.45);opacity:0;overflow:hidden';
  d.innerHTML=`<div style="height:70px;background:${col};color:#fff;font:800 34px 'Plus Jakarta Sans';display:flex;align-items:center;justify-content:center">PROMO</div><div style="margin:18px 16px;height:14px;background:#CBD5E1;border-radius:7px"></div><div style="margin:12px 16px;height:14px;width:60%;background:#CBD5E1;border-radius:7px"></div><div style="margin:12px 16px;height:14px;width:80%;background:#CBD5E1;border-radius:7px"></div><div style="margin:22px 16px 0;height:44px;background:${col};opacity:.35;border-radius:8px"></div>`;
  $('s1fly').appendChild(d);FLY.push({d,x:80+((i*137)%920),delay:(i*0.17)%2.2,rot:((i*53)%60)-30,spin:((i*29)%40)-20,land:1520+((i*71)%300)});}
// notifications WhatsApp (scène 2)
const NOTIFS=[['Awa','Bonjour, je vous contacte suite à votre publicité vue sur Pubwek…'],['Koffi','Bonjour, je vous contacte suite à votre publicité vue sur Pubwek…'],['Mariam','Bonjour, je suis intéressée, vous livrez à Calavi ?']];
NOTIFS.forEach(([n,m],i)=>{const d=document.createElement('div');d.className='notif';d.style.cssText+=';position:absolute;left:0;right:0;top:'+(i*150)+'px;padding:18px 22px;opacity:0';
  d.innerHTML=`<div class="ic" style="width:72px;height:72px;background:#25D366;box-shadow:none"><svg width="44" height="44" viewBox="0 0 24 24"><path d="M12 2a10 10 0 00-8.6 15.1L2 22l5-1.3A10 10 0 1012 2z" fill="#fff"/><path d="M8.6 7.6c.3-.6.6-.6.9-.6h.6c.2 0 .5 0 .7.5l.9 2.1c.1.2 0 .5-.1.7l-.6.7c-.2.2-.2.4 0 .7a7 7 0 003.3 2.9c.3.1.5.1.7-.1l.7-.9c.2-.2.4-.3.7-.2l2 .9c.3.1.5.3.5.5 0 .9-.6 1.9-1.6 2.2-1 .3-2.3.3-4.6-1a10 10 0 01-3.7-4c-.7-1.4-.6-2.6-.3-3.3z" fill="#25D366"/></svg></div>
  <div style="flex:1;min-width:0"><div style="display:flex;justify-content:space-between;font:600 24px Inter;color:#64748B"><span>WhatsApp · ${n}</span><span>maintenant</span></div><div style="font:500 27px/1.3 Inter;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${m}</div></div>`;
  $('s2notifs').appendChild(d);});

// ---------- scènes ----------
function S1(t){
  const Z=X.zero||7.2;
  const hk=P(t,0,.18);const hshake=t<.6?Math.sin(t*80)*14*(1-P(t,0,.6)):0;const hout=P(t,.85,.25);
  st('s1hey',{o:(1-hout),s:lerp(1.6,1,eo(hk))*lerp(1,.6,hout),x:hshake,y:-hout*200});
  // la commerçante (« toi qui as un commerce »)
  const vv=P(t,.9,.3)*(1-P(t,2.6,.3));$('v1').style.opacity=vv;$('s1veil').style.opacity=vv;$('s1shade').style.opacity=vv;
  if(t>.8&&t<3.0){vid('v1',2.0+t);st('v1',{s:lerp(1.12,1.02,P(t,.9,2))});}
  const a0=P(t,1.0,.3),b0=P(t,2.6,.25);st('s1t0',{o:a0*(1-b0),y:(1-eo(a0))*40-b0*40});
  const a1=P(t,2.7,.3),b1=P(t,5.3,.25);st('s1t1',{o:a1*(1-b1),y:(1-eo(a1))*40-b1*40});
  const a2=P(t,5.4,.3);st('s1t2',{o:a2,y:(1-eo(a2))*40});
  // les flyers tombent et s'entassent
  FLY.forEach(f=>{const k=t-2.75-f.delay;if(k<0){f.d.style.opacity=0;return}
    const y=Math.min(f.land,-200+k*k*1500);const landed=y>=f.land;
    f.d.style.left=(f.x+Math.sin(k*3+f.rot)*40)+'px';f.d.style.top=y+'px';f.d.style.opacity=1-P(t,Z-.3,.3)*.6;
    f.d.style.transform=`rotate(${landed?f.rot:f.rot+Math.sin(k*5)*25}deg) scale(${landed?.9:1})`;});
  const sp=P(t,3.4,.4);st('s1spent',{o:cl(sp*3)*(1-P(t,5.3,.3)),y:(1-eo(sp))*40});
  const z=P(t,Z-.1,.42);st('s1q',{o:z>0?1:0,s:lerp(2.6,1,eo(z)),r:-10});
  const sh=t>Z+.15&&t<Z+.6?Math.sin(t*90)*12*(1-P(t,Z+.15,.45)):0;$('s1fly').style.marginLeft=sh+'px';
}
function S2(t){
  vid('v2',4.6+t);st('v2',{s:lerp(1.1,1.0,eo(P(t,0,4)))});
  popIn($('s2t'),t,.15,{dy:50});popIn($('s2t2'),t,X.s2pill||2.8,{dy:30});
  document.querySelectorAll('#s2notifs .notif').forEach((n,i)=>{const k=P(t,1.4+i*.45,.45);n.style.opacity=cl(k*3);n.style.transform=`translateY(${(1-back(k))*-120}px)`;});
}
function S3(t){
  stepTitle('s3',t);
  const TY=X.s3types||[2.6,3.2,3.9];
  document.querySelectorAll('#s3 .c3').forEach((c,i)=>popIn(c,t,TY[i],{dy:30}));
  const ph=$('s3ph');st(ph,{y:(1-eo(P(t,.1,.6)))*700});
  const sw=P(t,1.9,.35);$('s3imgA').style.opacity=1-sw;$('s3imgB').style.opacity=sw;
  let f;
  if(sw<1){const k=eio(P(t,.7,.6));f=focus($('s3imgA'),$('s3ring'),lerpRect([0,0,390,844],[28,150,362,320],k),lerp(1,1.1,k),P(t,1.1,.25)*(1-sw));}
  else{const cards=[[40,140,350,259],[40,259,350,378],[40,378,350,497]];let r=cards[0];
    if(t>=TY[2])r=cards[2];else if(t>=TY[1])r=cards[1];
    if(t>=TY[2]+.6)r=cards[0];
    f=focus($('s3imgB'),$('s3ring'),r,1,t>=TY[0]-.2?1:0);}
  const [x,y]=phonePt(ph,f||{tx:0,ty:0,z:1},195,200,640);
  tap($('s3tap'),t,TY[2]+.85,x,y);
}
function S4(t){
  stepTitle('s4',t);
  const Z4=X.s4||{dep:1.1,obj:3.3,bud:4.6,btn:6.0};
  document.querySelectorAll('#s4 .z4').forEach((c,i)=>popIn(c,t,[Z4.dep,Z4.dep+.6,Z4.obj][i],{dy:30}));
  const ph=$('s4ph');st(ph,{y:(1-eo(P(t,.1,.6)))*700});
  const sw=P(t,Z4.obj-.5,.35);$('s4imgA').style.opacity=1-sw;$('s4imgB').style.opacity=sw;
  let f,tp;
  if(sw<1){const r=t<Z4.dep+.6?[40,265,350,303]:[40,461,350,499];f=focus($('s4imgA'),$('s4ring'),r,1,P(t,Z4.dep-.2,.2)*(1-sw));tp=t<Z4.dep+.6?[100,284]:[90,480];}
  else{const r=t<Z4.bud?[50,150,340,210]:(t<Z4.btn-.5?[50,318,340,377]:[40,668,350,724]);f=focus($('s4imgB'),$('s4ring'),r,1,1);tp=[195,696];}
  const [x,y]=phonePt(ph,f,tp[0],tp[1],640);
  if(sw<1){if(t<Z4.dep+.6)tap($('s4tap'),t,Z4.dep+.2,x,y);else tap($('s4tap'),t,Z4.dep+.8,x,y);}
  else tap($('s4tap'),t,Z4.btn,x,y);
  popIn($('s4budget'),t,Z4.bud+.2,{dy:50});$('s4budget').style.opacity*=(1-P(t,Z4.btn-.4,.3));
}
function S5(t){
  stepTitle('s5',t);
  const Z5=X.s5||{pay:.5,check:1.4,ok:2.6,diff:3.4};
  document.querySelectorAll('#s5 .op').forEach((o,i)=>popIn(o,t,Z5.pay+i*.14,{dy:30}));
  const ph=$('s5ph');st(ph,{y:(1-eo(P(t,.1,.6)))*700});
  const sw=P(t,Z5.diff,.35);$('s5imgA').style.opacity=1-sw;$('s5imgB').style.opacity=sw;
  if(sw<1)focus($('s5imgA'),$('s5ring'),[258,132,352,166],1,P(t,Z5.check,.25)*(1-sw));
  else focus($('s5imgB'),$('s5ring'),[251,132,352,166],1,P(t,Z5.diff+.3,.25));
  const n=P(t,Z5.ok+.3,.5);st('s5n',{o:cl(n*3)*(1-P(t,Z5.diff-.1,.25)),y:(1-back(n))*-120});
  const c=P(t,Z5.ok,.45);st('s5check',{o:cl(c*3),s:lerp(2,1,eo(c)),r:8});
}
function S6(t){
  stepTitle('s6',t);
  const Z6=X.s6||{tap:1.7,cli:2.2,chat:3.6,msg:3.9,rep:5.6,moto:6.3};
  const ph=$('s6ph');
  // 1) le statut d'un partageur : la pub avec le lien
  if(t<Z6.cli+.3)vid('v6s',1.2+t);
  $('s6prog').style.width=(10+P(t,0,3)*70)+'%';
  $('s6link').style.background=t>Z6.tap&&t<Z6.tap+.35?'rgba(125,211,252,.4)':'transparent';
  tap($('s6tap'),t,Z6.tap,256+284,560+16+828);
  // 2) une cliente clique
  const cv=P(t,Z6.cli,.3)*(1-P(t,Z6.chat,.3));$('v6a').style.opacity=cv;if(cv>0)vid('v6a',2.0+(t-Z6.cli));
  // 4) le client arrive
  const mv=P(t,Z6.moto,.3);$('v6b').style.opacity=mv;if(mv>0)vid('v6b',2.4+(t-Z6.moto));
  $('s6shade').style.opacity=Math.max(cv,mv);
  const phoneOut=Math.max(cv,mv);
  st(ph,{y:(1-eo(P(t,.1,.6)))*700+phoneOut*1400});
  // 3) le message arrive sur le WhatsApp de l'annonceur
  $('s6chat').style.opacity=P(t,Z6.chat,.2);
  const m=P(t,Z6.msg,.35);$('s6msg').style.opacity=m;$('s6msg').style.transform=`translateY(${(1-eo(m))*40}px) scale(${lerp(.9,1,eo(m))})`;
  const r=P(t,Z6.rep,.35);$('s6rep').style.opacity=r;$('s6rep').style.transform=`translateY(${(1-eo(r))*40}px)`;
  // étiquettes
  const L=$('s6lbl');let txt='',o=0;
  if(t>=Z6.cli+.2&&t<Z6.chat){txt='Une cliente clique sur le lien';o=P(t,Z6.cli+.2,.3);}
  else if(t>=Z6.msg+.3&&t<Z6.moto){txt='Elle t\'écrit directement sur WhatsApp';o=P(t,Z6.msg+.3,.3);}
  else if(t>=Z6.moto+.3){txt='Et le client arrive !';o=P(t,Z6.moto+.3,.3);}
  L.textContent=txt;L.style.opacity=o;L.style.top=(t>=Z6.msg+.3&&t<Z6.moto)?'470px':'1330px';
}
function S7(t){
  stepTitle('s7',t);
  const Z7=X.s7||{tot:1.2,obj:2.2,part:3.2,fake:4.4};
  document.querySelectorAll('#s7 .c7').forEach((c,i)=>popIn(c,t,[.5,Z7.fake][i],{dy:30}));
  const ph=$('s7ph');st(ph,{y:(1-eo(P(t,.1,.6)))*700});
  const sw=P(t,Z7.part,.35);$('s7imgA').style.opacity=1-sw;$('s7imgB').style.opacity=sw;
  if(sw<1){const r=t<Z7.obj?[28,234,362,417]:[28,429,362,547];focus($('s7imgA'),$('s7ring'),r,1,P(t,Z7.tot,.25)*(1-sw));}
  else{const r=t<Z7.fake?[28,156,362,378]:[240,302,350,336];focus($('s7imgB'),$('s7ring'),r,1,P(t,Z7.part+.25,.25));}
}
function S8(t){
  vid('v8',5.0+t*.9);st('v8',{s:lerp(1.08,1,eo(P(t,0,4)))});
  const T8=X.s8||[.3,1.2,2.4];
  document.querySelectorAll('.s8li').forEach((e,i)=>{const k=P(t,T8[i],.35);e.style.opacity=k;e.style.transform=`translateX(${(1-eo(k))*-80}px)`;});
}
function S9(t){
  ['v9a','v9b','v9c','v9d'].forEach((v,i)=>vid(v,[4.0,5.0,2.0,4.5][i]+t*.9));st('s9wall',{s:lerp(1.08,1,eo(P(t,0,6)))});
  const g=1+.06*Math.sin(t*3);st('s9glow',{s:g});
  const l=P(t,.1,.5);st('s9logo',{o:cl(l*3),s:lerp(.3,1,back(l))});
  const C9=X.s9||{url:.75,type:1.0,btn:2.1,list:2.7,tap:4.6};popIn($('s9word'),t,.4,{dy:30});popIn($('s9url'),t,C9.url,{dy:40});
  const txt='pubwek.com';const nch=Math.floor(cl((t-C9.type)/0.9)*txt.length+.0001);$('s9typed').textContent=txt.slice(0,nch);
  $('s9cur').style.opacity=(Math.floor(t*2.5)%2)?1:.1;
  const b=P(t,C9.btn,.5);const pulse=t>C9.btn+.7?1+.04*Math.sin((t-C9.btn-.7)*6):1;st('s9btn',{o:cl(b*3),s:lerp(.6,1,back(b))*pulse});
  document.querySelectorAll('.s9li').forEach((e,i)=>{const k=P(t,C9.list+i*.3,.35);e.style.opacity=k;e.style.transform=`translateX(${(1-eo(k))*-60}px)`;});
  tap($('s9tap'),t,C9.tap,860,1035);
}
