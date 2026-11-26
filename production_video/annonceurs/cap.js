const { chromium, devices } = require('playwright'); const fs=require('fs');
(async()=>{const out=process.argv[2];const b=await chromium.launch();
 const ctx=await b.newContext({...devices['iPhone 13'],deviceScaleFactor:3,locale:'fr-FR',viewport:{width:390,height:844}});const p=await ctx.newPage();
 const B='http://localhost:5001';const boxes={};
 const bx=async(k,sel)=>{const r=await p.locator(sel).first().boundingBox();boxes[k]=r?[r.x,r.y,r.x+r.width,r.y+r.height].map(Math.round):null;console.log(k,boxes[k]);};
 const top=async(sel,off)=>{await p.locator(sel).first().evaluate(e=>e.scrollIntoView({block:'start'}));await p.evaluate(o=>window.scrollBy(0,-o),off);await p.waitForTimeout(500);};
 const fixFile=()=>p.evaluate(()=>{document.querySelectorAll('input[type=file]').forEach(i=>{const w=document.createElement('span');w.textContent='Choisir un fichier';w.style.cssText='display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;background:#F0FBFD;color:#08486E;font:600 14px inherit;white-space:nowrap';i.style.display='none';i.parentNode.insertBefore(w,i);});});
 await p.goto(B+'/_apercu/annonceur?next=/dashboard/annonceur',{waitUntil:'networkidle'});await p.waitForTimeout(800);
 await fixFile();
 // remplissage réel du formulaire
 const nom=p.locator('input[name="promotion_detail"], input#promotion_detail').first(); if(await nom.count()) await nom.fill('Pâtisserie Doux Matin');
 await p.locator('#description').fill("Gâteaux d'anniversaire sur commande, livrés partout à Cotonou.");
 // vue 1 : lancer une campagne
 await top('text=Lancer une campagne',100); await p.screenshot({path:out+'/a_form1.png'});
 await bx('f1_title','text=Lancer une campagne');
 // vue 2 : style de diffusion
 await top('text=Style de diffusion',120); await p.screenshot({path:out+'/a_style0.png'});
 await bx('st_A','text=A · Vidéo publicitaire'); await bx('st_B','text=B · Multi-statuts'); await bx('st_C','text=C · Texte seul');
 for(const k of ['st_A','st_B','st_C']){const r=boxes[k]; if(r){}}
 const optA=p.locator('text=A · Vidéo publicitaire').first(); await optA.click(); await p.waitForTimeout(400);
 await top('text=Style de diffusion',120); await p.screenshot({path:out+'/a_style1.png'});
 // cartes complètes (parent cliquable)
 for(const [k,t] of [['cA','A · Vidéo publicitaire'],['cB','B · Multi-statuts'],['cC','C · Texte seul']]){const l=p.locator(`label:has-text("${t}")`).first(); if(await l.count()){const r=await l.boundingBox();boxes[k]=[r.x,r.y,r.x+r.width,r.y+r.height].map(Math.round);console.log(k,boxes[k]);}}
 // zones
 for(const d of ['Littoral','Atlantique']){const c=p.locator(`#dept_${d}`); if(await c.count()) await c.check({force:true}).catch(e=>console.log('chk',e.message));}
 await p.waitForTimeout(600);
 await top('text=Zones de diffusion',110); await p.screenshot({path:out+'/a_zones.png'});
 await bx('z_title','text=Zones de diffusion'); await bx('z_lit','label:has-text("Littoral")'); await bx('z_atl','label:has-text("Atlantique")');
 // objectif / durée / budget
 const obj=p.locator('input[name="whatsapp_views"]').first(); if(await obj.count()){await obj.fill('450'); await obj.dispatchEvent('input');}
 await p.waitForTimeout(600);
 await top('text=Objectif de clics',140); await p.screenshot({path:out+'/a_budget.png'});
 await bx('b_obj','input[name="whatsapp_views"]'); await bx('b_total','#total_cost_display'); await bx('b_btn','text=Valider ma campagne');
 console.log('budget', await p.locator('#total_cost_display').innerText());
 // mes campagnes : statuts
 await p.goto(B+'/mes-campagnes',{waitUntil:'networkidle'});await p.waitForTimeout(500);
 await top('text=En attente de validation',90); await p.screenshot({path:out+'/a_attente.png'}); await bx('m_analyse','text=En analyse');
 await top('text=En cours de diffusion',90); await p.screenshot({path:out+'/a_diffusion.png'}); await bx('m_diff','text=En diffusion'); await bx('m_stats','text=Partageurs & statistiques');
 // statistiques
 await p.goto(B+'/mes-campagnes/3/partageurs',{waitUntil:'networkidle'});await p.waitForTimeout(500);
 await p.evaluate(()=>window.scrollTo(0,0)); await p.waitForTimeout(300); await p.screenshot({path:out+'/a_stats1.png'});
 await bx('s_total','text=Total clics'); await bx('s_obj','text=Objectif de clics de la campagne');
 await top('text=Koffi229',140); await p.screenshot({path:out+'/a_stats2.png'}); await bx('s_koffi','text=Koffi229'); await bx('s_rate','text=Contacts reçus');
 fs.writeFileSync(out+'/boxes.json',JSON.stringify(boxes,null,1));await b.close();})();
