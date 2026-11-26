const { chromium, devices } = require('playwright');
(async()=>{const out=process.argv[2];const b=await chromium.launch();
 const ctx=await b.newContext({...devices['iPhone 13'],deviceScaleFactor:3,locale:'fr-FR',viewport:{width:390,height:844}});const p=await ctx.newPage();
 await p.goto('http://localhost:5001/_apercu/annonceur?next=/mes-campagnes/3/partageurs',{waitUntil:'networkidle'});await p.waitForTimeout(600);
 const y=await p.evaluate(()=>{const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&e.textContent.trim()==='95 clics'&&e.getBoundingClientRect().width>0);return el?el.getBoundingClientRect().top+window.scrollY-60:null;});
 console.log('koffi y',y);
 await p.evaluate(y=>window.scrollTo(0,y-150),y);await p.waitForTimeout(500);
 await p.screenshot({path:out+'/a_stats2.png'});
 const r=await p.evaluate(()=>{const f=t=>{const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&e.textContent.trim()===t&&e.getBoundingClientRect().width>0);if(!el)return null;const b=el.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom].map(Math.round)};return {c:f('95 clics'),cr:f('Contacts reçus'),v:f('De vrais clients'),a:f('Aucun contact')}});
 console.log(JSON.stringify(r));
 // stats1 : boîtes
 await p.evaluate(()=>window.scrollTo(0,0));await p.waitForTimeout(300);
 const r1=await p.evaluate(()=>{const f=t=>{const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&e.textContent.trim().startsWith(t));if(!el)return null;let c=el;for(let i=0;i<2;i++)c=c.parentElement;const b=c.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom].map(Math.round)};return {tot:f('Total clics'),obj:f('Objectif de clics')}});
 console.log(JSON.stringify(r1));await b.close();})();
