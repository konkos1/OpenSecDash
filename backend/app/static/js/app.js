document.addEventListener("DOMContentLoaded", () => {
    localizeOpenSecDashDatetimes();
    localizeOpenSecDashCountries();

    document.querySelectorAll("[data-uppercase-value]")
        .forEach(input => {
            input.addEventListener("input", () => {
                const start = input.selectionStart;
                const end = input.selectionEnd;
                input.value = input.value.toUpperCase();
                input.setSelectionRange(start, end);
            });
        });

    document.querySelectorAll("[data-confirm]")
        .forEach(element => {
            element.addEventListener("submit", event => {
                const text = element.dataset.confirm;

                if (!confirm(text)) {
                    event.preventDefault();
                }
            });
        });

    const dirtyForms = new Set();

    document.querySelectorAll("form[data-unsaved-warning]")
        .forEach(form => {
            const markDirty = () => dirtyForms.add(form);
            const markClean = () => dirtyForms.delete(form);

            form.addEventListener("input", markDirty);
            form.addEventListener("change", markDirty);
            form.addEventListener("submit", markClean);
        });

    window.addEventListener("beforeunload", event => {
        if (dirtyForms.size === 0) {
            return;
        }

        event.preventDefault();
        event.returnValue = "";
    });

    document.addEventListener("click", event => {
        const helpButton = event.target.closest(".help[data-tooltip]");

        document.querySelectorAll(".help-tooltip")
            .forEach(tooltip => tooltip.remove());

        if (helpButton) {
            event.preventDefault();
            event.stopPropagation();

            const tooltip = document.createElement("div");
            tooltip.className = "help-tooltip";
            tooltip.textContent = helpButton.dataset.tooltip;
            helpButton.appendChild(tooltip);
            return;
        }

        if (dirtyForms.size === 0) {
            return;
        }

        const link = event.target.closest("a[href]");
        const backButton = event.target.closest("button[data-check-unsaved]");

        if (!link && !backButton) {
            return;
        }

        if (link) {
            const href = link.getAttribute("href") || "";

            if (href.startsWith("#") || link.target === "_blank") {
                return;
            }
        }

        const form = Array.from(dirtyForms)[0];
        const message = form.dataset.unsavedWarning;

        if (!confirm(message)) {
            event.preventDefault();
            event.stopPropagation();
        }
    }, true);
});

function localizeOpenSecDashCountries() {
    if (!Intl.DisplayNames) {
        return;
    }

    const language = document.documentElement.lang || navigator.language || "en";
    const displayNames = new Intl.DisplayNames([language], {type: "region"});
    document.querySelectorAll(".osd-country[data-country-code]")
        .forEach(element => {
            const code = element.dataset.countryCode;
            if (!code) {
                return;
            }
            element.textContent = displayNames.of(code) || code;
        });
}

function formatOpenSecDashDate(date, timezone) {
    const options = {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    };

    if (timezone && timezone !== "auto") {
        options.timeZone = timezone;
    }

    try {
        const parts = new Intl.DateTimeFormat("en-CA", options)
            .formatToParts(date)
            .reduce((result, part) => {
                result[part.type] = part.value;
                return result;
            }, {});
        return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
    } catch (_error) {
        const pad = value => String(value).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    }
}

function configuredOpenSecDashTimezone() {
    return document.body.dataset.uiTimezone || "auto";
}

function localizeOpenSecDashDatetimes() {
    document.querySelectorAll(".osd-datetime[data-datetime-utc]")
        .forEach(element => {
            const isoUtc = element.dataset.datetimeUtc;
            const timezone = element.dataset.timezone || configuredOpenSecDashTimezone();

            if (!isoUtc) {
                return;
            }

            const date = new Date(isoUtc);

            if (Number.isNaN(date.getTime())) {
                return;
            }

            element.textContent = formatOpenSecDashDate(date, timezone);
        });
}

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
            this.socket.onmessage = event => {
                let payload = null;
                try {
                    payload = JSON.parse(event.data);
                } catch (_error) {
                    return;
                }

                if (this.live && payload.type === "events_changed") {
                    window.location.reload();
                }
            };
            this.socket.onclose = () => {
                if (this.live) {
                    window.setTimeout(() => this.connect(), 2000);
                }
            };
        },

        disconnect() {
            if (this.socket) {
                this.socket.close();
                this.socket = null;
            }
        },

        freezeSnapshot() {
            this.snapshotAt = formatOpenSecDashDate(new Date(), configuredOpenSecDashTimezone());
        },

        refreshSnapshot() {
            window.sessionStorage.setItem(this.storageKey, "snapshot");
            window.location.reload();
        },
    };
}
