const msModule = require('milsymbol');
const ms = msModule.default || msModule;
const fs = require('fs');

// Render key unit types and save SVGs for visual inspection
const testSidcs = {
    // Current codes from config
    'infantry':     { sidc: '10031000141205000000', expect: 'X cross' },
    'armor':        { sidc: '10031000141202000000', expect: 'oval/track' },
    'mech_inf':     { sidc: '10031000141205010000', expect: 'X + oval' },
    'artillery':    { sidc: '10031000151302000000', expect: 'dot' },
    'mortar':       { sidc: '10031000131303000000', expect: 'circle' },
    'anti_armor':   { sidc: '10031000111203000000', expect: 'anti-tank icon' },
    'engineer':     { sidc: '10031000141204000000', expect: 'castle' },
    'recon':        { sidc: '10031000111207000000', expect: 'slash' },
    'observer':     { sidc: '10031000111206000000', expect: 'eye/binocular' },
    'logistics':    { sidc: '10031000141607000000', expect: 'supply icon' },
    'hq':           { sidc: '10031002151100000000', expect: 'HQ staff + star' },
    'sniper':       { sidc: '10031000111205080000', expect: 'sniper crosshair' },
    'combat_engr':  { sidc: '10031000141204010000', expect: 'combat engineer' },
    'mine_layer':   { sidc: '10031000131204030000', expect: 'mine icon' },
    'avlb':         { sidc: '10031000111204060000', expect: 'bridge icon' },
};

// Generate an HTML file for visual inspection
let html = `<!DOCTYPE html><html><head><title>SIDC Visual Check</title>
<style>
body { background: #1a1a2e; color: #e0e0e0; font-family: sans-serif; padding: 20px; }
.row { display: flex; align-items: center; gap: 20px; margin: 8px 0; padding: 8px; background: #16213e; border-radius: 6px; }
.label { width: 200px; font-size: 13px; }
.expect { width: 200px; font-size: 11px; color: #888; }
.sidc { font-family: monospace; font-size: 10px; color: #666; width: 200px; }
</style></head><body><h1>SIDC Visual Verification</h1>`;

for (const [name, info] of Object.entries(testSidcs)) {
    const sym = new ms.Symbol(info.sidc, { size: 50 });
    const svg = sym.asSVG();

    // Also render hostile version
    const hostileSidc = info.sidc.substring(0, 3) + '6' + info.sidc.substring(4);
    const symRed = new ms.Symbol(hostileSidc, { size: 50 });
    const svgRed = symRed.asSVG();

    html += `<div class="row">
        <div class="label"><b>${name}</b></div>
        <div>${svg}</div>
        <div>${svgRed}</div>
        <div class="expect">Expected: ${info.expect}</div>
        <div class="sidc">${info.sidc}</div>
    </div>`;
}

// Also compare with what NATO symbology should look like - test alternative codes
html += `<h2>Alternative Entity Codes for Comparison</h2>`;
const altCodes = {
    // Let's try codes from the actual 2525D standard tables
    'Entity 120100 (AirDef)':   '10031000140120100000',
    'Entity 120200 (Armor)':    '10031000140120200000',
    'Entity 120300 (??)':       '10031000140120300000',
    'Entity 120400 (??)':       '10031000140120400000',
    'Entity 120500 (Inf)':      '10031000140120500000',
    'Entity 120600 (Obs)':      '10031000140120600000',
    'Entity 120700 (Recon)':    '10031000140120700000',
    'Entity 120800 (??)':       '10031000140120800000',
    'Entity 120900 (??)':       '10031000140120900000',
    'Entity 121000 (??)':       '10031000140121000000',
    'Entity 121100 (??)':       '10031000140121100000',
    'Entity 121200 (??)':       '10031000140121200000',
    'Entity 121300 (??)':       '10031000140121300000',
    'Entity 130100 (??)':       '10031000140130100000',
    'Entity 130200 (FA)':       '10031000140130200000',
    'Entity 130300 (Mort)':     '10031000140130300000',
    'Entity 140100 (??)':       '10031000140140100000',
    'Entity 150100 (??)':       '10031000140150100000',
    'Entity 160100 (??)':       '10031000140160100000',
    'Entity 160200 (??)':       '10031000140160200000',
    'Entity 160300 (??)':       '10031000140160300000',
    'Entity 160700 (Log)':      '10031000140160700000',
};

for (const [name, sidc] of Object.entries(altCodes)) {
    const sym = new ms.Symbol(sidc, { size: 50 });
    const svg = sym.asSVG();
    const entity = sidc.substring(10, 16);
    html += `<div class="row">
        <div class="label">${name}</div>
        <div>${svg}</div>
        <div class="sidc">${sidc} (${entity})</div>
    </div>`;
}

html += `</body></html>`;
fs.writeFileSync('frontend/sidc_check.html', html);
console.log('Generated frontend/sidc_check.html — open in browser to visually verify');

// Also dump raw SVG paths for key types to detect differences
console.log('\n=== SVG path analysis ===');
for (const [name, info] of Object.entries(testSidcs)) {
    const sym = new ms.Symbol(info.sidc, { size: 50 });
    const svg = sym.asSVG();
    // Extract all d="" attributes from paths (these define the icon shapes)
    const dPaths = [...svg.matchAll(/d="([^"]+)"/g)].map(m => m[1]);
    console.log(`\n${name} (${info.sidc.substring(10,16)}):`);
    dPaths.forEach((d, i) => console.log(`  path${i}: ${d.substring(0, 80)}${d.length > 80 ? '...' : ''}`));
}

