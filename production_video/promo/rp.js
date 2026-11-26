const { chromium } = require('playwright'); const { spawn } = require('child_process');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:540,height:960}});
await p.goto('file://'+process.argv[2]);await p.waitForTimeout(600);
const ff=spawn('ffmpeg',['-y','-v','error','-f','image2pipe','-framerate','30','-c:v','png','-i','-','-c:v','libx264','-pix_fmt','yuv420p','-crf','18',process.argv[3]],{stdio:['pipe','inherit','inherit']});
for(let i=0;i<180;i++){await p.evaluate(t=>render(t),i/30);ff.stdin.write(await p.screenshot());}
ff.stdin.end();await new Promise(r=>ff.on('close',r));await b.close();})();
