// node render.js compo.html timeline.json out.mp4
const { chromium } = require('playwright'); const { spawn } = require('child_process'); const fs=require('fs');
(async()=>{
 const [html,tlf,out]=process.argv.slice(2); const tl=JSON.parse(fs.readFileSync(tlf));
 const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1080,height:1920}});
 await p.addInitScript(tl=>{window.T=tl.T;window.SUBS=tl.SUBS||[];},tl);
 await p.goto('file://'+html); await p.waitForTimeout(1000);
 const FPS=30, dur=tl.T.s9[1], n=Math.round(dur*FPS);
 const ff=spawn('ffmpeg',['-y','-v','error','-f','image2pipe','-framerate',String(FPS),'-c:v','png','-i','-','-c:v','libx264','-preset','medium','-crf','14','-pix_fmt','yuv420p',out],{stdio:['pipe','inherit','inherit']});
 for(let i=0;i<n;i++){ await p.evaluate(t=>render(t),i/FPS); const buf=await p.screenshot({type:'png'}); if(!ff.stdin.write(buf)) await new Promise(r=>ff.stdin.once('drain',r)); if(i%150==0) console.log(i,'/',n); }
 ff.stdin.end(); await new Promise(r=>ff.on('close',r)); await b.close(); console.log('done',n);
})();
