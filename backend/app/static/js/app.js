document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-confirm]")
        .forEach(element => {
            element.addEventListener("submit", event => {
                const text = element.dataset.confirm;

                if (!confirm(text)) {
                    event.preventDefault();
                }
            });
        });
});

function openSecDashLiveMode(initialLive) {
    return {
        live: Boolean(initialLive),
        snapshotAt: null,
        socket: null,
        storageKey: "opensecdash.events.mode",

        init() {
            const storedMode = window.sessionStorage.getItem(this.storageKey);

            if (storedMode === "live") {
                this.live = true;
            }

            if (storedMode === "snapshot") {
                this.live = false;
            }

            if (this.live) {
                this.connect();
            } else {
                this.freezeSnapshot();
            }
        },

        toggle() {
            this.live = !this.live;
            window.sessionStorage.setItem(this.storageKey, this.live ? "live" : "snapshot");

            if (this.live) {
                this.snapshotAt = null;
                this.connect();
                return;
            }

            this.disconnect();
            this.freezeSnapshot();
        },

        connect() {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                return;
            }

            const protocol = window.location.protocol === "https:" ? "wss" : "ws";
            this.socket = new WebSocket(`${protocol}://${window.location.host}/ws/events`);
        },

        disconnect() {
            if (this.socket) {
                this.socket.close();
                this.socket = null;
            }
        },

        freezeSnapshot() {
            this.snapshotAt = new Date().toLocaleString(undefined, {
                year: "numeric",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
            });
        },

        refreshSnapshot() {
            window.sessionStorage.setItem(this.storageKey, "snapshot");
            window.location.reload();
        },
    };
}
