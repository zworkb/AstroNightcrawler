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

    /** @type {boolean} When true, clicks are captured for drawing instead of panning. */
    let drawModeActive = false;

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
     * Get the current camera state (center, FOV, canvas size).
     * @returns {object|null} Camera state or null if engine not ready.
     */
    function getCameraState() {
        if (!engine || !engine.core) return null;
        try {
            const canvas = container?.querySelector("canvas");
            return {
                fov: engine.core.fov * (180 / Math.PI),
                yaw: engine.core.observer.yaw * (180 / Math.PI),
                pitch: engine.core.observer.pitch * (180 / Math.PI),
                canvas_width: canvas?.width || 0,
                canvas_height: canvas?.height || 0,
            };
        } catch (_) {
            return null;
        }
    }

    /**
     * Attach mouse event listeners to the canvas element.
     * @param {HTMLElement} el - The container element.
     */
    function attachMouseEvents(el) {
        const canvas = el.querySelector("canvas");
        if (!canvas) {
            console.warn("stelBridge: no canvas found in container");
            return;
        }

        canvas.addEventListener("click", (evt) => {
            // Only handle clicks when drawing mode is active
            if (!drawModeActive) return;
            const rect = canvas.getBoundingClientRect();
            const x = Math.round(evt.clientX - rect.left);
            const y = Math.round(evt.clientY - rect.top);
            const cam = getCameraState();
            if (cam) {
                console.log("stelBridge: draw click at", x, y);
                el.dispatchEvent(
                    new CustomEvent("map_click", {
                        bubbles: true,
                        detail: { x, y, ...cam },
                    })
                );
            }
        });

        canvas.addEventListener("mousemove", (evt) => {
            const rect = canvas.getBoundingClientRect();
            const x = Math.round(evt.clientX - rect.left);
            const y = Math.round(evt.clientY - rect.top);
            const overlay = ensureOverlay(el);
            overlay.textContent = `Pixel: ${x}, ${y}`;
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

            // Load the Stellarium WASM module.
            if (typeof StelWebEngine === "undefined") {
                console.warn("StelWebEngine not found — loading script...");
                await new Promise((resolve, reject) => {
                    const s = document.createElement("script");
                    s.src = wasmUrl;
                    s.onload = resolve;
                    s.onerror = reject;
                    document.head.appendChild(s);
                });
            }

            if (typeof StelWebEngine === "undefined") {
                console.error("StelWebEngine still not available after loading script.");
                return;
            }

            // Initialize the engine via the module factory.
            engine = await new Promise((resolve) => {
                StelWebEngine({
                    canvas: canvas,
                    wasmFile: wasmUrl.replace(/\.js$/, ".wasm"),
                    onReady: (stel) => resolve(stel),
                });
            });

            // Register star data sources after engine is ready.
            if (engine && engine.core) {
                const base = skydataUrl.replace(/\/$/, "");
                try { engine.core.stars.addDataSource({ url: base + "/stars" }); } catch(_) {}
                try { engine.core.dsos.addDataSource({ url: base + "/dso" }); } catch(_) {}
                try {
                    engine.core.skycultures.addDataSource({
                        url: base + "/skycultures/western", key: "western"
                    });
                } catch(_) {}
                try { engine.core.milkyway.addDataSource({ url: base + "/surveys/milkyway" }); } catch(_) {}
            }

            attachMouseEvents(container);
            console.log("Stellarium Web Engine initialized.");
        },

        /**
         * Get the current camera state for Python-side projection.
         * @returns {object|null} {fov, yaw, pitch, canvas_width, canvas_height}
         */
        getCameraState() {
            return getCameraState();
        },

        /**
         * Toggle draw mode. In draw mode, clicks are captured for
         * point placement. In pan mode (default), Stellarium handles
         * all mouse interaction.
         * @param {boolean} active - True to enable draw mode.
         */
        setDrawMode(active) {
            drawModeActive = active;
            console.log("stelBridge: drawMode =", active);
        },

        /**
         * Convert screen pixel coordinates to equatorial RA/Dec.
         * NOTE: This is a placeholder — actual conversion is done in Python
         * via astropy using the camera state. Returns null for now.
         * @param {number} x - X pixel coordinate.
         * @param {number} y - Y pixel coordinate.
         * @returns {{ra: number, dec: number}|null}
         */
        screenToWorld(x, y) {
            // Placeholder — Python handles this via astropy
            return null;
        },

        /**
         * Convert equatorial RA/Dec to screen pixel coordinates.
         * NOTE: This is a placeholder — actual conversion is done in Python.
         * @param {number} ra  - Right ascension in degrees.
         * @param {number} dec - Declination in degrees.
         * @returns {{x: number, y: number}|null}
         */
        worldToScreen(ra, dec) {
            // Placeholder — Python handles this via astropy
            return null;
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
