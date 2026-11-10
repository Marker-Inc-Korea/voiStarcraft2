"use strict";
// Pure geometry of the tactical board window. No Electron, no timers; everything is global DIP space like displays.js.
//
// Rules (all unit-tested):
//  - ~300-340 px wide, anchored upper-left (or left edge) of the display the HUD is on, offset below the SC2 resource bar.
//  - Height is derived from the number of visible card slots and CAPPED at 40 % of the display height, so a page never grows;
//    if the cap cannot hold `pageSize` cards the effective page size shrinks (paging absorbs the rest).
//  - Stays in the left 40 % of the display (never the screen centre) and above 60 % of its height (SC2 minimap / bottom HUD).
//  - Never overlaps the HUD panel rect when one is supplied: it moves beside/below it, shrinks above it, or hides.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiBoardLayout = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const ANCHORS = Object.freeze(["upper-left", "left"]);
  const M = Object.freeze({
    cardH: 56, gap: 8, pad: 8, headerH: 20,
    minWidth: 300, maxWidth: 340, narrowMin: 220,
    tabW: 92, tabH: 26,
    maxHeightRatio: 0.4, maxRightRatio: 0.4, maxBottomRatio: 0.6, leftCentreRatio: 0.4,
    defaultOffsetX: 12, defaultOffsetY: 64, dockGap: 8, minHeight: 96,
  });
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const intersects = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

  // Height of a board showing `slots` cards (header row holds the page label, only drawn when paging).
  function boardHeight(slots, paged) {
    const n = Math.max(0, Math.floor(slots));
    return M.pad * 2 + (paged ? M.headerH + M.gap : 0) + n * M.cardH + Math.max(0, n - 1) * M.gap;
  }
  const heightCap = display => Math.floor(display.bounds.height * M.maxHeightRatio);
  // How many card slots fit under the 40 % cap (with a header, the worst case). At least 1.
  function fitSlots(display) {
    const room = heightCap(display) - M.pad * 2 - M.headerH - M.gap;
    return Math.max(1, Math.floor((room + M.gap) / (M.cardH + M.gap)));
  }
  function boardWidth(display, width, offsetX) {
    const want = clamp(Math.round(Number(width) || M.minWidth), M.minWidth, M.maxWidth);
    const room = Math.floor(display.bounds.width * M.maxRightRatio) - offsetX;
    return Math.max(M.narrowMin, Math.min(want, room));
  }

  // input: { display:{bounds,workArea?}, anchor, pageSize, cardCount (cards on the page being shown), pageCount, width,
  //          offsetX, offsetY, collapsed, dockRect?:{x,y,width,height} (screen rect of the visible HUD panel) }
  // -> { visible, rect, pageSize (effective), slots, cap, reason }
  function computeBoardLayout(input) {
    const display = input.display;
    const b = display.bounds;
    const wa = display.workArea || b;
    const anchor = ANCHORS.includes(input.anchor) ? input.anchor : "upper-left";
    const offsetX = clamp(Math.round(input.offsetX ?? M.defaultOffsetX), 0, 400);
    const offsetY = clamp(Math.round(input.offsetY ?? M.defaultOffsetY), 0, 600);
    const cap = heightCap(display);
    const pageSize = Math.max(1, Math.min(Math.round(input.pageSize || 3), fitSlots(display)));
    const dock = input.dockRect && input.dockRect.width > 0 && input.dockRect.height > 0 ? input.dockRect : null;
    const bottomLimit = b.y + Math.floor(b.height * M.maxBottomRatio);
    const topLimit = Math.max(wa.y, b.y);
    const rightLimit = b.x + Math.floor(b.width * M.maxRightRatio);
    const collapsed = Boolean(input.collapsed);
    const paged = Number(input.pageCount) > 1;
    const slots = collapsed ? 0 : Math.max(1, Math.min(pageSize, Math.floor(input.cardCount ?? pageSize)));
    const width = collapsed ? M.tabW : boardWidth(display, input.width, offsetX);
    let height = collapsed ? M.tabH : Math.min(cap, boardHeight(paged ? pageSize : slots, paged));
    // A paged board keeps one page height; a single page is as tall as its cards (never taller than the cap).
    const x0 = b.x + offsetX;
    const yFor = h => {
      const y = anchor === "left"
        ? b.y + Math.round(b.height * M.leftCentreRatio - h / 2) + (offsetY - M.defaultOffsetY)
        : Math.max(topLimit, b.y + offsetY);
      return clamp(y, topLimit, Math.max(topLimit, bottomLimit - h));
    };
    const centreLimit = b.x + Math.floor(b.width / 2); // hard guard: never reach the screen centre line
    const fits = rect => rect.x >= b.x && rect.x + rect.width <= centreLimit && rect.y >= topLimit && rect.y + rect.height <= bottomLimit && (!dock || !intersects(rect, dock));

    const preferred = { x: x0, y: yFor(height), width, height };
    const candidates = [preferred];
    if (dock) {
      candidates.push({ ...preferred, x: dock.x + dock.width + M.dockGap });                  // beside the HUD
      candidates.push({ ...preferred, y: dock.y + dock.height + M.dockGap });                 // below the HUD
      const room = dock.y - M.dockGap - preferred.y;                                           // above the HUD: shrink to fit
      if (!collapsed && room >= M.minHeight) candidates.push({ ...preferred, height: Math.min(height, room) });
    }
    for (const rect of candidates) {
      if (fits(rect)) {
        // A shrunk candidate may hold fewer slots than the page: report what truly fits.
        const room = rect.height - M.pad * 2 - (paged ? M.headerH + M.gap : 0);
        const fitted = collapsed ? 0 : Math.max(1, Math.min(pageSize, Math.floor((room + M.gap) / (M.cardH + M.gap))));
        return { visible: true, rect, pageSize: fitted || 1, slots: collapsed ? 0 : Math.min(slots, fitted || 1), cap, reason: rect === preferred ? "" : "avoid-hud" };
      }
    }
    return { visible: false, rect: null, pageSize, slots: 0, cap, reason: "no-room" };
  }

  return { ANCHORS, METRICS: M, boardHeight, fitSlots, heightCap, boardWidth, computeBoardLayout, intersects };
});
