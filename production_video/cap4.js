const { chromium, devices } = require('playwright'); const fs=require('fs');
(async()=>{const out=process.argv[2];const b=await chromium.launch();
 const ctx=await b.newContext({...devices['iPhone 13'],deviceScaleFactor:3,locale:'fr-FR',viewport:{width:390,height:844}});const p=await ctx.newPage();
 const B='http://localhost:5001';const boxes=JSON.parse(fs.readFileSync(out+'/boxes.json'));
 await p.goto(B+'/_apercu/partageur?next=/partageur/instructions_partage/5',{waitUntil:'networkidle'});await p.waitForTimeout(1200);
 await p.evaluate(async()=>{for(const v of document.querySelectorAll('video')){v.pause();v.currentTime=3;await new Promise(r=>{v.onseeked=r;setTimeout(r,1500)});}});await p.waitForTimeout(400);
 await p.screenshot({path:out+'/instr_video_full.png',fullPage:true});
 const bx=async(k,sel)=>{const r=await p.locator(sel).first().boundingBox();boxes[k]=r?[r.x,r.y,r.x+r.width,r.y+r.height].map(Math.round):null;console.log(k,boxes[k]);};
 // vue 1 : haut de page (tarif + légende)
 await p.locator('text=Copiez votre légende').first().evaluate(e=>e.scrollIntoView({block:'start'}));await p.evaluate(()=>window.scrollBy(0,-120));await p.waitForTimeout(400);
 await p.screenshot({path:out+'/instr_v1.png'});await bx('v1_title','text=Copiez votre légende');await bx('v1_copy','text=Copier ma légende');
 // vue 2 : envoyer la vidéo
 await p.locator('text=Envoyer la vidéo vers mon statut WhatsApp').first().evaluate(e=>e.scrollIntoView({block:'center'}));await p.waitForTimeout(400);
 await p.screenshot({path:out+'/instr_v2.png'});await bx('v2_send','text=Envoyer la vidéo vers mon statut WhatsApp');await bx('v2_step','text=Envoyez la vidéo vers WhatsApp');
 fs.writeFileSync(out+'/boxes.json',JSON.stringify(boxes,null,1));await b.close();})();
