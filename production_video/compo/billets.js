// Billets stylisés aux couleurs du franc CFA (pas une reproduction des billets officiels).
window.BILLET=function(v){
  const c = v===1000 ? {a:'#C9733A',b:'#F2C38B',d:'#7A3B16',e:'#E8A465'} : {a:'#2E8B57',b:'#BFE3B4',d:'#14532D',e:'#7CC48A'};
  const id='b'+v+Math.random().toString(36).slice(2,7);
  let waves='';for(let i=0;i<9;i++){waves+=`<path d="M0 ${18+i*15} C60 ${6+i*15} 120 ${30+i*15} 180 ${18+i*15} S300 ${6+i*15} 360 ${18+i*15}" stroke="${c.d}" stroke-opacity=".16" stroke-width="2" fill="none"/>`;}
  let ros='';for(let i=0;i<24;i++){const a=i*15;ros+=`<ellipse cx="0" cy="0" rx="38" ry="12" transform="rotate(${a})" fill="none" stroke="${c.d}" stroke-opacity=".28" stroke-width="1.5"/>`;}
  return `<svg width="360" height="170" viewBox="0 0 360 170" xmlns="http://www.w3.org/2000/svg">
  <defs><linearGradient id="${id}g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c.b}"/><stop offset=".55" stop-color="${c.e}"/><stop offset="1" stop-color="${c.a}"/></linearGradient>
  <radialGradient id="${id}r" cx=".3" cy=".5" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
  <rect x="1" y="1" width="358" height="168" rx="8" fill="url(#${id}g)" stroke="${c.d}" stroke-opacity=".5" stroke-width="2"/>
  ${waves}
  <rect x="12" y="12" width="336" height="146" rx="5" fill="none" stroke="${c.d}" stroke-opacity=".55" stroke-width="2.5" stroke-dasharray="1 4"/>
  <ellipse cx="96" cy="85" rx="58" ry="56" fill="url(#${id}r)"/>
  <g transform="translate(96 85)">${ros}<circle r="10" fill="${c.d}" fill-opacity=".35"/></g>
  <rect x="200" y="46" width="132" height="10" rx="5" fill="${c.d}" fill-opacity=".25"/>
  <rect x="200" y="64" width="96" height="8" rx="4" fill="${c.d}" fill-opacity=".2"/>
  <text x="338" y="140" text-anchor="end" font-family="Plus Jakarta Sans,Inter,sans-serif" font-weight="800" font-size="46" fill="${c.d}">${v}</text>
  <text x="24" y="40" font-family="Plus Jakarta Sans,Inter,sans-serif" font-weight="800" font-size="24" fill="${c.d}" fill-opacity=".85">${v}</text>
  <text x="340" y="38" text-anchor="end" font-family="Plus Jakarta Sans,Inter,sans-serif" font-weight="800" font-size="18" letter-spacing="2" fill="${c.d}" fill-opacity=".8">FRANCS</text>
  <rect x="176" y="4" width="7" height="162" fill="#C0C7CF" fill-opacity=".8"/>
  <rect x="1" y="1" width="358" height="168" rx="8" fill="none" stroke="#000" stroke-opacity=".15"/>
  </svg>`;
};
