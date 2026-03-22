/**
 * @file path_overlay.js
 * SVG overlay for spline path editing on top of the Stellarium canvas.
 *
 * Creates an absolutely-positioned SVG element that sits over the map
 * container and renders cubic Bézier control paths, handles, capture
 * points, and control-point dots.  Supports multiple interaction modes
 * (draw, freehand, move, add_point, remove_point, split) and dispatches
 * CustomEvents on the container element.
 *
 * Exports: window.pathOverlayBridge = { init, setMode, update, highlightPoint }
 */

window.pathOverlayBridge = (() => {
    "use strict";

    // ------------------------------------------------------------------
    // Module state
    // ------------------------------------------------------------------

    /** @type {HTMLElement|null} */
    let container = null;

    /** @type {SVGSVGElement|null} */
    let svg = null;

    /** @type {string} Current interaction mode. */
    let mode = "pan";

    /** @type {Array<object>} Current control points with handles. */
    let controlPoints = [];

    /** @type {Array<object>} Current capture (sample) points along the path. */
    let capturePoints = [];

    /** @type {number|null} Index of highlighted capture point. */
    let highlightedIndex = null;

    // Drag state for move mode
    /** @type {boolean} */
    let dragging = false;
    /** @type {number|null} */
    let dragIndex = null;

    // Freehand state
    /** @type {boolean} */
    let freehandActive = false;
    /** @type {Array<{ra: number, dec: number}>} */
    let freehandPoints = [];

    // ------------------------------------------------------------------
    // CSS for pulsing highlight animation
    // ------------------------------------------------------------------

    const PULSE_CSS = `
@keyframes pathOverlayPulse {
    0%   { r: 8; opacity: 1; }
    50%  { r: 12; opacity: 0.5; }
    100% { r: 8; opacity: 1; }
}
.path-overlay-pulse {
    animation: pathOverlayPulse 1.2s ease-in-out infinite;
}`;

    /**
     * Inject the pulse CSS into the document head (once).
     */
    function injectStyles() {
        if (document.getElementById("path-overlay-styles")) return;
        const style = document.createElement("style");
        style.id = "path-overlay-styles";
        style.textContent = PULSE_CSS;
        document.head.appendChild(style);
    }

    // ------------------------------------------------------------------
    // SVG creation
    // ------------------------------------------------------------------

    /**
     * Create the SVG element and append it to the container.
     * @param {HTMLElement} parent
     * @returns {SVGSVGElement}
     */
    function createSVG(parent) {
        const ns = "http://www.w3.org/2000/svg";
        const el = document.createElementNS(ns, "svg");
        el.setAttribute("xmlns", ns);
        el.style.cssText =
            "position:absolute;top:0;left:0;width:100%;height:100%;" +
            "pointer-events:none;z-index:5;";
        // Ensure parent is positioned so absolute child works.
        parent.style.position = "relative";
        parent.appendChild(el);
        return el;
    }

    // ------------------------------------------------------------------
    // Coordinate helpers
    // ------------------------------------------------------------------

    /**
     * Project a world coordinate to screen pixels via stelBridge.
     * @param {number} ra
     * @param {number} dec
     * @returns {{x: number, y: number}|null}
     */
    /**
     * Stereographic projection: azalt → screen pixels.
     * Uses camera state (yaw=azimuth, pitch=altitude, fov).
     * @param {number} az  - Azimuth in degrees (same frame as camera yaw).
     * @param {number} alt - Altitude in degrees (same frame as camera pitch).
     * @returns {{x: number, y: number}|null}
     */
    let _logCounter = 0;

    /**
     * Stereographic projection matching Stellarium Web Engine exactly.
     *
     * Engine formula (proj_stereographic.c):
     *   1. Convert direction to unit vector (x, y, z)
     *   2. Project: x' = x / (0.5 * (1 - z)),  y' = y / (0.5 * (1 - z))
     *   3. Scale by perspective matrix using fovy2 = 2*atan(2*tan(fov/4))
     */
    function toScreen(az, alt) {
        const cam = window.stelBridge?.getCameraState();
        if (!cam || !cam.canvas_width) return null;

        const D = Math.PI / 180;
        const fovRad = cam.fov * D;

        // Direction vector of the point in the camera's local frame.
        // Camera looks along -Z, X is right, Y is up.
        const az0 = cam.yaw * D;
        const alt0 = cam.pitch * D;
        const azR = az * D;
        const altR = alt * D;

        // Convert both directions to 3D unit vectors
        const cx = Math.cos(altR) * Math.sin(azR);
        const cy = Math.sin(altR);
        const cz = Math.cos(altR) * Math.cos(azR);

        const rx = Math.cos(alt0) * Math.sin(az0);
        const ry = Math.sin(alt0);
        const rz = Math.cos(alt0) * Math.cos(az0);

        // Rotate point into camera frame (camera looks along ref direction)
        // Camera right = perpendicular to ref in horizontal plane
        // Camera up = perpendicular to ref and right
        const rightX = Math.cos(az0);
        const rightY = 0;
        const rightZ = -Math.sin(az0);

        const upX = -Math.sin(alt0) * Math.sin(az0);
        const upY = Math.cos(alt0);
        const upZ = -Math.sin(alt0) * Math.cos(az0);

        // Project point onto camera axes
        const dx = cx * rightX + cy * rightY + cz * rightZ;
        const dy = cx * upX + cy * upY + cz * upZ;
        const dz = cx * rx + cy * ry + cz * rz;  // dot with forward

        if (dz <= 0.01) return null;  // Behind camera

        // Stereographic projection: x' = x / (0.5 * (1 - (-dz)))
        // But we use the standard form: x' = 2 * dx / (1 + dz)
        const oneOverH = 1.0 / (0.5 * (1.0 + dz));
        const projX = dx * oneOverH;
        const projY = dy * oneOverH;

        // Scale using Stellarium's FOV formula: fovy2 = 2*atan(2*tan(fov/4))
        const fovy2 = 2 * Math.atan(2 * Math.tan(fovRad / 4));
        const scale = cam.canvas_height / (2 * Math.tan(fovy2 / 2));

        const sx = cam.canvas_width / 2 + projX * scale;
        const sy = cam.canvas_height / 2 - projY * scale;

        if (_logCounter++ < 20) {
            console.log(`toScreen(az=${az.toFixed(2)}, alt=${alt.toFixed(2)})` +
                ` cam(yaw=${cam.yaw.toFixed(2)}, pitch=${cam.pitch.toFixed(2)},` +
                ` fov=${cam.fov.toFixed(2)}, ${cam.canvas_width}x${cam.canvas_height})` +
                ` dz=${dz.toFixed(3)} → (${sx.toFixed(1)}, ${sy.toFixed(1)})`);
        }

        return { x: sx, y: sy };
    }

    /**
     * Inverse stereographic projection: screen pixels → azalt.
     * @param {number} x - Pixel x.
     * @param {number} y - Pixel y.
     * @returns {{ra: number, dec: number}|null} (named ra/dec but actually az/alt)
     */
    function toWorld(x, y) {
        const cam = window.stelBridge?.getCameraState();
        if (!cam || !cam.canvas_width) return null;

        const D = Math.PI / 180;
        const fovRad = cam.fov * D;
        const az0 = cam.yaw * D;
        const alt0 = cam.pitch * D;

        // Inverse of the FOV scaling
        const fovy2 = 2 * Math.atan(2 * Math.tan(fovRad / 4));
        const scale = cam.canvas_height / (2 * Math.tan(fovy2 / 2));

        const projX = (x - cam.canvas_width / 2) / scale;
        const projY = -(y - cam.canvas_height / 2) / scale;

        // Inverse stereographic: from projected coords to camera-space direction
        const lqq = 0.25 * (projX * projX + projY * projY);
        const ifactor = 1.0 / (lqq + 1.0);
        const dx = projX * ifactor;       // camera right
        const dy = projY * ifactor;       // camera up
        const dz = (1.0 - lqq) * ifactor; // camera forward (negated from Stellarium z)

        // Rotate from camera frame back to world frame
        const rightX = Math.cos(az0);
        const rightZ = -Math.sin(az0);
        const upX = -Math.sin(alt0) * Math.sin(az0);
        const upY = Math.cos(alt0);
        const upZ = -Math.sin(alt0) * Math.cos(az0);
        const fwdX = Math.cos(alt0) * Math.sin(az0);
        const fwdY = Math.sin(alt0);
        const fwdZ = Math.cos(alt0) * Math.cos(az0);

        const wx = dx * rightX + dy * upX + dz * fwdX;
        const wy = dy * upY + dz * fwdY;
        const wz = dx * rightZ + dy * upZ + dz * fwdZ;

        const alt = Math.asin(Math.max(-1, Math.min(1, wy)));
        const az = Math.atan2(wx, wz);

        const result = {
            ra: ((az / D) % 360 + 360) % 360,
            dec: alt / D,
        };
        console.log(`toWorld(${x}, ${y}) cam(yaw=${cam.yaw.toFixed(2)},` +
            ` pitch=${cam.pitch.toFixed(2)}) → az=${result.ra.toFixed(2)}, alt=${result.dec.toFixed(2)}`);
        return result;
    }

    // ------------------------------------------------------------------
    // SVG element helpers
    // ------------------------------------------------------------------

    const NS = "http://www.w3.org/2000/svg";

    /**
     * Create an SVG element with the given attributes.
     * @param {string} tag
     * @param {Object<string, string>} attrs
     * @returns {SVGElement}
     */
    function svgEl(tag, attrs) {
        const el = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            el.setAttribute(k, v);
        }
        return el;
    }

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------

    /**
     * Clear SVG and re-render all layers.
     * @private
     */
    function render() {
        if (!svg) return;
        // Remove all children.
        while (svg.firstChild) svg.removeChild(svg.firstChild);

        renderPath();
        renderHandles();
        renderCapturePoints();
        renderControlPointDots();

        if (highlightedIndex !== null) {
            renderHighlight(highlightedIndex);
        }
    }

    /**
     * Build a cubic Bézier `d` attribute string from the current control
     * points.  Each control point object is expected to have:
     *   { ra, dec, handleIn?: {ra, dec}, handleOut?: {ra, dec} }
     * @returns {string} SVG path `d` attribute.
     * @private
     */
    function buildPathD() {
        if (controlPoints.length < 2) return "";

        const pts = controlPoints.map((cp) => {
            const s = toScreen(cp.ra, cp.dec);
            const hIn = cp.handleIn
                ? toScreen(cp.handleIn.ra, cp.handleIn.dec) : s;
            const hOut = cp.handleOut
                ? toScreen(cp.handleOut.ra, cp.handleOut.dec) : s;
            return { s, hIn, hOut };
        });

        if (pts.some((p) => !p.s || !p.hIn || !p.hOut)) return "";

        let d = `M ${pts[0].s.x} ${pts[0].s.y}`;
        for (let i = 1; i < pts.length; i++) {
            const cp1 = pts[i - 1].hOut;
            const cp2 = pts[i].hIn;
            const end = pts[i].s;
            d += ` C ${cp1.x} ${cp1.y}, ${cp2.x} ${cp2.y}, ${end.x} ${end.y}`;
        }
        return d;
    }

    /**
     * Render the main Bézier path as a dashed orange line.
     * @private
     */
    function renderPath() {
        const d = buildPathD();
        if (!d) return;
        svg.appendChild(
            svgEl("path", {
                d,
                fill: "none",
                stroke: "rgba(237,137,54,0.6)",
                "stroke-width": "2",
                "stroke-dasharray": "6,4",
                class: "path-overlay-line",
            })
        );
    }

    /**
     * Render handle lines and dots from each control point to its handles.
     * @private
     */
    function renderHandles() {
        for (const cp of controlPoints) {
            const anchor = toScreen(cp.ra, cp.dec);
            if (!anchor) continue;

            for (const hKey of ["handleIn", "handleOut"]) {
                if (!cp[hKey]) continue;
                const hd = cp[hKey];
                const h = toScreen(hd.ra, hd.dec);
                if (!h) continue;

                // Line from control point to handle.
                svg.appendChild(
                    svgEl("line", {
                        x1: String(anchor.x),
                        y1: String(anchor.y),
                        x2: String(h.x),
                        y2: String(h.y),
                        stroke: "rgba(237,137,54,0.3)",
                        "stroke-width": "1",
                    })
                );

                // Handle dot.
                svg.appendChild(
                    svgEl("circle", {
                        cx: String(h.x),
                        cy: String(h.y),
                        r: "3",
                        fill: "rgba(237,137,54,0.5)",
                    })
                );
            }
        }
    }

    /**
     * Render blue capture-point dots along the path.
     * @private
     */
    function renderCapturePoints() {
        for (let i = 0; i < capturePoints.length; i++) {
            const cp = capturePoints[i];
            const s = toScreen(cp.ra, cp.dec);
            if (!s) continue;

            const circle = svgEl("circle", {
                cx: String(s.x),
                cy: String(s.y),
                r: "3",
                fill: "#63b3ed",
                opacity: "0.8",
                "data-capture-index": String(i),
            });
            svg.appendChild(circle);
        }
    }

    /**
     * Render orange control-point dots (draggable in move mode).
     * @private
     */
    function renderControlPointDots() {
        for (let i = 0; i < controlPoints.length; i++) {
            const cp = controlPoints[i];
            const s = toScreen(cp.ra, cp.dec);
            if (!s) continue;

            const circle = svgEl("circle", {
                cx: String(s.x),
                cy: String(s.y),
                r: "6",
                fill: "#ed8936",
                stroke: "#fff",
                "stroke-width": "1.5",
                "data-point-index": String(i),
                style: "cursor:pointer;",
            });
            svg.appendChild(circle);
        }
    }

    /**
     * Add a pulsing highlight ring around a capture point.
     * @param {number} index - Index into capturePoints.
     * @private
     */
    function renderHighlight(index) {
        if (index < 0 || index >= capturePoints.length) return;
        const cp = capturePoints[index];
        const s = toScreen(cp.ra, cp.dec);
        if (!s) return;

        const ring = svgEl("circle", {
            cx: String(s.x),
            cy: String(s.y),
            r: "8",
            fill: "none",
            stroke: "#48bb78",
            "stroke-width": "2",
            class: "path-overlay-pulse",
        });
        svg.appendChild(ring);
    }

    // ------------------------------------------------------------------
    // Interaction
    // ------------------------------------------------------------------

    /**
     * Get mouse position relative to the container.
     * @param {MouseEvent} evt
     * @returns {{x: number, y: number}}
     */
    function mousePos(evt) {
        const rect = container.getBoundingClientRect();
        return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
    }

    /**
     * Find the control-point index under a click (if any).
     * @param {EventTarget} target
     * @returns {number|null}
     */
    function pointIndexFromTarget(target) {
        if (!(target instanceof SVGElement)) return null;
        const attr = target.getAttribute("data-point-index");
        return attr !== null ? parseInt(attr, 10) : null;
    }

    /**
     * Find the nearest path segment to a screen position.
     * Returns { segmentIndex, t } where t is approximate parametric position.
     * @param {number} sx
     * @param {number} sy
     * @returns {{segmentIndex: number, t: number}|null}
     */
    function nearestSegment(sx, sy) {
        if (controlPoints.length < 2) return null;

        let bestDist = Infinity;
        let bestSeg = 0;
        let bestT = 0;
        const steps = 20;

        for (let seg = 0; seg < controlPoints.length - 1; seg++) {
            const p0 = toScreen(controlPoints[seg].ra, controlPoints[seg].dec);
            const cp0 = controlPoints[seg].handleOut
                ? toScreen(controlPoints[seg].handleOut.ra, controlPoints[seg].handleOut.dec)
                : p0;
            const cp1 = controlPoints[seg + 1].handleIn
                ? toScreen(controlPoints[seg + 1].handleIn.ra, controlPoints[seg + 1].handleIn.dec)
                : null;
            const p1 = toScreen(controlPoints[seg + 1].ra, controlPoints[seg + 1].dec);

            if (!p0 || !cp0 || !p1) continue;
            const c1 = cp1 || p1;

            for (let i = 0; i <= steps; i++) {
                const t = i / steps;
                const u = 1 - t;
                const bx = u * u * u * p0.x + 3 * u * u * t * cp0.x +
                           3 * u * t * t * c1.x + t * t * t * p1.x;
                const by = u * u * u * p0.y + 3 * u * u * t * cp0.y +
                           3 * u * t * t * c1.y + t * t * t * p1.y;
                const dx = bx - sx;
                const dy = by - sy;
                const dist = dx * dx + dy * dy;
                if (dist < bestDist) {
                    bestDist = dist;
                    bestSeg = seg;
                    bestT = t;
                }
            }
        }

        // Only match if reasonably close (within 20px).
        if (bestDist > 20 * 20) return null;
        return { segmentIndex: bestSeg, t: bestT };
    }

    /**
     * Set up all mouse interaction handlers on the SVG element.
     * @private
     */
    function setupInteraction() {
        if (!svg || !container) return;

        svg.addEventListener("click", (evt) => {
            const pos = mousePos(evt);

            if (mode === "draw") {
                const coords = toWorld(pos.x, pos.y);
                if (coords) {
                    container.dispatchEvent(
                        new CustomEvent("path_add_point", {
                            detail: { ra: coords.ra, dec: coords.dec },
                        })
                    );
                }
                return;
            }

            if (mode === "add_point") {
                const seg = nearestSegment(pos.x, pos.y);
                if (seg) {
                    const coords = toWorld(pos.x, pos.y);
                    if (coords) {
                        container.dispatchEvent(
                            new CustomEvent("path_add_point_on_segment", {
                                detail: {
                                    segmentIndex: seg.segmentIndex,
                                    ra: coords.ra,
                                    dec: coords.dec,
                                },
                            })
                        );
                    }
                }
                return;
            }

            if (mode === "remove_point") {
                const idx = pointIndexFromTarget(evt.target);
                if (idx !== null) {
                    container.dispatchEvent(
                        new CustomEvent("path_remove_point", {
                            detail: { index: idx },
                        })
                    );
                }
                return;
            }

            if (mode === "split") {
                const seg = nearestSegment(pos.x, pos.y);
                if (seg) {
                    container.dispatchEvent(
                        new CustomEvent("path_split", {
                            detail: { segmentIndex: seg.segmentIndex, t: seg.t },
                        })
                    );
                }
                return;
            }
        });

        svg.addEventListener("mousedown", (evt) => {
            const pos = mousePos(evt);

            if (mode === "move") {
                const idx = pointIndexFromTarget(evt.target);
                if (idx !== null) {
                    dragging = true;
                    dragIndex = idx;
                    evt.preventDefault();
                }
                return;
            }

            if (mode === "freehand") {
                freehandActive = true;
                freehandPoints = [];
                const coords = toWorld(pos.x, pos.y);
                if (coords) freehandPoints.push(coords);
                evt.preventDefault();
                return;
            }
        });

        svg.addEventListener("mousemove", (evt) => {
            const pos = mousePos(evt);

            if (mode === "move" && dragging && dragIndex !== null) {
                // Live visual feedback — update the control point screen
                // position directly on the SVG dot being dragged.
                const dot = svg.querySelector(
                    `circle[data-point-index="${dragIndex}"]`
                );
                if (dot) {
                    dot.setAttribute("cx", String(pos.x));
                    dot.setAttribute("cy", String(pos.y));
                }
                evt.preventDefault();
                return;
            }

            if (mode === "freehand" && freehandActive) {
                const coords = toWorld(pos.x, pos.y);
                if (coords) freehandPoints.push(coords);
                evt.preventDefault();
                return;
            }
        });

        svg.addEventListener("mouseup", (evt) => {
            const pos = mousePos(evt);

            if (mode === "move" && dragging && dragIndex !== null) {
                const coords = toWorld(pos.x, pos.y);
                if (coords) {
                    container.dispatchEvent(
                        new CustomEvent("path_point_moved", {
                            detail: {
                                index: dragIndex,
                                ra: coords.ra,
                                dec: coords.dec,
                            },
                        })
                    );
                }
                dragging = false;
                dragIndex = null;
                return;
            }

            if (mode === "freehand" && freehandActive) {
                freehandActive = false;
                if (freehandPoints.length > 0) {
                    container.dispatchEvent(
                        new CustomEvent("path_freehand_complete", {
                            detail: { points: freehandPoints },
                        })
                    );
                }
                freehandPoints = [];
                return;
            }
        });
    }

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    return {
        /**
         * Initialise the path overlay on a given container.
         * @param {string} containerId - DOM id of the starmap container.
         */
        init(containerId) {
            container = document.getElementById(containerId);
            if (!container) {
                throw new Error("PathOverlay: container not found: " + containerId);
            }
            injectStyles();
            svg = createSVG(container);
            setupInteraction();

            // Continuously re-render so points follow camera movement.
            let lastCam = "";
            window.addEventListener("resize", () => render());

            setInterval(() => {
                const cam = window.stelBridge?.getCameraState();
                if (!cam) return;
                const key = `${cam.yaw},${cam.pitch},${cam.fov},${cam.canvas_width},${cam.canvas_height}`;
                if (key !== lastCam) {
                    lastCam = key;
                    render();
                }
            }, 100);
        },

        /**
         * Set the current interaction mode.
         * @param {string} newMode - One of 'draw', 'freehand', 'move',
         *     'add_point', 'remove_point', 'split'.
         */
        setMode(newMode) {
            mode = newMode;
            dragging = false;
            dragIndex = null;
            freehandActive = false;
            freehandPoints = [];
            // Pan & Draw: SVG transparent (clicks go to canvas/engine)
            // Other modes: SVG captures clicks (move points, remove, etc.)
            if (svg) {
                const passThrough = (mode === "pan" || mode === "draw");
                svg.style.pointerEvents = passThrough ? "none" : "all";
            }
            console.log("pathOverlay: mode =", mode);
        },

        /**
         * Update the overlay with new path data and re-render.
         * @param {Array<object>} newControlPoints  - Control points with
         *     optional handleIn / handleOut sub-objects.
         * @param {Array<object>} newCapturePoints  - Sample points along
         *     the evaluated path.
         */
        update(newControlPoints, newCapturePoints) {
            controlPoints = newControlPoints || [];
            capturePoints = newCapturePoints || [];
            render();
        },

        /**
         * Highlight a specific capture point with a pulsing ring.
         * Pass null to clear the highlight.
         * @param {number|null} index - Index into capturePoints, or null.
         */
        highlightPoint(index) {
            highlightedIndex = index;
            render();
        },

        /** @private Expose toWorld for bridge.js click handling. */
        _toWorld(x, y) {
            return toWorld(x, y);
        },
    };
})();
