/**
 * @file bridge.js
 * JavaScript bridge between Stellarium Web Engine (WASM) and NiceGUI.
 *
 * Exposes window.stelBridge with methods for engine initialisation,
 * coordinate conversion, observer control, and camera animation.
 * Mouse events are converted to RA/Dec and dispatched as CustomEvents.
 */

window.stelBridge = (() => {
    "use strict";

    /** @type {object|null} Reference to the Stellarium engine instance. */
    let engine = null;

    /** @type {HTMLElement|null} Container element holding the canvas. */
    let container = null;

    /** @type {HTMLDivElement|null} Small overlay showing RA/Dec under cursor. */
    let coordOverlay = null;

    // ------------------------------------------------------------------
    // Internal helpers
    // ------------------------------------------------------------------

    /**
     * Format a decimal-degree value as a human-readable angle string.
     * @param {number} deg - Angle in decimal degrees.
     * @returns {string} Formatted string like "12.3456".
     */
    function fmtDeg(deg) {
        return deg.toFixed(4);
    }

    /**
     * Create (or return existing) coordinate overlay element.
     * @param {HTMLElement} parent - Container to append the overlay to.
     * @returns {HTMLDivElement} The overlay element.
     */
    function ensureOverlay(parent) {
        if (coordOverlay) return coordOverlay;
        coordOverlay = document.createElement("div");
        coordOverlay.style.cssText =
            "position:absolute;bottom:4px;left:4px;padding:2px 6px;" +
            "background:rgba(0,0,0,0.6);color:#ccc;font-size:11px;" +
            "font-family:monospace;pointer-events:none;z-index:10;" +
            "border-radius:3px;";
        parent.style.position = "relative";
        parent.appendChild(coordOverlay);
        return coordOverlay;
    }

    /**
     * Convert screen pixel coordinates to RA/Dec via the engine.
     * @param {number} x - X pixel coordinate relative to the canvas.
     * @param {number} y - Y pixel coordinate relative to the canvas.
     * @returns {{ra: number, dec: number}|null} Equatorial coordinates or null.
     */
    function pixelToRaDec(x, y) {
        if (!engine) return null;
        try {
            const world = engine.core.screenToWorld([x, y]);
            if (!world) return null;
            return { ra: world[0], dec: world[1] };
        } catch (_) {
            return null;
        }
    }

    /**
     * Attach mouse event listeners to the container.
     * @param {HTMLElement} el - The container element.
     */
    function attachMouseEvents(el) {
        el.addEventListener("click", (evt) => {
            const rect = el.getBoundingClientRect();
            const x = evt.clientX - rect.left;
            const y = evt.clientY - rect.top;
            const coords = pixelToRaDec(x, y);
            if (coords) {
                el.dispatchEvent(
                    new CustomEvent("map_click", {
                        detail: { ra: coords.ra, dec: coords.dec, x, y },
                    })
                );
            }
        });

        el.addEventListener("mousemove", (evt) => {
            const rect = el.getBoundingClientRect();
            const x = evt.clientX - rect.left;
            const y = evt.clientY - rect.top;
            const coords = pixelToRaDec(x, y);
            if (coords) {
                el.dispatchEvent(
                    new CustomEvent("map_mousemove", {
                        detail: { ra: coords.ra, dec: coords.dec, x, y },
                    })
                );
                const overlay = ensureOverlay(el);
                overlay.textContent =
                    "RA: " + fmtDeg(coords.ra) + " | Dec: " + fmtDeg(coords.dec);
            }
        });
    }

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    return {
        /**
         * Create canvas inside *containerId*, load WASM, and start the engine.
         * @param {string} containerId - DOM id of the container element.
         * @param {string} wasmUrl     - URL to the .wasm file.
         * @param {string} skydataUrl  - Base URL serving sky-data catalogues.
         * @returns {Promise<void>}
         */
        async initEngine(containerId, wasmUrl, skydataUrl) {
            container = document.getElementById(containerId);
            if (!container) {
                throw new Error("Container element not found: " + containerId);
            }

            const canvas = document.createElement("canvas");
            canvas.style.width = "100%";
            canvas.style.height = "100%";
            container.appendChild(canvas);

            // Resize observer keeps the canvas resolution in sync.
            const ro = new ResizeObserver((entries) => {
                for (const entry of entries) {
                    const { width, height } = entry.contentRect;
                    canvas.width = Math.round(width * devicePixelRatio);
                    canvas.height = Math.round(height * devicePixelRatio);
                }
            });
            ro.observe(container);

            // Load and initialise the Stellarium engine.
            // The engine constructor API varies by build; adapt as needed.
            if (typeof StelWebEngine !== "undefined") {
                engine = new StelWebEngine({
                    canvas: canvas,
                    wasmFile: wasmUrl,
                    skydataUrl: skydataUrl,
                });
            } else {
                console.warn(
                    "StelWebEngine not found — bridge loaded without engine. " +
                    "Ensure the Stellarium WASM script is loaded first."
                );
            }

            attachMouseEvents(container);
        },

        /**
         * Convert screen pixel coordinates to equatorial RA/Dec.
         * @param {number} x - X pixel coordinate.
         * @param {number} y - Y pixel coordinate.
         * @returns {{ra: number, dec: number}|null}
         */
        screenToWorld(x, y) {
            return pixelToRaDec(x, y);
        },

        /**
         * Convert equatorial RA/Dec to screen pixel coordinates.
         * @param {number} ra  - Right ascension in degrees.
         * @param {number} dec - Declination in degrees.
         * @returns {{x: number, y: number}|null}
         */
        worldToScreen(ra, dec) {
            if (!engine) return null;
            try {
                const pos = engine.core.worldToScreen([ra, dec]);
                if (!pos) return null;
                return { x: pos[0], y: pos[1] };
            } catch (_) {
                return null;
            }
        },

        /**
         * Return the current field-of-view in degrees.
         * @returns {number}
         */
        getFieldOfView() {
            if (!engine) return 0;
            return engine.core.fov;
        },

        /**
         * Set observer location and optional UTC time.
         * @param {number} lat     - Latitude in decimal degrees.
         * @param {number} lon     - Longitude in decimal degrees.
         * @param {string} [utcTime] - ISO-8601 UTC time string (optional).
         */
        setObserver(lat, lon, utcTime) {
            if (!engine) return;
            engine.core.observer.latitude = lat * (Math.PI / 180);
            engine.core.observer.longitude = lon * (Math.PI / 180);
            if (utcTime) {
                engine.core.observer.utc = new Date(utcTime).getTime() / 1000;
            }
        },

        /**
         * Animate the camera to look at a given RA/Dec.
         * @param {number} ra       - Right ascension in degrees.
         * @param {number} dec      - Declination in degrees.
         * @param {number} fov      - Target field-of-view in degrees.
         * @param {number} duration - Animation duration in seconds.
         */
        lookAt(ra, dec, fov, duration) {
            if (!engine) return;
            engine.core.lookat(
                [ra, dec],
                duration,
                fov * (Math.PI / 180)
            );
        },
    };
})();
