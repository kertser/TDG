/**
 * symbols.js – milsymbol.js wrapper for NATO military symbol rendering.
 *
 * Icons use L.divIcon → fixed pixel size regardless of map zoom.
 * Size varies by unit echelon: company-level → larger, team/individual → smaller.
 * At low zoom levels, markers are scaled down to prevent clutter.
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

    /** Compute zoom-based scale factor for unit markers. */
    function getZoomScale(zoom) {
        if (zoom >= 13) return 1.0;
        if (zoom >= 12) return 0.85;
        if (zoom >= 11) return 0.7;
        if (zoom >= 10) return 0.55;
        if (zoom >= 9)  return 0.4;
        if (zoom >= 8)  return 0.3;
        return 0.22;
    }

    /**
     * Get the zoom "bucket" — only re-render when bucket changes.
     * This prevents re-rendering on every fractional zoom step.
     */
    function getZoomBucket(zoom) {
        if (zoom >= 13) return 'full';
        if (zoom >= 11) return 'large';
        if (zoom >= 10) return 'medium';
        if (zoom >= 8)  return 'small';
        return 'tiny';
    }

    function createIcon(sidc, options = {}) {
        const unitType = options.unitType || '';
        const baseSize = options.size || SIZE_MAP[unitType] || DEFAULT_SIZE;
        const scale = options.zoomScale || 1.0;
        const size = Math.max(8, Math.round(baseSize * scale));

        if (!sidc || !window.ms) {
            const r = Math.max(Math.round(size * 0.35), 3);
            return L.divIcon({
                className: 'unit-marker-fallback',
                html: `<div style="width:${r*2}px;height:${r*2}px;border-radius:50%;background:#4fc3f7;border:2px solid #fff;"></div>`,
                iconSize: [r * 2 + 4, r * 2 + 4],
                iconAnchor: [r + 2, r + 2],
            });
        }

        // Build milsymbol options — do NOT pass direction (we draw our own
        // movement arrows in units.js; milsymbol's direction indicator adds
        // unwanted staff/arrow lines extending from the symbol frame).
        const symOpts = { size: size };

        // Optional: show unit size info below symbol
        if (options.infoFields) {
            if (options.infoFields.uniqueDesignation)
                symOpts.uniqueDesignation = options.infoFields.uniqueDesignation;
        }

        const sym = new ms.Symbol(sidc, symOpts);

        const svg = sym.asSVG();
        const anchor = sym.getAnchor();

        return L.divIcon({
            className: 'unit-icon',
            html: svg,
            iconSize: [sym.getSize().width, sym.getSize().height],
            iconAnchor: [anchor.x, anchor.y],
        });
    }

    return { createIcon, getZoomScale, getZoomBucket };
})();

