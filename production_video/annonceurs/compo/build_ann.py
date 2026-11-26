import json
P=json.load(open('parts.json'))
body=open('ann_body.html').read(); scenes=open('ann_scenes.js').read()
html=f'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///home/user/Pubwek/static/css/fonts.css">
<style>{P["css"]}</style></head>
<body><div id="stage">
{body}
<div id="sub" style="position:absolute;left:50px;right:50px;top:1462px;text-align:center;z-index:50;display:none">
  <span id="subt" style="display:inline;background:rgba(5,11,20,.82);color:#fff;font:800 46px/1.36 'Plus Jakarta Sans';padding:8px 22px;border-radius:18px;-webkit-box-decoration-break:clone;box-decoration-break:clone"></span></div>
</div>
<script>
{P["eng"]}
// ---------- préparation ----------
{P["icons"]}
{scenes}
{P["wrap"]}
{P["step"]}
{P["mk"]}
{P["sub"]}
const FN={{s1:S1,s2:S2,s3:S3,s4:S4,s5:S5,s6:S6,s7:S7,s8:S8,s9:S9}};
window.render=function(t){{SEEKS=[];for(const id in FN){{const lt=sceneWrap(id,t);if(lt!==null)FN[id](lt);}}SUB(t);return Promise.all(SEEKS).then(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}};
window.DUR=DUR;
render(0);
</script></body></html>'''
open('compo_ann.html','w').write(html)
