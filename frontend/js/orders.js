/**
 * orders.js – Order entry panel: submit text orders with selected units.
 */
const KOrders = (() => {

    function init(sessionId, token) {
        const submitBtn = document.getElementById('submit-order-btn');
        const textArea = document.getElementById('order-text');
        const clearSelBtn = document.getElementById('clear-unit-selection-btn');

        if (submitBtn) {
            submitBtn.addEventListener('click', async () => {
                const text = textArea.value.trim();
                if (!text) return;

                const selectedIds = KUnits.getSelectedIds();

                try {
                    const resp = await fetch(`/api/sessions/${sessionId}/orders`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`,
                        },
                        body: JSON.stringify({
                            original_text: text,
                            target_unit_ids: selectedIds.length > 0 ? selectedIds : null,
                        }),
                    });
                    const result = await resp.json();
                    console.log('Order submitted:', result);
                    textArea.value = '';

                    const unitNames = selectedIds.length > 0
                        ? ` → [${KUnits.getAllUnits().filter(u => selectedIds.includes(u.id)).map(u => u.name).join(', ')}]`
                        : '';
                    KGameLog.addEntry(`Order submitted: ${text}${unitNames}`, 'order');

                    // Clear selection after submit
                    KUnits.clearSelection();
                } catch (err) {
                    console.error('Order submit failed:', err);
                }
            });
        }

        if (clearSelBtn) {
            clearSelBtn.addEventListener('click', () => KUnits.clearSelection());
        }
    }

    return { init };
})();
