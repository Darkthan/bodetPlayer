using System.Diagnostics;
using System.Net.Http.Json;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using NAudio.Wave;

namespace BodetAgent;

internal sealed record AgentSettings(string Server, string Token, string Name);

internal sealed class AgentClient(AgentSettings settings, Action<string> status, bool synthetic = false)
{
    private readonly SemaphoreSlim controlSend = new(1);
    private ClientWebSocket? control;
    private CancellationTokenSource? captureCancellation;
    private Task? captureTask;
    private string captureSession = "", deviceName = "", captureError = "";
    private bool capturing;

    public static Uri ServerUri(string value)
    {
        if (!Uri.TryCreate(value.Trim().TrimEnd('/') + "/", UriKind.Absolute, out var uri)
            || (uri.Scheme != "http" && uri.Scheme != "https") || !string.IsNullOrEmpty(uri.UserInfo)
            || uri.AbsolutePath != "/" || uri.Query != "" || uri.Fragment != "")
            throw new ArgumentException("Saisissez l’adresse HTTP ou HTTPS de Bodet Player, sans chemin.");
        return uri;
    }

    public static async Task<AgentSettings> Pair(string server, string code, string name)
    {
        var uri = ServerUri(server);
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        using var response = await http.PostAsJsonAsync(new Uri(uri, "api/agents/pair"), new {code, name});
        var json = await response.Content.ReadFromJsonAsync<JsonElement>();
        if (!response.IsSuccessStatusCode) throw new Exception(json.TryGetProperty("detail", out var detail) ? detail.ToString() : "Association refusée.");
        return new AgentSettings(uri.ToString(), json.GetProperty("token").GetString()!, name);
    }

    private Uri SocketUri(string path)
    {
        var builder = new UriBuilder(new Uri(ServerUri(settings.Server), path));
        builder.Scheme = builder.Scheme == "https" ? "wss" : "ws";
        return builder.Uri;
    }

    private ClientWebSocket Socket()
    {
        var socket = new ClientWebSocket();
        socket.Options.SetRequestHeader("Authorization", "Bearer " + settings.Token);
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(5);
        return socket;
    }

    private static async Task<JsonElement> Receive(ClientWebSocket socket, CancellationToken token)
    {
        byte[] buffer = new byte[4096]; int used = 0;
        while (true)
        {
            var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer, used, buffer.Length - used), token);
            if (result.MessageType == WebSocketMessageType.Close) throw new IOException("Connexion fermée par le serveur.");
            if (result.MessageType != WebSocketMessageType.Text) throw new IOException("Commande invalide.");
            used += result.Count;
            if (result.EndOfMessage) return JsonSerializer.Deserialize<JsonElement>(buffer.AsSpan(0, used));
            if (used == buffer.Length) throw new IOException("Commande trop longue.");
        }
    }

    private async Task Report(CancellationToken token, string? reportStatus = null)
    {
        if (control?.State != WebSocketState.Open) return;
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(new {status = reportStatus ?? (capturing ? "capturing" : "idle"),
            error = captureError, device = deviceName, session = captureSession, heartbeat = true});
        await controlSend.WaitAsync(token);
        try { await control.SendAsync(json, WebSocketMessageType.Text, true, token); }
        finally { controlSend.Release(); }
    }

    public async Task Run(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            using var connected = CancellationTokenSource.CreateLinkedTokenSource(token);
            Task heartbeat = Task.CompletedTask;
            try
            {
                status("Connexion au serveur…");
                control = Socket();
                using var connectTimeout = CancellationTokenSource.CreateLinkedTokenSource(token);
                connectTimeout.CancelAfter(TimeSpan.FromSeconds(15));
                await control.ConnectAsync(SocketUri("api/agents/control"), connectTimeout.Token);
                status("Connecté — en attente d’une commande depuis Bodet Player.");
                heartbeat = Task.Run(async () => {
                    try {
                        while (!connected.IsCancellationRequested) {
                            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(connected.Token);
                            timeout.CancelAfter(TimeSpan.FromSeconds(10));
                            await Report(timeout.Token);
                            await Task.Delay(3000, connected.Token);
                        }
                    } finally {
                        // A failed send must also release the receive loop so it can reconnect.
                        connected.Cancel(); control?.Abort();
                    }
                }, connected.Token);
                while (control.State == WebSocketState.Open && !token.IsCancellationRequested)
                {
                    using var receiveTimeout = CancellationTokenSource.CreateLinkedTokenSource(connected.Token);
                    receiveTimeout.CancelAfter(TimeSpan.FromSeconds(15));
                    var command = await Receive(control, receiveTimeout.Token);
                    switch (command.GetProperty("command").GetString())
                    {
                        case "start":
                            await StopCapture();
                            captureSession = command.GetProperty("session").GetString()!;
                            captureError = "";
                            captureCancellation = CancellationTokenSource.CreateLinkedTokenSource(connected.Token);
                            var capturedCommand = command;
                            captureTask = Task.Run(() => Capture(capturedCommand, captureCancellation.Token), captureCancellation.Token);
                            break;
                        case "stop":
                            if (!command.TryGetProperty("session", out var session) || session.GetString() == captureSession)
                                await StopCapture();
                            break;
                    }
                }
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { break; }
            catch (Exception) { status("Déconnecté. Nouvelle tentative dans 5 secondes. Vérifiez l’adresse, le certificat et l’association."); }
            finally
            {
                connected.Cancel();
                await StopCapture();
                control?.Abort(); control?.Dispose(); control = null;
                try { await heartbeat; } catch (Exception) { }
            }
            try { await Task.Delay(5000, token); } catch (OperationCanceledException) { break; }
        }
    }

    private async Task StopCapture()
    {
        captureCancellation?.Cancel();
        if (captureTask is not null) { try { await captureTask; } catch (Exception) { } }
        captureCancellation?.Dispose(); captureCancellation = null; captureTask = null; capturing = false;
    }

    private async Task Capture(JsonElement command, CancellationToken token)
    {
        try
        {
            int block = command.GetProperty("audio").GetProperty("block_ms").GetInt32();
            int backlog = command.GetProperty("audio").GetProperty("max_backlog_ms").GetInt32();
            if (block is < 10 or > 100 || backlog < Math.Max(40, block * 2) || backlog > 1000)
                throw new IOException("Réglages de buffer invalides.");
            using var socket = Socket();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
            timeout.CancelAfter(10000);
            await socket.ConnectAsync(SocketUri("api/live"), timeout.Token);
            await socket.SendAsync(JsonSerializer.SerializeToUtf8Bytes(new {agent_session = captureSession}), WebSocketMessageType.Text, true, timeout.Token);
            var ready = await Receive(socket, timeout.Token);
            if (!ready.TryGetProperty("ready", out _)) throw new IOException(ready.TryGetProperty("error", out var e) ? e.ToString() : "Capture refusée.");
            using var capture = synthetic ? null : new WasapiLoopbackCapture();
            deviceName = synthetic ? "Signal de test (sans capture)" : "Sortie audio Windows par défaut";
            var provider = capture is null ? null : new BufferedWaveProvider(capture.WaveFormat.AsStandardWaveFormat()) {
                BufferDuration = TimeSpan.FromMilliseconds(Math.Max(backlog, 100)), ReadFully = true
            };
            ISampleProvider mono = provider is null
                ? new NAudio.Wave.SampleProviders.SignalGenerator(48000, 1) { Frequency = 440, Gain = .2 }
                : AudioPipeline.Convert(provider);
            Exception? recordingError = null;
            if (capture is not null && provider is not null) {
                capture.RecordingStopped += (_, args) => { recordingError = args.Exception ?? new IOException("La sortie audio a été déconnectée."); };
                capture.DataAvailable += (_, args) => {
                    try {
                        if (provider.BufferedDuration.TotalMilliseconds > backlog) throw new IOException("Buffer audio dépassé. Choisissez un profil plus stable.");
                        provider.AddSamples(args.Buffer, 0, args.BytesRecorded);
                    } catch (Exception error) { recordingError = error; }
                };
                capture.StartRecording();
            }
            capturing = true;
            status("Capture active — le son du PC est diffusé vers Bodet Player.");
            await Report(token);
            float[] samples = new float[block * 48]; byte[] pcm = new byte[samples.Length * 2];
            var clock = Stopwatch.StartNew(); long blocks = 0;
            using var receiving = CancellationTokenSource.CreateLinkedTokenSource(token);
            var receiveTask = Task.Run(async () => {
                try {
                    while (!receiving.IsCancellationRequested) {
                        var report = await Receive(socket, receiving.Token);
                        if (report.TryGetProperty("error", out var error)) throw new IOException(error.ToString());
                    }
                } finally { receiving.Cancel(); }
            }, receiving.Token);
            try
            {
                while (!receiving.IsCancellationRequested)
                {
                    if (recordingError is not null) throw recordingError;
                    int read = mono.Read(samples, 0, samples.Length);
                    if (read < samples.Length) Array.Clear(samples, read, samples.Length - read);
                    AudioPipeline.ToPcm(samples, pcm);
                    using var sendTimeout = CancellationTokenSource.CreateLinkedTokenSource(receiving.Token);
                    sendTimeout.CancelAfter(backlog);
                    await socket.SendAsync(pcm, WebSocketMessageType.Binary, true, sendTimeout.Token);
                    blocks++;
                    double delay = blocks * block - clock.Elapsed.TotalMilliseconds;
                    if (delay < -backlog) throw new IOException("Réseau trop lent : capture arrêtée pour éviter l’accumulation de retard.");
                    if (delay > 0) await Task.Delay(TimeSpan.FromMilliseconds(delay), receiving.Token);
                }
                await receiveTask;
            }
            finally {
                receiving.Cancel(); capture?.StopRecording(); socket.Abort();
                try { await receiveTask; } catch (OperationCanceledException) { }
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error) { captureError = error.Message; status("Capture arrêtée : " + captureError); }
        finally
        {
            capturing = false;
            if (captureError == "") status("Connecté — capture arrêtée.");
            using var reportTimeout = new CancellationTokenSource(3000);
            try { await Report(reportTimeout.Token, captureError == "" ? "stopped" : "error"); } catch (Exception) { }
        }
    }
}
