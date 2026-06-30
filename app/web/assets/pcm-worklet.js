// Downsamples the captured audio (context rate, typically 48 kHz, mono-mixed) to 16 kHz mono
// Int16 and posts 100 ms frames (1600 samples) to the offscreen document. A simple accumulator
// decimator — adequate for STT, which is robust to mild aliasing.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // sampleRate is the AudioWorklet global (input rate)
    this.acc = 0;
    this.out = [];
    this.frameSamples = 1600; // 100 ms @ 16 kHz
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channels = input.length;
    const frames = input[0].length;
    for (let i = 0; i < frames; i++) {
      this.acc += 1;
      if (this.acc < this.ratio) continue;
      this.acc -= this.ratio;
      let sample = 0;
      for (let c = 0; c < channels; c++) sample += input[c][i];
      sample /= channels;
      sample = Math.max(-1, Math.min(1, sample));
      this.out.push(sample < 0 ? sample * 0x8000 : sample * 0x7fff);
      if (this.out.length >= this.frameSamples) {
        const chunk = Int16Array.from(this.out.splice(0, this.frameSamples));
        this.port.postMessage(chunk, [chunk.buffer]);
      }
    }
    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
