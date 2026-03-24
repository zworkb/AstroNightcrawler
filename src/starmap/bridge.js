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
     * Approximate Az/Alt to RA/Dec conversion.
     * Good enough for cursor display (~0.1° accuracy).
     * @param {number} az  - Azimuth in degrees.
     * @param {number} alt - Altitude in degrees.
     * @param {number} lat - Observer latitude in degrees.
     * @param {number} lon - Observer longitude in degrees.
     * @param {number} mjd - Modified Julian Date (UTC).
     * @returns {{ra: number, dec: number}} RA and Dec in degrees.
     */
    function azaltToRaDec(az, alt, lat, lon, mjd) {
        const D = Math.PI / 180;
        const azR = az * D;
        const altR = alt * D;
        const latR = lat * D;

        // Approximate Local Sidereal Time from MJD
        // LST ≈ 280.46061837 + 360.98564736629 * (MJD - 51544.5) + lon
        const lst = (280.46061837 + 360.98564736629 * (mjd - 51544.5) + lon) % 360;
        const lstR = lst * D;

        // Alt/Az to Hour Angle / Dec
        const sinDec = Math.sin(altR) * Math.sin(latR) +
                       Math.cos(altR) * Math.cos(latR) * Math.cos(azR);
        const dec = Math.asin(sinDec);

        const cosHA = (Math.sin(altR) - Math.sin(latR) * sinDec) /
                      (Math.cos(latR) * Math.cos(dec));
        let ha = Math.acos(Math.max(-1, Math.min(1, cosHA)));
        if (Math.sin(azR) > 0) ha = 2 * Math.PI - ha;

        // RA = LST - HA
        let ra = (lstR - ha) / D;
        ra = ((ra % 360) + 360) % 360;

        return { ra: ra, dec: dec / D };
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
            // Use CSS pixels (clientWidth/Height) to match click coordinates.
            // canvas.width/height are device pixels (scaled by devicePixelRatio).
            const state = {
                fov: engine.core.fov * (180 / Math.PI),
                yaw: engine.core.observer.yaw * (180 / Math.PI),
                pitch: engine.core.observer.pitch * (180 / Math.PI),
                canvas_width: canvas?.clientWidth || canvas?.width || 0,
                canvas_height: canvas?.clientHeight || canvas?.height || 0,
                // Observer data for coordinate conversion
                observer_lat: engine.core.observer.latitude * (180 / Math.PI),
                observer_lon: engine.core.observer.longitude * (180 / Math.PI),
                observer_utc: engine.core.observer.utc,  // MJD
            };
            return state;
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

        // Show object info when any property changes — filter for selection
        Module.change((obj, attr) => {
            if (attr !== "selection") return;
            if (drawModeActive) return;
            const sel = engine.core.selection;
            if (!sel) return;
            const obs = engine.observer;
            let name = "";
            try { name = sel.designations(); } catch(_) {}
            let vmag;
            try { vmag = sel.getInfo("vmag", obs); } catch(_) {}
            if (!name && (vmag === undefined || vmag === null)) return;
            let info = name || "Unknown";
            if (vmag !== undefined && vmag !== null && !isNaN(vmag)) {
                info += ` | mag ${Number(vmag).toFixed(1)}`;
            }
            const overlay = ensureOverlay(el);
            if (overlay) {
                overlay.textContent = info;
                overlay.style.background = "rgba(0,100,200,0.8)";
                setTimeout(() => {
                    overlay.style.background = "rgba(0,0,0,0.6)";
                }, 3000);
            }
        });

        canvas.addEventListener("click", (evt) => {
            if (!drawModeActive) return;
            const rect = canvas.getBoundingClientRect();
            const x = Math.round(evt.clientX - rect.left);
            const y = Math.round(evt.clientY - rect.top);
            // Use the overlay's toWorld to get az/alt coords
            const coords = window.pathOverlayBridge?._toWorld(x, y);
            const cam = getCameraState();
            if (coords && cam) {
                console.log("stelBridge: draw click →", coords);
                el.dispatchEvent(
                    new CustomEvent("map_click", {
                        bubbles: true,
                        detail: {
                            ra: coords.ra, dec: coords.dec, x, y,
                            observer_lat: cam.observer_lat,
                            observer_lon: cam.observer_lon,
                            observer_utc: cam.observer_utc,
                        },
                    })
                );
            }
        });

        canvas.addEventListener("mousemove", (evt) => {
            const rect = canvas.getBoundingClientRect();
            const x = Math.round(evt.clientX - rect.left);
            const y = Math.round(evt.clientY - rect.top);
            const overlay = ensureOverlay(el);

            // Get Az/Alt from overlay's projection
            const azalt = window.pathOverlayBridge?._toWorld(x, y);
            const cam = getCameraState();

            if (azalt && cam && cam.observer_lat !== undefined) {
                const radec = azaltToRaDec(
                    azalt.ra, azalt.dec,
                    cam.observer_lat, cam.observer_lon, cam.observer_utc
                );
                // Format RA as hours (0-24h) and Dec as degrees
                const raH = radec.ra / 15;
                const raHH = Math.floor(raH);
                const raMM = Math.floor((raH - raHH) * 60);
                const raSS = ((raH - raHH) * 60 - raMM) * 60;
                const decSign = radec.dec >= 0 ? "+" : "-";
                const decAbs = Math.abs(radec.dec);
                const decDD = Math.floor(decAbs);
                const decMM = Math.floor((decAbs - decDD) * 60);
                const decSS = ((decAbs - decDD) * 60 - decMM) * 60;

                overlay.textContent =
                    `RA ${raHH}h ${raMM}m ${raSS.toFixed(0)}s | ` +
                    `Dec ${decSign}${decDD}\u00B0 ${decMM}' ${decSS.toFixed(0)}"`;
            } else {
                overlay.textContent = `Pixel: ${x}, ${y}`;
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

            // Enable constellations and labels by default
            engine.core.constellations.show_only_pointed = false;
            engine.core.constellations.lines_visible = true;
            engine.core.constellations.labels_visible = true;

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
         * Toggle constellation lines visibility.
         * @param {boolean} visible - True to show, false to hide.
         */
        setConstellationLines(visible) {
            if (!engine) return;
            engine.core.constellations.lines_visible = visible;
            engine.core.constellations.show_only_pointed = false;
        },

        /**
         * Toggle constellation labels visibility.
         * @param {boolean} visible - True to show, false to hide.
         */
        setConstellationLabels(visible) {
            if (!engine) return;
            engine.core.constellations.labels_visible = visible;
            console.log("Constellation labels:", visible);
        },

        /**
         * Toggle atmosphere visibility.
         * @param {boolean} visible - True to show, false to hide.
         */
        setAtmosphere(visible) {
            if (!engine) return;
            engine.core.atmosphere.visible = visible;
            console.log("Atmosphere:", visible);
        },

        /**
         * Toggle deep sky objects visibility and labels.
         * @param {boolean} visible - True to show, false to hide.
         */
        setDSOVisible(visible) {
            if (!engine) return;
            engine.core.dsos.visible = visible;
            engine.core.dsos.hints_visible = visible;
            console.log("DSOs:", visible);
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
