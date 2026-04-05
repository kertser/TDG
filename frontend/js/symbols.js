/**
 * symbols.js – milsymbol.js wrapper for NATO military symbol rendering.
 */
const KSymbols = (() => {

    function createIcon(sidc, options = {}) {
        if (!sidc || !window.ms) {
            // Fallback: simple circle marker
            return L.divIcon({
                className: 'unit-marker-fallback',
                html: '<div style="width:12px;height:12px;border-radius:50%;background:#4fc3f7;border:2px solid #fff;"></div>',
                iconSize: [16, 16],
                iconAnchor: [8, 8],
            });
        }

        const sym = new ms.Symbol(sidc, {
            size: options.size || 35,
            direction: options.direction || 0,
            ...options,
        });

        const svg = sym.asSVG();
        const anchor = sym.getAnchor();

        return L.divIcon({
            className: '',
            html: svg,
            iconSize: [sym.getSize().width, sym.getSize().height],
            iconAnchor: [anchor.x, anchor.y],
        });
    }

    return { createIcon };
})();

