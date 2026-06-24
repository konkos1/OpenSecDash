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

    document.querySelectorAll("form[data-show-submit-on-dirty]")
        .forEach(form => {
            const submitButtons = document.querySelectorAll(`button[form="${CSS.escape(form.id)}"][type="submit"]`);
            const controls = document.querySelectorAll(`[form="${CSS.escape(form.id)}"]`);
            const showButtons = () => submitButtons.forEach(button => button.hidden = false);
            const hideButtons = () => submitButtons.forEach(button => button.hidden = true);
            controls.forEach(control => {
                control.addEventListener("input", showButtons);
                control.addEventListener("change", showButtons);
            });
            form.addEventListener("submit", hideButtons);
        });

    window.addEventListener("beforeunload", event => {
        if (dirtyForms.size === 0) {
            return;
        }

        event.preventDefault();
        event.returnValue = "";
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            document.querySelectorAll(".text-overlay-backdrop").forEach(overlay => overlay.remove());
        }
    });

    document.addEventListener("click", event => {
        const helpButton = event.target.closest(".help[data-tooltip]");
        const overlayTrigger = event.target.closest(".text-overlay-trigger[data-full-text]");
        const pathButton = event.target.closest(".path-truncate[data-full-text]");
        const overlayClose = event.target.closest("[data-text-overlay-close]");
        const overlayBackdrop = event.target.classList.contains("text-overlay-backdrop") ? event.target : null;

        document.querySelectorAll(".help-tooltip")
            .forEach(tooltip => tooltip.remove());

        if (overlayClose || overlayBackdrop) {
            event.preventDefault();
            event.stopPropagation();
            document.querySelectorAll(".text-overlay-backdrop").forEach(overlay => overlay.remove());
            return;
        }

        if (overlayTrigger || pathButton) {
            const trigger = overlayTrigger || pathButton;
            event.preventDefault();
            event.stopPropagation();
            showTextOverlay(trigger.dataset.fullText || trigger.textContent || "", trigger.dataset.overlayTitle || "");
            return;
        }

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

function showTextOverlay(text, overlayTitle) {
    document.querySelectorAll(".text-overlay-backdrop").forEach(overlay => overlay.remove());

    const backdrop = document.createElement("div");
    backdrop.className = "text-overlay-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");

    const panel = document.createElement("div");
    panel.className = "text-overlay";

    const header = document.createElement("div");
    header.className = "text-overlay-header";

    const title = document.createElement("strong");
    title.textContent = overlayTitle || "Path";

    const close = document.createElement("button");
    close.type = "button";
    close.className = "text-overlay-close";
    close.setAttribute("data-text-overlay-close", "true");
    close.setAttribute("aria-label", "Close");
    close.textContent = "×";

    const content = document.createElement("div");
    content.className = "text-overlay-content";
    content.textContent = text;

    header.appendChild(title);
    header.appendChild(close);
    panel.appendChild(header);
    panel.appendChild(content);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);
    close.focus();
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

function openSecDashLiveMode(initialLive, messages = {}) {
    return {
        live: Boolean(initialLive),
        liveIssue: "",
        snapshotAt: null,
        snapshotBefore: "",
        socket: null,
        reconnectTimer: null,
        storageKey: "opensecdash.events.mode",
        messages: {
            closed: messages.closed || "Live connection closed",
            unavailable: messages.unavailable || "Live currently unavailable",
            reconnecting: messages.reconnecting || "reconnecting",
        },

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
                this.freezeSnapshot(this.snapshotBefore || new URLSearchParams(window.location.search).get("snapshot_before"));
            }
        },

        toggle() {
            this.live = !this.live;
            window.sessionStorage.setItem(this.storageKey, this.live ? "live" : "snapshot");

            if (this.live) {
                this.liveIssue = "";
                this.snapshotAt = null;
                this.snapshotBefore = "";
                this.syncSnapshotInputs();
                this.connect();
                return;
            }

            this.liveIssue = "";
            this.disconnect();
            this.freezeSnapshot();
        },

        connect() {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                return;
            }

            const protocol = window.location.protocol === "https:" ? "wss" : "ws";
            try {
                this.socket = new WebSocket(`${protocol}://${window.location.host}/ws/events`);
            } catch (_error) {
                this.liveIssue = this.messages.unavailable;
                return;
            }
            this.socket.onopen = () => {
                this.liveIssue = "";
            };
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
            this.socket.onerror = () => {
                if (this.live) {
                    this.liveIssue = this.messages.unavailable;
                }
            };
            this.socket.onclose = event => {
                this.socket = null;
                if (this.live) {
                    this.liveIssue = event.code === 1008
                        ? this.messages.unavailable
                        : `${this.messages.closed} (${this.messages.reconnecting})`;
                    this.reconnectTimer = window.setTimeout(() => this.connect(), 2000);
                }
            };
        },

        disconnect() {
            if (this.reconnectTimer) {
                window.clearTimeout(this.reconnectTimer);
                this.reconnectTimer = null;
            }
            if (this.socket) {
                this.socket.close();
                this.socket = null;
            }
        },

        freezeSnapshot(existingCutoff = null) {
            const cutoffDate = existingCutoff ? new Date(existingCutoff) : new Date();
            const safeDate = Number.isNaN(cutoffDate.getTime()) ? new Date() : cutoffDate;
            this.snapshotBefore = safeDate.toISOString();
            this.snapshotAt = formatOpenSecDashDate(safeDate, configuredOpenSecDashTimezone());
            this.syncSnapshotInputs();
        },

        syncSnapshotInputs() {
            document.querySelectorAll("input[data-snapshot-before]").forEach(input => {
                input.value = this.live ? "" : this.snapshotBefore;
            });
        },

        refreshSnapshot() {
            window.sessionStorage.setItem(this.storageKey, "snapshot");
            const url = new URL(window.location.href);
            url.searchParams.delete("snapshot_before");
            window.location.href = url.toString();
        },
    };
}
