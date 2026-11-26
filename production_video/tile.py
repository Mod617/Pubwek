import sys,glob
from PIL import Image,ImageDraw
pre=sys.argv[1]; out=sys.argv[2]
fs=sorted(glob.glob(pre+'_*.png'),key=lambda f:float(f.rsplit('_',1)[1][:-4]))
W=270;H=480;n=len(fs);cols=8;rows=(n+cols-1)//cols
s=Image.new('RGB',(W*cols,H*rows),'black')
for i,f in enumerate(fs):
    im=Image.open(f).convert('RGB').resize((W,H));d=ImageDraw.Draw(im);d.text((5,5),f.rsplit('_',1)[1][:-4],fill='yellow')
    s.paste(im,((i%cols)*W,(i//cols)*H))
s.save(out)
