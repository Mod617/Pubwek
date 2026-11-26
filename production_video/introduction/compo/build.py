# python3 build.py body.html scenes.js out.html
import json,sys
P=json.load(open('parts.json'))
body=open(sys.argv[1]).read(); scenes=open(sys.argv[2]).read()
wrap=P["wrap"].replace("id!=='s9'","id!==LAST")
eng=P["eng"].replace("const DUR=T.s9[1];","const DUR=Math.max(...Object.values(T).map(v=>v[1]));")
html=f'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///home/user/Pubwek/static/css/fonts.css">
<style>{P["css"]}</style></head>
<body><div id="stage">
{body}
<div id="sub" style="position:absolute;left:50px;right:50px;top:1462px;text-align:center;z-index:50;display:none">
  <span id="subt" style="display:inline;background:rgba(5,11,20,.82);color:#fff;font:800 46px/1.36 'Plus Jakarta Sans';padding:8px 22px;border-radius:18px;-webkit-box-decoration-break:clone;box-decoration-break:clone"></span></div>
</div>
<script src="billets.js"></script>
<script>
{eng}
const LAST=Object.keys(T).pop();
// ---------- préparation ----------
{P["icons"]}
{scenes}
{wrap}
{P["step"]}
{P["mk"]}
{P["sub"]}
const FN={{}};for(const k of Object.keys(T)){{FN[k]=window['S'+k.slice(1)]||eval('S'+k.slice(1));}}
window.render=function(t){{SEEKS=[];for(const id in FN){{const lt=sceneWrap(id,t);if(lt!==null)FN[id](lt);}}SUB(t);return Promise.all(SEEKS).then(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}};
window.DUR=DUR;
render(0);
</script></body></html>'''
open(sys.argv[3],'w').write(html)
