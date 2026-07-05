document.addEventListener("DOMContentLoaded", () => {
    localizeOpenSecDashDatetimes();
    localizeOpenSecDashCountries();

    // Every periodically/live-refreshed region on any page (Events/Access,
    // Dashboard, CrowdSec, Diagnostics, Assets) goes through an htmx outerHTML
    // swap. That replaces the DOM node, which would otherwise silently reset
    // both the page's vertical scroll and any inner horizontal table scroll
    // back to the top/left on every refresh - especially disruptive on small
    // (mobile) screens where users are scrolled deep into a list. Restoring
    // it here, once, covers every such region instead of duplicating this in
    // each place that triggers a refresh.
    let pendingScrollRestore = null;

    document.body.addEventListener("htmx:beforeSwap", event => {
        const target = event.detail && event.detail.target;
        if (!target || !target.id) {
            return;
        }
        pendingScrollRestore = {
            id: target.id,
            windowScrollY: window.scrollY,
            scrollLefts: Array.from(target.querySelectorAll(".overflow-x-auto")).map(el => el.scrollLeft),
        };
    });

    // A failed/cancelled swap must drop the captured position, or it would be
    // (wrongly) applied to the next unrelated swap and jump the page around.
    document.body.addEventListener("htmx:swapError", () => { pendingScrollRestore = null; });
    document.body.addEventListener("htmx:responseError", () => { pendingScrollRestore = null; });

    document.body.addEventListener("htmx:afterSwap", () => {
        localizeOpenSecDashDatetimes();
        localizeOpenSecDashCountries();

        if (!pendingScrollRestore) {
            return;
        }
        const { id, windowScrollY, scrollLefts } = pendingScrollRestore;
        pendingScrollRestore = null;
        const target = document.getElementById(id);
        if (target) {
            Array.from(target.querySelectorAll(".overflow-x-auto")).forEach((el, index) => {
                if (scrollLefts[index] !== undefined) {
                    el.scrollLeft = scrollLefts[index];
                }
            });
        }
        window.scrollTo(0, windowScrollY);
    });

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

    document.querySelectorAll("form[data-submit-busy]")
        .forEach(form => {
            form.addEventListener("submit", event => {
                if (event.defaultPrevented) {
                    return;
                }
                const associatedButton = form.id
                    ? document.querySelector(`button[form="${CSS.escape(form.id)}"][type="submit"], button[form="${CSS.escape(form.id)}"]:not([type])`)
                    : null;
                const button = form.querySelector('button[type="submit"], button:not([type])') || associatedButton;
                if (!button || button.disabled) {
                    event.preventDefault();
                    return;
                }
                const label = button.dataset.busyLabel;
                button.disabled = true;
                button.setAttribute("aria-busy", "true");
                if (label) {
                    button.innerHTML = `<span class="button-spinner" aria-hidden="true"></span>${label}`;
                }
            });
        });

    const dirtyForms = new Set();
    let skipBeforeUnloadWarning = false;

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
        if (skipBeforeUnloadWarning || dirtyForms.size === 0) {
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
        const columnsOpen = event.target.closest("[data-columns-open]");
        const columnsClose = event.target.closest("[data-columns-close]");
        const columnsDialogBackdrop = event.target.tagName === "DIALOG" && event.target.classList.contains("columns-dialog") ? event.target : null;
        const overlayBackdrop = event.target.classList.contains("text-overlay-backdrop") ? event.target : null;

        document.querySelectorAll(".help-tooltip")
            .forEach(tooltip => tooltip.remove());

        if (overlayClose || overlayBackdrop) {
            event.preventDefault();
            event.stopPropagation();
            document.querySelectorAll(".text-overlay-backdrop").forEach(overlay => overlay.remove());
            return;
        }

        if (columnsOpen) {
            event.preventDefault();
            const dialog = document.getElementById(columnsOpen.dataset.columnsOpen || "");
            if (dialog && typeof dialog.showModal === "function") {
                dialog.showModal();
            }
            return;
        }

        if (columnsClose || columnsDialogBackdrop) {
            event.preventDefault();
            const dialog = columnsDialogBackdrop || document.getElementById(columnsClose.dataset.columnsClose || "");
            if (dialog && typeof dialog.close === "function") {
                dialog.close();
            }
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
            document.body.appendChild(tooltip);

            const gap = 8;
            const margin = 8;
            const buttonRect = helpButton.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();
            const left = Math.min(
                Math.max(buttonRect.left + (buttonRect.width / 2) - (tooltipRect.width / 2), margin),
                window.innerWidth - tooltipRect.width - margin,
            );
            let top = buttonRect.top - tooltipRect.height - gap;
            if (top < margin) {
                top = buttonRect.bottom + gap;
            }
            if (top + tooltipRect.height > window.innerHeight - margin) {
                top = Math.max(margin, window.innerHeight - tooltipRect.height - margin);
            }
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${top}px`;
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
            return;
        }

        skipBeforeUnloadWarning = true;
        dirtyForms.clear();
    }, true);
});

function localizeOpenSecDashCountries() {
    if (!Intl.DisplayNames) {
        return;
    }

    const language = document.documentElement.lang || navigator.language || "en";
    const displayNames = new Intl.DisplayNames([language], {type: "region"});
    // .of() throws RangeError for malformed codes; events created via the API
    // can carry arbitrary country values, and one bad code must not abort
    // localization (and everything after it in the htmx:afterSwap handler).
    const regionLabel = code => {
        try {
            return displayNames.of(code) || code;
        } catch (_error) {
            return code;
        }
    };
    document.querySelectorAll(".osd-country[data-country-code]")
        .forEach(element => {
            const code = element.dataset.countryCode;
            if (!code) {
                return;
            }
            element.textContent = regionLabel(code);
        });
    document.querySelectorAll("[data-country-title]")
        .forEach(element => {
            const code = element.dataset.countryTitle;
            if (!code) {
                return;
            }
            const label = regionLabel(code);
            const count = element.dataset.countryCount;
            element.setAttribute("title", count ? `${label} · ${count}` : label);
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

function openSecDashLiveMode(initialLive, messages = {}, resultsSelector = null) {
    return {
        live: Boolean(initialLive),
        liveIssue: "",
        snapshotAt: null,
        snapshotBefore: "",
        socket: null,
        reconnectTimer: null,
        storageKey: "opensecdash.events.mode",
        resultsSelector,
        refreshThrottleMs: 3000,
        refreshTimer: null,
        lastRefreshAt: 0,
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
                    this.scheduleRefresh();
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
            if (this.refreshTimer) {
                window.clearTimeout(this.refreshTimer);
                this.refreshTimer = null;
            }
            if (this.socket) {
                this.socket.close();
                this.socket = null;
            }
        },

        // New events arrive roughly once a second while live; throttling avoids
        // replacing the table on every single tick, which felt jerky and closed
        // any open overlay/dialog on every refresh when this used a full reload.
        scheduleRefresh() {
            if (this.refreshTimer) {
                return;
            }
            const wait = Math.max(0, this.refreshThrottleMs - (Date.now() - this.lastRefreshAt));
            this.refreshTimer = window.setTimeout(() => {
                this.refreshTimer = null;
                this.lastRefreshAt = Date.now();
                this.refreshResults();
            }, wait);
        },

        refreshResults() {
            if (!this.resultsSelector || typeof htmx === "undefined") {
                window.location.reload();
                return;
            }
            // Horizontal/vertical scroll position across the swap is handled
            // globally for every htmx swap in the app - see htmx:beforeSwap /
            // htmx:afterSwap near the top of this file.
            htmx.ajax("GET", window.location.href, {
                target: this.resultsSelector,
                select: this.resultsSelector,
                swap: "outerHTML",
            });
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

// Lighter-weight sibling of openSecDashLiveMode() for pages that don't have a
// Live/Snapshot toggle (Dashboard, CrowdSec): always connects unless the
// "Auto-refresh" setting is off, and has no snapshot mode of its own.
function openSecDashAutoRefresh(resultsSelector, enabled) {
    return {
        enabled: Boolean(enabled),
        socket: null,
        reconnectTimer: null,
        refreshTimer: null,
        lastRefreshAt: 0,
        refreshThrottleMs: 3000,

        init() {
            if (this.enabled) {
                this.connect();
            }
        },

        connect() {
            if (!this.enabled || (this.socket && this.socket.readyState === WebSocket.OPEN)) {
                return;
            }
            const protocol = window.location.protocol === "https:" ? "wss" : "ws";
            try {
                this.socket = new WebSocket(`${protocol}://${window.location.host}/ws/events`);
            } catch (_error) {
                return;
            }
            this.socket.onmessage = event => {
                let payload = null;
                try {
                    payload = JSON.parse(event.data);
                } catch (_error) {
                    return;
                }
                if (this.enabled && payload.type === "events_changed") {
                    this.scheduleRefresh();
                }
            };
            this.socket.onclose = () => {
                this.socket = null;
                if (this.enabled) {
                    this.reconnectTimer = window.setTimeout(() => this.connect(), 2000);
                }
            };
        },

        scheduleRefresh() {
            if (this.refreshTimer) {
                return;
            }
            const wait = Math.max(0, this.refreshThrottleMs - (Date.now() - this.lastRefreshAt));
            this.refreshTimer = window.setTimeout(() => {
                this.refreshTimer = null;
                this.lastRefreshAt = Date.now();
                this.refreshResults();
            }, wait);
        },

        refreshResults() {
            if (typeof htmx === "undefined") {
                return;
            }
            htmx.ajax("GET", window.location.href, {
                target: resultsSelector,
                select: resultsSelector,
                swap: "outerHTML",
            });
        },
    };
}
