const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../sketch/capture.js");

const DISPLAY = { id: 7, scaleFactor: 2, bounds: { x: 0, y: 0, width: 1000, height: 600 } };
const RECT = { x: 10, y: 350, w: 240, h: 240 };
function fakeImage(w, h, fill = 128) {
  const calls = [];
  const img = {
    getSize: () => ({ width: w, height: h }), isEmpty: () => w === 0,
    crop: r => { calls.push(["crop", r]); return fakeImage(r.width, r.height, fill); },
    resize: o => { calls.push(["resize", o]); return fakeImage(o.width, o.height, fill); },
    toBitmap: () => Buffer.alloc(4 * w * h, fill), toPNG: () => Buffer.from("PNG!" + w + "x" + h), calls,
  };
  return img;
}
const deps = (over = {}) => ({
  getAccess: () => "granted",
  getSources: async () => [{ display_id: "3", thumbnail: fakeImage(10, 10) }, { display_id: "7", thumbnail: fakeImage(2000, 1200) }],
  ...over,
});

test("crops the minimap rect out of the matching display's capture, caps the long side at 512 and returns base64 PNG", async () => {
  let calls;
  const d = deps({ getSources: async opts => { calls = opts; return [{ display_id: "7", thumbnail: fakeImage(2000, 1200) }]; } });
  const out = await C.captureMinimap(d, DISPLAY, RECT);
  assert.deepEqual(calls.thumbnailSize, { width: 2000, height: 1200 }); assert.deepEqual(calls.types, ["screen"]);
  assert.equal(out.reason, ""); assert.equal(Buffer.from(out.imagePngB64, "base64").toString(), "PNG!480x480");
});
test("a larger crop is resized so the long side is at most 512", async () => {
  const d = deps({ getSources: async () => [{ display_id: "7", thumbnail: fakeImage(2000, 1200) }] });
  const out = await C.captureMinimap(d, DISPLAY, { x: 0, y: 0, w: 400, h: 300 });
  assert.equal(Buffer.from(out.imagePngB64, "base64").toString(), "PNG!512x384");
});
test("scale is measured from the actual thumbnail (a 1x thumbnail of a 2x display still crops the right region)", async () => {
  const img = fakeImage(1000, 600);
  const d = deps({ getSources: async () => [{ display_id: "7", thumbnail: img }] });
  await C.captureMinimap(d, DISPLAY, RECT);
  assert.deepEqual(img.calls[0], ["crop", { x: 10, y: 350, width: 240, height: 240 }]);
});
test("no Screen Recording permission -> no capture attempt, image null with a reason", async () => {
  for (const status of ["denied", "restricted", "not-determined"]) {
    let asked = false;
    const out = await C.captureMinimap(deps({ getAccess: () => status, getSources: async () => { asked = true; return []; } }), DISPLAY, RECT);
    assert.equal(out.imagePngB64, null); assert.equal(out.reason, "permission"); assert.equal(asked, false);
  }
});
test("a black capture is rejected as no image (locked screen / revoked permission), never sent", async () => {
  const out = await C.captureMinimap(deps({ getSources: async () => [{ display_id: "7", thumbnail: fakeImage(2000, 1200, 0) }] }), DISPLAY, RECT);
  assert.equal(out.imagePngB64, null); assert.equal(out.reason, "dark");
});
test("missing display source, empty image and thrown errors all degrade to image null with a reason", async () => {
  assert.equal((await C.captureMinimap(deps({ getSources: async () => [] }), DISPLAY, RECT)).reason, "no-source");
  assert.equal((await C.captureMinimap(deps({ getSources: async () => [{ display_id: "7", thumbnail: fakeImage(0, 0) }] }), DISPLAY, RECT)).reason, "empty");
  assert.equal((await C.captureMinimap(deps({ getSources: async () => { throw new Error("boom"); } }), DISPLAY, RECT)).reason, "error");
});
test("a capture that takes too long is abandoned (the route request must not wait on it)", async () => {
  const out = await C.captureMinimap(deps({ getSources: () => new Promise(() => {}), timeoutMs: 30 }), DISPLAY, RECT);
  assert.equal(out.imagePngB64, null); assert.equal(out.reason, "timeout");
});
test("rect outside the capture is clamped; a rect fully outside is an error", async () => {
  const d = deps({ getSources: async () => [{ display_id: "7", thumbnail: fakeImage(2000, 1200) }] });
  const clamped = await C.captureMinimap(d, DISPLAY, { x: 900, y: 500, w: 240, h: 240 });
  assert.equal(Buffer.from(clamped.imagePngB64, "base64").toString(), "PNG!200x200");
  assert.equal((await C.captureMinimap(d, DISPLAY, { x: 2000, y: 2000, w: 10, h: 10 })).reason, "empty");
});
