/**
 * symbols.js – milsymbol.js wrapper for NATO military symbol rendering.
 *
 * Icons use L.divIcon → fixed pixel size regardless of map zoom.
 * Size varies by unit echelon: company-level → larger, team/individual → smaller.
 */
const KSymbols = (() => {

    // Unit type → icon pixel size.  Company+ bigger, team/individual smaller.
    const SIZE_MAP = {
        'tank_company':      34,
        'mech_company':      34,
        'infantry_company':  32,
        'infantry_platoon':  28,
        'mortar_section':    26,
        'at_team':           26,
        'recon_team':        24,
        'observation_post':  22,
        'sniper_team':       22,
    };
    const DEFAULT_SIZE = 28;

    function createIcon(sidc, options = {}) {
        const unitType = options.unitType || '';
        const size = options.size || SIZE_MAP[unitType] || DEFAULT_SIZE;

        if (!sidc || !window.ms) {
            const r = Math.max(Math.round(size * 0.35), 5);
            return L.divIcon({
                className: 'unit-marker-fallback',
                html: `<div style="width:${r*2}px;height:${r*2}px;border-radius:50%;background:#4fc3f7;border:2px solid #fff;"></div>`,
                iconSize: [r * 2 + 4, r * 2 + 4],
                iconAnchor: [r + 2, r + 2],
            });
        }

        const sym = new ms.Symbol(sidc, {
            size: size,
            direction: options.direction || 0,
        });

        const svg = sym.asSVG();
        const anchor = sym.getAnchor();

        return L.divIcon({
            className: 'unit-icon',
            html: svg,
            iconSize: [sym.getSize().width, sym.getSize().height],
            iconAnchor: [anchor.x, anchor.y],
        });
    }

    return { createIcon };
})();

