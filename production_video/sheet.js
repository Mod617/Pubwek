const { chromium } = require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1080,height:1920}});
if(process.argv[5]){const tl=JSON.parse(require('fs').readFileSync(process.argv[5]));await p.addInitScript(tl=>{window.T=tl.T;window.SUBS=tl.SUBS||[];window.EXTRA=tl.EXTRA||{};},tl);}
const errs=[];p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
await p.goto('file://'+process.argv[2]);await p.waitForFunction(()=>window.videosReady&&videosReady(),null,{timeout:60000});await p.waitForTimeout(500);
const ts=process.argv[4].split(',').map(Number);
for(const t of ts){await p.evaluate(t=>render(t),t);await p.screenshot({path:`${process.argv[3]}_${t.toFixed(2)}.png`});}
console.log('errors:',errs.slice(0,5));await b.close();})();
