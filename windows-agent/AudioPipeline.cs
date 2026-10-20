using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace BodetAgent;

internal sealed class MonoProvider(ISampleProvider source) : ISampleProvider
{
    public WaveFormat WaveFormat { get; } = WaveFormat.CreateIeeeFloatWaveFormat(source.WaveFormat.SampleRate, 1);
    private float[] scratch = [];
    public int Read(float[] buffer, int offset, int count)
    {
        int channels = source.WaveFormat.Channels;
        if (scratch.Length < count * channels) scratch = new float[count * channels];
        int frames = source.Read(scratch, 0, count * channels) / channels;
        for (int i = 0; i < frames; i++)
        {
            float value = 0;
            for (int c = 0; c < channels; c++) value += scratch[i * channels + c];
            buffer[offset + i] = Math.Clamp(value / channels, -1, 1);
        }
        return frames;
    }
}

internal static class AudioPipeline
{
    // Keep recent audio after a scheduling pause or capture/render clock drift.
    // The caller also locks the provider while reading it.
    public static void AddLatest(BufferedWaveProvider provider, byte[] data, int count, int backlog)
    {
        lock (provider)
        {
            if (provider.BufferedDuration.TotalMilliseconds > backlog
                || provider.BufferedBytes + count > provider.BufferLength)
                provider.ClearBuffer();
            // A single WASAPI callback can itself exceed a very small buffer.
            int keep = Math.Min(count, provider.BufferLength);
            keep -= keep % provider.WaveFormat.BlockAlign;
            provider.AddSamples(data, count - keep, keep);
        }
    }

    public static double NextDelay(ref double deadline, double elapsed, int block, int backlog)
    {
        // Rebase after a long pause; do not burst stale blocks or stop permanently.
        deadline += block;
        if (elapsed - deadline > backlog) deadline = elapsed;
        return Math.Max(0, deadline - elapsed);
    }

    public static ISampleProvider Convert(IWaveProvider source)
    {
        ISampleProvider mono = new MonoProvider(source.ToSampleProvider());
        return mono.WaveFormat.SampleRate == 48000 ? mono : new WdlResamplingSampleProvider(mono, 48000);
    }

    public static void ToPcm(float[] samples, byte[] output)
    {
        for (int i = 0; i < samples.Length; i++)
        {
            float value = float.IsFinite(samples[i]) ? Math.Clamp(samples[i], -1, 1) : 0;
            short pcm = (short)(value * (value < 0 ? 32768 : 32767));
            output[2 * i] = (byte)(pcm & 255);
            output[2 * i + 1] = (byte)((pcm >> 8) & 255);
        }
    }

    public static void SelfTest()
    {
        var bounded = new BufferedWaveProvider(new WaveFormat(48000, 16, 1)) {
            BufferDuration = TimeSpan.FromMilliseconds(100)
        };
        byte[] callback = new byte[1920];
        for (int i = 0; i < 10000; i++) AddLatest(bounded, callback, callback.Length, 100);
        if (bounded.BufferedBytes > bounded.BufferLength) throw new Exception("Buffer non borné.");
        AddLatest(bounded, new byte[19200], 19200, 100);
        if (bounded.BufferedBytes != bounded.BufferLength) throw new Exception("Callback audio trop long.");
        double deadline = 0;
        for (int i = 0; i < 100000; i++) {
            double elapsed = i * 20.002;
            double delay = NextDelay(ref deadline, elapsed, 20, 200);
            if (delay > 20.01) throw new Exception("Dérive de l’horloge audio.");
        }
        double resumed = 3000000;
        if (NextDelay(ref deadline, resumed, 20, 200) != 0 || deadline != resumed)
            throw new Exception("Reprise après suspension audio.");
        var source = new BufferedWaveProvider(WaveFormat.CreateIeeeFloatWaveFormat(44100, 2));
        var raw = new byte[44100 * 8];
        var input = Enumerable.Range(0, 44100 * 2).Select(i => i % 2 == 0 ? .75f : .25f).ToArray();
        Buffer.BlockCopy(input, 0, raw, 0, raw.Length);
        source.AddSamples(raw, 0, raw.Length);
        var mono = Convert(source);
        var samples = new float[48000];
        int count = mono.Read(samples, 0, samples.Length);
        if (count != 48000 || mono.WaveFormat.Channels != 1 || Math.Abs(samples[24000] - .5f) > .01)
            throw new Exception("Échec conversion stéréo 44,1 kHz vers mono 48 kHz.");
        var result = new byte[samples.Length * 2]; ToPcm(samples, result);
        if (result.Length != 96000 || Math.Abs(BitConverter.ToInt16(result, 48000) - 16383) > 2)
            throw new Exception("Échec conversion PCM 16 bits.");
        float[] limits = [-2, 2, float.NaN]; byte[] clipped = new byte[6]; ToPcm(limits, clipped);
        if (BitConverter.ToInt16(clipped, 0) != short.MinValue || BitConverter.ToInt16(clipped, 2) != short.MaxValue
            || BitConverter.ToInt16(clipped, 4) != 0) throw new Exception("Échec limites PCM.");
    }
}
