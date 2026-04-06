/**
 * websocket.js – WebSocket client with reconnection and message dispatch.
 */
const KWebSocket = (() => {
    let ws = null;
    let sessionId = null;
    let token = null;
    let handlers = {};
    let reconnectTimer = null;

    let _disconnecting = false;

    function connect(sessId, authToken) {
        sessionId = sessId;
        token = authToken;
        _disconnecting = false;

        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = `${protocol}://${location.host}/ws/${sessionId}?token=${token}`;

        ws = new WebSocket(url);

        ws.onopen = () => {
            console.log('WebSocket connected');
            clearTimeout(reconnectTimer);
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                const handler = handlers[msg.type];
                if (handler) handler(msg.data);
            } catch (err) {
                console.warn('WS message parse error:', err);
            }
        };

        ws.onclose = () => {
            if (_disconnecting) return; // Don't reconnect if intentionally disconnected
            console.log('WebSocket closed, reconnecting in 3s...');
            reconnectTimer = setTimeout(() => connect(sessionId, token), 3000);
        };

        ws.onerror = (err) => {
            console.error('WebSocket error:', err);
        };
    }

    function send(type, data = {}) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type, data }));
        }
    }

    function on(type, handler) {
        handlers[type] = handler;
    }

    function disconnect() {
        _disconnecting = true;
        clearTimeout(reconnectTimer);
        if (ws) {
            ws.close();
            ws = null;
        }
        handlers = {};
        sessionId = null;
        token = null;
    }

    return { connect, send, on, disconnect };
})();

