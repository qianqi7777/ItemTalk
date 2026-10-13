/** 将浏览器录音重采样为单声道 16kHz WAV，避免不同手机容器格式影响 ASR。 */
export function encodeWav(
  samples: Float32Array,
  sampleRate = 16000,
): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index++)
      view.setUint8(offset + index, value.charCodeAt(index));
  };
  write(0, 'RIFF');
  view.setUint32(4, buffer.byteLength - 8, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, index) => {
    const sample = Math.max(-1, Math.min(1, value));
    view.setInt16(
      44 + index * 2,
      sample < 0 ? sample * 32768 : sample * 32767,
      true,
    );
  });
  return buffer;
}

export async function recordingToWav(blob: Blob): Promise<File> {
  const decoder = new AudioContext();
  try {
    const audio = await decoder.decodeAudioData(await blob.arrayBuffer());
    if (audio.duration > 61) throw new Error('录音最长 60 秒');
    const context = new OfflineAudioContext(
      1,
      Math.ceil(audio.duration * 16000),
      16000,
    );
    const source = context.createBufferSource();
    source.buffer = audio;
    source.connect(context.destination);
    source.start();
    const resampled = await context.startRendering();
    return new File([encodeWav(resampled.getChannelData(0))], 'recording.wav', {
      type: 'audio/wav',
    });
  } finally {
    await decoder.close();
  }
}
