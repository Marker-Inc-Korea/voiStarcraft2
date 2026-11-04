// AudioWorkletProcessor: float32 128-frame blocks -> 50 ms pcm16 little-endian chunks (1200 samples @ 24 kHz).
class Pcm16Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = 1200;
    this.buffer = new Int16Array(this.size);
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      if (this.filled === this.size) {
        const out = this.buffer.buffer;
        this.port.postMessage(out, [out]);
        this.buffer = new Int16Array(this.size);
        this.filled = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm16-capture", Pcm16Capture);
