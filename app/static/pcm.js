class PCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Int16Array(960);
    this.offset = 0;
  }
  process(inputs, outputs) {
    const channels = inputs[0];
    if (channels && channels.length) {
      for (let i = 0; i < channels[0].length; i++) {
        let value = 0;
        for (const channel of channels) value += channel[i];
        value = Math.max(-1, Math.min(1, value / channels.length));
        this.block[this.offset++] = value < 0 ? value * 32768 : value * 32767;
        if (this.offset === this.block.length) {
          this.port.postMessage(this.block.buffer, [this.block.buffer]);
          this.block = new Int16Array(960);
          this.offset = 0;
        }
      }
    }
    // Silent output keeps the processor scheduled without microphone feedback.
    for (const channel of outputs[0]) channel.fill(0);
    return true;
  }
}
registerProcessor('pcm', PCM);
