const ms = (require('milsymbol').default || require('milsymbol'));
const fs = require('fs');

// CORRECT entity codes from milsymbol landunit.js source:
// 110000 = C2/Command and Control
// 120400 = Antitank/Anti-Armor
// 120500 = Armor (oval icon)
// 121100 = Infantry (X cross icon)
// 121102 = Mech Infantry (X + armor)
// 121200 = Observer/Observation
// 121300 = Reconnaissance (slash)
// 121500 = Sniper
// 130300 = Field Artillery
// 130800 = Mortar
// 140700 = Engineer (castle)
// 140703 = Engineer + Recon
// 141400 = Mine Clearing
// 141600 = Mine Laying
// 160600 = Combat Service Support

const correctedCodes = {
    'headquarters':       { entity: '110000', ech: '15', hq: '2', m1: '00', m2: '00', expect: 'C2 + HQ staff' },
    'command_post':       { entity: '110000', ech: '14', hq: '2', m1: '00', m2: '00', expect: 'C2 + HQ staff' },
    'infantry_team':      { entity: '121100', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'infantry_squad':     { entity: '121100', ech: '12', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'infantry_section':   { entity: '121100', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'infantry_platoon':   { entity: '121100', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'infantry_company':   { entity: '121100', ech: '15', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'infantry_battalion':  { entity: '121100', ech: '16', hq: '0', m1: '00', m2: '00', expect: 'X cross' },
    'mech_platoon':       { entity: '121102', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'X + oval' },
    'mech_company':       { entity: '121102', ech: '15', hq: '0', m1: '00', m2: '00', expect: 'X + oval' },
    'tank_platoon':       { entity: '120500', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'oval' },
    'tank_company':       { entity: '120500', ech: '15', hq: '0', m1: '00', m2: '00', expect: 'oval' },
    'artillery_battery':  { entity: '130300', ech: '15', hq: '0', m1: '00', m2: '00', expect: 'FA dot' },
    'artillery_platoon':  { entity: '130300', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'FA dot' },
    'mortar_section':     { entity: '130800', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'mortar circle' },
    'mortar_team':        { entity: '130800', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'mortar circle' },
    'at_team':            { entity: '120400', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'anti-armor' },
    'recon_team':         { entity: '121300', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'slash' },
    'recon_section':      { entity: '121300', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'slash' },
    'observation_post':   { entity: '121200', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'eye/obs' },
    'sniper_team':        { entity: '121500', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'sniper' },
    'engineer_platoon':   { entity: '140700', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'castle' },
    'engineer_section':   { entity: '140700', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'castle' },
    'combat_engr_plt':    { entity: '140700', ech: '14', hq: '0', m1: '09', m2: '00', expect: 'castle+combat' },
    'combat_engr_sect':   { entity: '140700', ech: '13', hq: '0', m1: '09', m2: '00', expect: 'castle+combat' },
    'combat_engr_team':   { entity: '140700', ech: '11', hq: '0', m1: '09', m2: '00', expect: 'castle+combat' },
    'mine_layer_sect':    { entity: '141600', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'mine laying' },
    'mine_layer_team':    { entity: '141600', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'mine laying' },
    'obst_breacher_tm':   { entity: '141400', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'mine clearing' },
    'obst_breacher_sect': { entity: '141400', ech: '13', hq: '0', m1: '00', m2: '00', expect: 'mine clearing' },
    'engr_recon_team':    { entity: '140703', ech: '11', hq: '0', m1: '00', m2: '00', expect: 'castle+slash' },
    'const_engr_plt':     { entity: '140700', ech: '14', hq: '0', m1: '12', m2: '00', expect: 'castle+construction' },
    'const_engr_sect':    { entity: '140700', ech: '13', hq: '0', m1: '12', m2: '00', expect: 'castle+construction' },
    'avlb_vehicle':       { entity: '140700', ech: '11', hq: '0', m1: '06', m2: '00', expect: 'castle+bridge' },
    'avlb_section':       { entity: '140700', ech: '13', hq: '0', m1: '06', m2: '00', expect: 'castle+bridge' },
    'logistics_unit':     { entity: '160600', ech: '14', hq: '0', m1: '00', m2: '00', expect: 'CSS icon' },
};

let html = `<!DOCTYPE html><html><head><title>Corrected SIDC Check</title>
<style>
body { background: #1a1a2e; color: #e0e0e0; font-family: sans-serif; padding: 20px; }
h1 { color: #4fc3f7; }
h2 { color: #ff9800; margin-top: 30px; }
.row { display: flex; align-items: center; gap: 15px; margin: 6px 0; padding: 6px 10px; background: #16213e; border-radius: 6px; }
.row.bad { background: #2a1010; border-left: 3px solid #e53935; }
.row.good { border-left: 3px solid #4caf50; }
.label { width: 180px; font-size: 12px; font-weight: 700; }
.expect { width: 150px; font-size: 11px; color: #888; }
.sidc { font-family: monospace; font-size: 10px; color: #666; width: 200px; }
.entity { font-family: monospace; font-size: 10px; color: #90caf9; width: 80px; }
</style></head><body><h1>Corrected SIDC Verification</h1>`;

let allOk = true;
for (const [name, info] of Object.entries(correctedCodes)) {
    const blueSidc = `10031${info.hq === '2' ? '0' : '0'}0${info.hq}${info.ech}${info.entity}${info.m1}${info.m2}`;
    // Actually construct properly:
    const sidc = `1003100${info.hq}${info.ech}${info.entity}${info.m1}${info.m2}`;
    const sym = new ms.Symbol(sidc, { size: 45 });
    const svg = sym.asSVG();

    const redSidc = `1006100${info.hq}${info.ech}${info.entity}${info.m1}${info.m2}`;
    const redSym = new ms.Symbol(redSidc, { size: 45 });
    const redSvg = redSym.asSVG();

    const paths = (svg.match(/<path/g) || []).length;
    const hasQ = svg.includes('>?<');
    const ok = !hasQ && paths > 1;
    if (!ok) allOk = false;

    html += `<div class="row ${ok ? 'good' : 'bad'}">
        <div class="label">${name}</div>
        <div>${svg}</div>
        <div>${redSvg}</div>
        <div class="entity">${info.entity}</div>
        <div class="expect">${info.expect}</div>
        <div class="sidc">${sidc}</div>
        <div style="font-size:11px;">${ok ? '✅' : '❌'} paths=${paths}</div>
    </div>`;

    console.log(`${name.padEnd(22)} ${sidc} entity=${info.entity} paths=${paths} ${ok ? '✅' : '❌ PROBLEM'}`);
}

html += `</body></html>`;
fs.writeFileSync('frontend/sidc_corrected.html', html);
console.log(`\n${allOk ? '✅ All OK' : '❌ Some problems'}`);
console.log('Generated frontend/sidc_corrected.html');

