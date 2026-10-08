const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let Processor;
const context = {
  AudioWorkletProcessor:class {constructor() {this.sent = []; this.port = {postMessage:buffer => this.sent.push(buffer)};}},
  registerProcessor:(_, processor) => {Processor = processor;},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'pcm.js'), 'utf8'), context);
for (const blockFrames of [480, 960, 4800]) {
  const processor = new Processor({processorOptions:{blockFrames}});
  const left = new Float32Array(128).fill(0.5);
  const right = new Float32Array(128).fill(-0.25);
  const output = new Float32Array(128).fill(1);
  for (let index = 0; index < 150; index++) processor.process([[left, right]], [[output]]);
  assert.equal(processor.sent.length, 19200 / blockFrames);
  for (const buffer of processor.sent) {
    assert.equal(buffer.byteLength, blockFrames * 2);
    assert.equal(new Int16Array(buffer).every(sample => sample === 4095), true);
  }
  assert.equal(output.every(sample => sample === 0), true);
}
assert.throws(() => new Processor({processorOptions:{blockFrames:1}}), /invalide/);
console.log('PCM passed: configurable 10/20/100 ms blocks, continuous samples, mono mix and silent output.');
