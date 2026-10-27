const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const {
  smoothPoint, snapTarget, DwellTracker, PinchDetector, PalmPauseDetector, scrollDrag,
} = require("./handsfree-core.js");

test("smoothing applies an exponential step and initializes directly", () => {
  assert.deepEqual(smoothPoint(null, { x: 10, y: 20 }), { x: 10, y: 20 });
  assert.deepEqual(smoothPoint({ x: 0, y: 0 }, { x: 10, y: 20 }, 0.5), { x: 5, y: 10 });
});

test("snapping prefers direct targets and finds the nearest within radius", () => {
  const a = { x: 5, y: 5, element: "a" };
  const b = { x: 22, y: 10, element: "b" };
  assert.equal(snapTarget({ x: 20, y: 8 }, [a, b], 24), b);
  assert.equal(snapTarget({ x: 0, y: 0 }, [a, b], 24, "b"), b);
  assert.equal(snapTarget({ x: 60, y: 60 }, [a, b], 24), null);
});

test("dwell requires a stable target for its configured duration", () => {
  const dwell = new DwellTracker(1400);
  assert.equal(dwell.update("button", 0).clicked, false);
  assert.equal(dwell.update("button", 1399).clicked, false);
  assert.equal(dwell.update("button", 1400).clicked, true);
});

test("pinch uses close/open hysteresis and a 150ms debounce", () => {
  const pinch = new PinchDetector();
  assert.equal(pinch.update(0.04, 0).started, false);
  assert.equal(pinch.update(0.04, 149).started, false);
  assert.equal(pinch.update(0.04, 150).started, true);
  assert.equal(pinch.update(0.06, 200).released, false);
  assert.equal(pinch.update(0.09, 210).released, false);
  assert.equal(pinch.update(0.09, 360).released, true);
});

test("open palm pauses after one second and scroll drag follows vertical movement", () => {
  const palm = new PalmPauseDetector(1000);
  assert.equal(palm.update(true, 0), false);
  assert.equal(palm.update(true, 999), false);
  assert.equal(palm.update(true, 1000), true);
  assert.equal(palm.update(true, 1200), false);
  assert.equal(palm.update(false, 1300), false);
  assert.equal(scrollDrag(100, 140), -40);
});

test("browser orchestration JavaScript parses", () => {
  const source = readFileSync(path.join(__dirname, "handsfree.js"), "utf8");
  assert.doesNotThrow(() => new Function(source));
});
