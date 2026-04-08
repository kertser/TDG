const msModule = require('milsymbol');
const ms = msModule.default || msModule;
const fs = require('fs');

const types = JSON.parse(fs.readFileSync('frontend/config/unit_types.json', 'utf8'));

console.log('=== SIDC Verification with milsymbol v2.2.0 ===\n');

let ok = 0, blank = 0, unknown = 0;

for (const [key, info] of Object.entries(types)) {
    if (key.startsWith('_')) continue;

    const sym = new ms.Symbol(info.sidc_blue, { size: 30 });
    const svg = sym.asSVG();

    const hasQ = svg.includes('>?<');
    const entity = info.sidc_blue.substring(10, 16);
    const echelon = info.sidc_blue.substring(8, 10);
    const hq = info.sidc_blue.charAt(7);

    // Count SVG elements to detect blank/empty icons
    const paths = (svg.match(/<path/g) || []).length;
    const circles = (svg.match(/<circle/g) || []).length;
    const ellipses = (svg.match(/<ellipse/g) || []).length;
    const texts = (svg.match(/<text/g) || []).length;
    const useElements = (svg.match(/<use/g) || []).length;

    // Extract unique icon elements (beyond the frame rectangle)
    const totalIconElements = paths + circles + ellipses + useElements;

    let status;
    if (hasQ) {
        status = '❌ UNKNOWN';
        unknown++;
    } else if (totalIconElements <= 2) {
        status = '⚠️  BLANK/FRAME ONLY';
        blank++;
    } else {
        status = '✅ OK';
        ok++;
    }

    console.log(`${key.padEnd(32)} entity=${entity} ech=${echelon} hq=${hq} elements=${totalIconElements.toString().padStart(2)} ${status}`);
}

console.log(`\n=== Summary: ${ok} OK, ${blank} Blank/Frame, ${unknown} Unknown ===`);

// Now test some known-good entity codes to see what milsymbol recognizes
console.log('\n=== Testing known entity codes ===');
const testCodes = {
    '110000': 'C2/HQ',
    '120100': 'Air Defense',
    '120200': 'Armor',
    '120300': 'Aviation Fixed Wing or Anti-Armor?',
    '120400': 'Aviation Rotary Wing or Engineer?',
    '120500': 'Infantry',
    '120600': 'Observer',
    '120700': 'Reconnaissance',
    '130200': 'Field Artillery',
    '130300': 'Mortar',
    '160700': 'Logistics',
    // Alternative entity codes
    '121100': 'Infantry (alt)',
    '121102': 'Mech Infantry (alt)',
    '121300': 'Engineer (alt)',
    '120800': 'Signal?',
    '120900': 'Special Operations?',
    '121000': 'Combined Arms?',
    '121200': 'Armor (alt)?',
    '130100': 'Fires Air Defense?',
    '140700': 'EOD?',
    '150100': 'Administrative?',
    '160200': 'Maintenance?',
    '160301': 'Medical?',
};

for (const [entity, desc] of Object.entries(testCodes)) {
    const sidc = '10031000140' + entity + '0000'.substring(0, 20 - 11 - entity.length);
    const fullSidc = ('10031000140' + entity + '00000000').substring(0, 20);
    const sym = new ms.Symbol(fullSidc, { size: 30 });
    const svg = sym.asSVG();
    const hasQ = svg.includes('>?<');
    const paths = (svg.match(/<path/g) || []).length;
    const circles = (svg.match(/<circle/g) || []).length;
    const ellipses = (svg.match(/<ellipse/g) || []).length;
    const useElements = (svg.match(/<use/g) || []).length;
    const total = paths + circles + ellipses + useElements;
    const status = hasQ ? '❌ UNKNOWN' : total <= 2 ? '⚠️  BLANK' : '✅ OK';
    console.log(`  ${entity} (${desc.padEnd(30)}) sidc=${fullSidc} elements=${total.toString().padStart(2)} ${status}`);
}



