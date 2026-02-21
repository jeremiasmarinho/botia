/**
 * Action Mapper — Translates game decisions to ADB tap coordinates.
 *
 * Maps logical actions (FOLD, CALL, RAISE, ALL-IN) to Android-space
 * pixel coordinates on the PPPoker UI inside LDPlayer.
 *
 * Coordinates are calibrated per resolution. Default: 540x960 (portrait)
 * or 960x540 (landscape) depending on LDPlayer config.
 */

"use strict";

/**
 * Default button regions for PPPoker on LDPlayer (960x540 landscape).
 * Each region defines the center point and a bounding box for jitter.
 */
const DEFAULT_REGIONS_960x540 = Object.freeze({
  fold: { x: 620, y: 490, w: 80, h: 36 },
  check: { x: 750, y: 490, w: 80, h: 36 },
  call: { x: 750, y: 490, w: 80, h: 36 },
  raise: { x: 880, y: 490, w: 80, h: 36 },
  raise_confirm: { x: 880, y: 490, w: 80, h: 36 },
  allin: { x: 880, y: 490, w: 80, h: 36 },
  pot: { x: 820, y: 430, w: 60, h: 28 },

  // Raise slider endpoints (for swipe)
  slider_min: { x: 650, y: 430 },
  slider_max: { x: 910, y: 430 },
});

class ActionMapper {
  /**
   * @param {Object} [regions] - Custom region overrides
   * @param {{ width: number, height: number }} [screenSize] - Device screen size
   */
  constructor(regions = null, screenSize = { width: 960, height: 540 }) {
    this._regions = regions || { ...DEFAULT_REGIONS_960x540 };
    this._screenSize = screenSize;
  }

  /**
   * Get tap coordinates for a logical action.
   *
   * @param {'fold'|'check'|'call'|'raise'|'raise_confirm'|'allin'|'pot'} action
   * @returns {{ x: number, y: number, w?: number, h?: number }}
   */
  getCoords(action) {
    const normalized = action.toLowerCase().replace(/[-\s]/g, "_");
    const region = this._regions[normalized];
    if (!region) {
      throw new Error(`[ActionMapper] Unknown action: "${action}"`);
    }
    return { ...region };
  }

  /**
   * Update calibration for a specific action.
   * @param {string} action
   * @param {{ x: number, y: number, w?: number, h?: number }} coords
   */
  calibrate(action, coords) {
    this._regions[action.toLowerCase()] = { ...coords };
  }

  /**
   * Auto-calibrate from YOLO detections (button class IDs 52-61).
   * @param {Array<{ classId: number, cx: number, cy: number, w: number, h: number }>} detections
   */
  calibrateFromDetections(detections) {
    const CLASS_MAP = {
      52: "fold",
      53: "check",
      54: "raise",
      55: "raise_2x",
      56: "raise_2_5x",
      57: "raise_pot",
      58: "raise_confirm",
      59: "allin",
      60: "pot",
      61: "stack",
    };

    for (const det of detections) {
      const action = CLASS_MAP[det.classId];
      if (action) {
        this._regions[action] = {
          x: Math.round(det.cx),
          y: Math.round(det.cy),
          w: Math.round(det.w),
          h: Math.round(det.h),
        };
      }
    }
  }

  /** Get all current region mappings. */
  get regions() {
    return { ...this._regions };
  }
}

module.exports = { ActionMapper, DEFAULT_REGIONS_960x540 };
