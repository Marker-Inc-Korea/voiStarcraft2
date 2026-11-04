"use strict";
// Microphone capture: getUserMedia -> AudioContext(24 kHz) -> AudioWorklet -> pcm16 chunks -> IPC.
// The renderer holds no network access; chunks go to the main process which owns the Realtime socket.
(function () {
  let stream = null, context = null, node = null, source = null, sink = null;
  async function start(sendChunk, onLevel, deviceId) {
    if (context) return;
    const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (deviceId) audio.deviceId = { exact: deviceId };
    try { stream = await navigator.mediaDevices.getUserMedia({ audio }); }
    catch (error) { if (deviceId && error && error.name === "OverconstrainedError") stream = await navigator.mediaDevices.getUserMedia({ audio: { ...audio, deviceId: undefined } }); else throw error; }
    context = new AudioContext({ sampleRate: 24000, latencyHint: "interactive" });
    await context.audioWorklet.addModule("./mic-worklet.js");
    source = context.createMediaStreamSource(stream);
    node = new AudioWorkletNode(context, "pcm16-capture", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
    node.port.onmessage = event => {
      if (!(event.data instanceof ArrayBuffer)) return;
      sendChunk(event.data);
      if (onLevel) { // RMS of the 50 ms chunk, mapped so normal speech reaches ~0.6-1.0
        const samples = new Int16Array(event.data); let sum = 0;
        for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
        onLevel(Math.min(1, Math.sqrt(sum / samples.length) / 32768 * 9));
      }
    };
    // Worklet output is silent; connecting to destination keeps the graph pulling on all platforms.
    sink = context.createGain(); sink.gain.value = 0;
    source.connect(node); node.connect(sink); sink.connect(context.destination);
    if (context.state === "suspended") await context.resume();
    stream.getAudioTracks().forEach(track => track.addEventListener("ended", () => stop()));
  }
  function stop() {
    try { if (node) { node.port.onmessage = null; node.disconnect(); } } catch { /* ignore */ }
    try { if (source) source.disconnect(); } catch { /* ignore */ }
    try { if (sink) sink.disconnect(); } catch { /* ignore */ }
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (context) context.close().catch(() => {});
    stream = context = node = source = sink = null;
  }
  window.VoiMic = { start, stop, get running() { return Boolean(context); } };
})();
