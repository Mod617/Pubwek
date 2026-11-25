const { chromium, devices } = require('playwright');
const fs=require('fs');
(async()=>{
 const out=process.argv[2], phase=process.argv[3]; const boxes=fs.existsSync(out+'/boxes.json')?JSON.parse(fs.readFileSync(out+'/boxes.json')):{};
 const b=await chromium.launch();
 const ctx=await b.newContext({...devices['iPhone 13'], deviceScaleFactor:3, locale:'fr-FR', viewport:{width:390,height:844}}); const p=await ctx.newPage();
 const B='http://localhost:5001';
 const scrollTo=async(sel,off)=>{const l=p.locator(sel).first(); await l.evaluate(e=>e.scrollIntoView({block:'start'})); await p.evaluate(o=>window.scrollBy(0,-o),off); await p.waitForTimeout(400);};
 const bx=async(key,sel,up=0)=>{let l=p.locator(sel).first(); for(let i=0;i<up;i++) l=l.locator('xpath=..'); const r=await l.boundingBox(); boxes[key]=r?[r.x,r.y,r.x+r.width,r.y+r.height].map(v=>Math.round(v)):null; console.log(key,boxes[key]);};
 const shot=async n=>{await p.waitForTimeout(300);await p.screenshot({path:`${out}/${n}.png`});};
 const fixFile=()=>p.evaluate(()=>{document.querySelectorAll('input[type=file]').forEach(i=>{const w=document.createElement('span');w.textContent='Choisir un fichier';w.style.cssText='display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;background:#F0FBFD;color:#08486E;font:600 14px inherit;white-space:nowrap';i.style.display='none';i.parentNode.insertBefore(w,i);});});
 if(phase==='a'){
  await p.goto(B+'/register/partageur',{waitUntil:'networkidle'}); await shot('register'); await bx('reg_card','text=Créer votre compte',1); await bx('reg_badge','text=Compte partageur');
  await p.goto(B+'/_apercu/partageur?next=/dashboard/partageur',{waitUntil:'networkidle'});
  const btn=p.locator('xpath=//*[contains(text(),"Pâtisserie Doux Matin")]/following::button[1]').first();
  if((await btn.innerText()).includes('Partager')){await btn.click(); await p.waitForLoadState('networkidle');}
  await p.goto(B+'/partageur/instructions_partage/5',{waitUntil:'networkidle'}); await shot('instr_top');
  await bx('it_rate','text=10 FCFA par clic valide',1); await bx('it_copy','text=Copier le texte complet');
  await fixFile(); await scrollTo('text=Envoyez la preuve',90); await shot('instr_proof');
  await bx('ip_card','text=Preuve à envoyer',2); await bx('ip_send','button:has-text("Envoyer") >> visible=true');
  await p.goto(B+'/partageur/mes-retraits',{waitUntil:'networkidle'}); await scrollTo('text=Faire une demande de retrait',330); await shot('retrait_form');
  await bx('rf_select','select'); await bx('rf_send','text=Envoyer la demande de retrait');
 } else {
  await p.goto(B+'/_apercu/partageur?next=/dashboard/partageur',{waitUntil:'networkidle'});
  await scrollTo('text=Campagnes disponibles dans vos zones',90); await shot('dash_camps');
  await scrollTo('text=Pâtisserie Doux Matin',230); await shot('dash_video');
  await bx('dv_rate','text=10 FCFA / clic'); await bx('dv_title','text=Pâtisserie Doux Matin');
  await bx('dv_btn','xpath=//*[contains(text(),"Pâtisserie Doux Matin")]/following::button[1]');
 }
 fs.writeFileSync(out+'/boxes.json',JSON.stringify(boxes,null,1)); await b.close();
})();
