// Illustration vectorielle : une commerçante à son étal (marché), style "flat".
// Exporte window.MAMAN_SVG (chaîne SVG, viewBox 0 0 1080 1100).
// Ids animables : mm-head (groupe tête), mm-eyes (paupières), mm-mouth-o / mm-mouth-s
// (bouche neutre / sourire), mm-phone (téléphone), mm-glow (lueur écran).
(function () {
  const skin = '#7B4A2C', skinD = '#5E3620', skinL = '#9A613B';
  const tomatoes = [];
  const tpos = [[250, 800], [290, 790], [330, 802], [270, 770], [312, 768], [292, 745]];
  tpos.forEach(([x, y]) => tomatoes.push(
    `<circle cx="${x}" cy="${y}" r="26" fill="#E0412B"/><circle cx="${x - 8}" cy="${y - 9}" r="7" fill="#FF8A70" opacity=".7"/><path d="M${x - 6} ${y - 24} l6 -6 l6 6" stroke="#1F7A3A" stroke-width="5" fill="none" stroke-linecap="round"/>`));
  const peppers = [];
  [[760, 790, '#F5B700'], [800, 798, '#E0412B'], [840, 788, '#2E9E4F'], [780, 765, '#2E9E4F'], [820, 762, '#F5B700']].forEach(([x, y, c]) =>
    peppers.push(`<ellipse cx="${x}" cy="${y}" rx="20" ry="28" fill="${c}" transform="rotate(-18 ${x} ${y})"/><ellipse cx="${x - 6}" cy="${y - 8}" rx="5" ry="9" fill="#fff" opacity=".35" transform="rotate(-18 ${x} ${y})"/>`));
  const onions = [];
  [[540 - 0, 0]].forEach(() => { });
  const stripes = [];
  for (let i = 0; i < 12; i++) {
    const x = i * 90;
    stripes.push(`<rect x="${x}" y="0" width="90" height="120" fill="${i % 2 ? '#FFF4E0' : '#E0412B'}"/>`);
    stripes.push(`<path d="M${x} 120 q45 46 90 0 z" fill="${i % 2 ? '#FFF4E0' : '#E0412B'}"/>`);
  }
  window.MAMAN_SVG = `
<svg viewBox="0 0 1080 1100" xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">
 <defs>
  <pattern id="wax" width="70" height="70" patternUnits="userSpaceOnUse">
    <rect width="70" height="70" fill="#E8590C"/>
    <circle cx="35" cy="35" r="20" fill="#0B6E4F"/><circle cx="35" cy="35" r="11" fill="#FCD34D"/>
    <circle cx="0" cy="0" r="9" fill="#FCD34D"/><circle cx="70" cy="0" r="9" fill="#FCD34D"/>
    <circle cx="0" cy="70" r="9" fill="#FCD34D"/><circle cx="70" cy="70" r="9" fill="#FCD34D"/>
  </pattern>
  <pattern id="gele" width="56" height="56" patternUnits="userSpaceOnUse" patternTransform="rotate(30)">
    <rect width="56" height="56" fill="#F2B705"/>
    <path d="M0 28 h56" stroke="#D97706" stroke-width="10"/>
    <circle cx="28" cy="10" r="5" fill="#0B6E4F"/>
  </pattern>
  <radialGradient id="glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#7DD3FC" stop-opacity=".9"/><stop offset="1" stop-color="#7DD3FC" stop-opacity="0"/></radialGradient>
  <linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#A0672F"/><stop offset="1" stop-color="#6B4220"/></linearGradient>
 </defs>
 <!-- auvent -->
 <g>${stripes.join('')}<rect x="0" y="0" width="1080" height="14" fill="#7A1F14" opacity=".35"/></g>
 <!-- poteaux -->
 <rect x="70" y="120" width="26" height="900" fill="#6B4220"/><rect x="984" y="120" width="26" height="900" fill="#6B4220"/>

 <!-- corps -->
 <g id="mm-body">
  <path d="M330 1000 C330 760 360 640 450 610 L630 610 C720 640 750 760 750 1000 Z" fill="url(#wax)"/>
  <path d="M450 610 L630 610 L600 640 Q540 690 480 640 Z" fill="${skinD}"/>
  <!-- bras gauche (côté spectateur droit) posé sur l'étal -->
  <path d="M650 640 C730 700 740 780 690 812" stroke="#7C2D12" stroke-width="90" fill="none" stroke-linecap="round"/><path d="M650 640 C730 700 740 780 690 812" stroke="url(#wax)" stroke-width="78" fill="none" stroke-linecap="round"/>
  <ellipse cx="672" cy="818" rx="46" ry="24" fill="${skin}"/>
 </g>
 <!-- cou -->
 <path d="M500 520 L580 520 L590 615 Q540 650 490 615 Z" fill="${skin}"/>
 <!-- tête -->
 <g id="mm-head" style="transform-origin:540px 560px">
  <ellipse cx="540" cy="430" rx="112" ry="132" fill="${skin}"/>
  <ellipse cx="430" cy="440" rx="18" ry="30" fill="${skinD}"/><ellipse cx="650" cy="440" rx="18" ry="30" fill="${skinD}"/>
  <circle cx="428" cy="486" r="13" fill="none" stroke="#F2B705" stroke-width="6"/><circle cx="652" cy="486" r="13" fill="none" stroke="#F2B705" stroke-width="6"/>
  <!-- sourcils -->
  <path d="M478 395 q24 -16 48 -2" stroke="#2B160B" stroke-width="8" fill="none" stroke-linecap="round"/>
  <path d="M554 393 q24 -14 48 2" stroke="#2B160B" stroke-width="8" fill="none" stroke-linecap="round"/>
  <!-- yeux -->
  <g id="mm-eyes" style="transform-origin:540px 428px">
   <ellipse cx="502" cy="428" rx="13" ry="16" fill="#1B0E06"/><circle cx="506" cy="423" r="4.5" fill="#fff"/>
   <ellipse cx="578" cy="428" rx="13" ry="16" fill="#1B0E06"/><circle cx="582" cy="423" r="4.5" fill="#fff"/>
  </g>
  <!-- nez -->
  <path d="M532 445 q-14 30 0 36 q8 4 16 0 q14 -6 0 -36" fill="${skinD}" opacity=".55"/>
  <!-- joues -->
  <ellipse cx="478" cy="478" rx="22" ry="12" fill="#C2410C" opacity=".22"/><ellipse cx="602" cy="478" rx="22" ry="12" fill="#C2410C" opacity=".22"/>
  <!-- bouches -->
  <path id="mm-mouth-o" d="M512 506 q28 10 56 0" stroke="#4A1D10" stroke-width="7" fill="none" stroke-linecap="round"/>
  <g id="mm-mouth-s" opacity="0">
   <path d="M500 498 q40 52 80 0 z" fill="#5A1A10"/>
   <path d="M506 500 q34 14 68 0 l-4 10 q-30 10 -60 0 z" fill="#fff"/>
   <path d="M520 530 q20 10 40 0 q-20 -8 -40 0" fill="#E35D5B"/>
  </g>
  <!-- gele (foulard) -->
  <path d="M418 400 C400 300 450 240 540 236 C640 232 690 300 664 400 C650 352 610 330 540 330 C470 330 430 352 418 400 Z" fill="url(#gele)"/>
  <path d="M400 330 C380 230 470 150 560 168 C660 150 740 230 700 330 C720 270 660 200 600 230 C560 190 470 200 470 250 C430 240 400 280 400 330 Z" fill="url(#gele)"/>
  <path d="M640 210 C720 160 790 200 770 250 C750 230 700 230 680 260 Z" fill="#D97706"/>
  <path d="M440 250 C470 220 520 215 560 230" stroke="#B45309" stroke-width="7" fill="none" stroke-linecap="round" opacity=".7"/>
  <path d="M460 300 C510 280 590 280 640 300" stroke="#B45309" stroke-width="7" fill="none" stroke-linecap="round" opacity=".7"/>
 </g>
 <!-- bras droit qui tient le téléphone -->
 <path d="M440 640 C360 700 350 790 400 800 C440 806 455 760 470 720" stroke="#7C2D12" stroke-width="92" fill="none" stroke-linecap="round"/><path d="M440 640 C360 700 350 790 400 800 C440 806 455 760 470 720" stroke="url(#wax)" stroke-width="80" fill="none" stroke-linecap="round"/>
 <g id="mm-phone" style="transform-origin:470px 640px">
  <circle id="mm-glow" cx="470" cy="620" r="170" fill="url(#glow)" opacity="0"/>
  <rect x="416" y="530" width="110" height="200" rx="22" fill="#111827" stroke="#4B5563" stroke-width="5"/>
  <circle cx="440" cy="556" r="8" fill="#4B5563"/><circle cx="440" cy="580" r="8" fill="#4B5563"/>
  <ellipse cx="452" cy="712" rx="44" ry="34" fill="${skin}"/>
  <path d="M500 640 q26 10 24 40 q-2 18 -22 16" fill="${skinL}"/>
 </g>
<!-- étal -->
 <g>
  ${tomatoes.join('')}
  ${peppers.join('')}
  
  <circle cx="885" cy="800" r="22" fill="#8E3B8E"/><circle cx="922" cy="802" r="22" fill="#A44AA4"/><circle cx="903" cy="778" r="20" fill="#B565B5"/>
  <rect x="120" y="820" width="840" height="40" rx="8" fill="#B87333"/>
  <rect x="140" y="860" width="800" height="240" fill="url(#wood)"/>
  <path d="M140 920 h800 M140 990 h800 M140 1060 h800" stroke="#4A2C14" stroke-width="5" opacity=".5"/>
 </g>
</svg>`;
})();
