using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace BodetAgent;

internal static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        if (args.Contains("--self-test"))
        {
            try { AudioPipeline.SelfTest(); AgentInstallation.SelfTest(); return 0; } catch (Exception) { return 1; }
        }
        if ((args.Length == 3 && args[0] == "--integration-test") || args.Contains("--bootstrap-integration-test"))
        {
            try {
                var bootstrap = args.Contains("--bootstrap-integration-test") ? AgentBootstrap.Load()
                    ?? throw new IOException("Configuration intégrée absente.") : new AgentBootstrap(args[1], args[2]);
                var settings = AgentClient.Pair(bootstrap.Server, bootstrap.Code, "Agent de test").GetAwaiter().GetResult();
                using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(60));
                var localIdentity = new LocalAgentIdentity(settings);
                localIdentity.Start().GetAwaiter().GetResult();
                new AgentClient(settings, _ => { }, synthetic:true).Run(cancellation.Token).GetAwaiter().GetResult();
                localIdentity.DisposeAsync().AsTask().GetAwaiter().GetResult();
                return 0;
            } catch (Exception) { return 1; }
        }
        // One process per Windows user, including launches from Downloads and startup.
        using var instance = new Mutex(true, @"Local\BodetPlayerAgent-" +
            System.Security.Principal.WindowsIdentity.GetCurrent().User!.Value, out bool firstInstance);
        if (!firstInstance)
        {
            if (!args.Contains("--background"))
                MessageBox.Show("L’agent est déjà actif. Ouvrez-le depuis son icône dans la zone de notification. Pour installer une mise à jour, quittez d’abord l’agent depuis cette icône.", "Bodet Player");
            return 0;
        }
        ApplicationConfiguration.Initialize();
        Application.Run(new AgentWindow());
        return 0;
    }
}

internal sealed class AgentWindow : Form
{
    private readonly TextBox server = new() { Width = 480, PlaceholderText = "https://bodet.example.fr ou http://192.168.1.50:8080" };
    private readonly TextBox code = new() { Width = 480, PlaceholderText = "Code affiché dans Bodet Player" };
    private readonly TextBox name = new() { Width = 480, Text = Environment.MachineName, MaxLength = 80 };
    private readonly Button connect = new() { Text = "Associer et connecter", AutoSize = true };
    private readonly Button disconnect = new() { Text = "Déconnecter et arrêter la capture", AutoSize = true, Enabled = false };
    private readonly Button forget = new() { Text = "Oublier l’association", AutoSize = true };
    private readonly Label state = new() { Text = "Téléchargez un code d’association depuis Bodet Player.", Width = 490, Height = 70 };
    private readonly NotifyIcon tray = new() { Icon = SystemIcons.Application, Text = "Bodet Player — agent audio", Visible = true };
    private AgentSettings? settings;
    private CancellationTokenSource? connectionCancellation;
    private Task? connectionTask;
    private LocalAgentIdentity? localIdentity;
    private bool exiting;
    private bool exitRequested;
    private static string SettingsPath => Path.Combine(AgentInstallation.DataDirectory, "agent.dat");

    public AgentWindow()
    {
        Text = "Bodet Player — Agent Windows"; ClientSize = new Size(540, 470);
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        var layout = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
            WrapContents = false, Padding = new Padding(20), AutoScroll = true };
        layout.Controls.Add(new Label { Text = "Capture de la sortie audio Windows", AutoSize = true, Font = new Font(Font, FontStyle.Bold) });
        foreach (var (label, field) in new[] {("Adresse du serveur Bodet Player", server), ("Nom du PC", name), ("Code d’association (valable 5 minutes)", code)})
        {
            layout.Controls.Add(new Label {Text = label, AutoSize = true, Margin = new Padding(0, 12, 0, 4)});
            layout.Controls.Add(field);
        }
        layout.Controls.Add(connect); layout.Controls.Add(disconnect); layout.Controls.Add(forget); layout.Controls.Add(state);
        layout.Controls.Add(new Label {Text = "Après association, l’agent démarre avec votre session Windows et se reconnecte automatiquement. Fermer cette fenêtre le laisse actif en arrière-plan. La capture commence sur commande de l’interface web.", Width = 490, Height = 65});
        Controls.Add(layout);
        tray.DoubleClick += (_, _) => { Show(); WindowState = FormWindowState.Normal; Activate(); };
        var menu = new ContextMenuStrip();
        menu.Items.Add("Ouvrir", null, (_, _) => { Show(); WindowState = FormWindowState.Normal; Activate(); });
        menu.Items.Add("Quitter et arrêter la capture", null, (_, _) => { exitRequested = true; Close(); }); tray.ContextMenuStrip = menu;
        Resize += (_, _) => { if (WindowState == FormWindowState.Minimized) Hide(); };
        connect.Click += async (_, _) => {
            connect.Enabled = false;
            try {
                if (settings is null) {
                    settings = await AgentClient.Pair(server.Text, code.Text, name.Text.Trim());
                    Save(settings); code.Clear();
                }
                AgentInstallation.EnableStartup();
                await Start(); Hide();
            } catch (Exception error) { state.Text = error.Message; connect.Enabled = true; }
        };
        disconnect.Click += async (_, _) => { await Stop(); state.Text = "Déconnecté. Aucune capture audio."; };
        forget.Click += async (_, _) => {
            await Stop();
            try { AgentInstallation.DisableStartup(); }
            catch (Exception error) { state.Text = "Impossible de désactiver le démarrage automatique : " + error.Message; return; }
            settings = null;
            if (File.Exists(SettingsPath)) File.Delete(SettingsPath);
            server.Enabled = name.Enabled = code.Enabled = true;
            connect.Text = "Associer et connecter"; state.Text = "Association oubliée sur ce PC. Révoquez aussi l’agent dans l’interface web.";
        };
        FormClosing += async (_, eventArgs) => {
            if (exiting) return;
            if (eventArgs.CloseReason != CloseReason.UserClosing)
            {
                connectionCancellation?.Cancel(); tray.Dispose(); return;
            }
            if (!exitRequested && settings is not null && eventArgs.CloseReason == CloseReason.UserClosing)
            {
                eventArgs.Cancel = true; Hide(); return;
            }
            eventArgs.Cancel = true; await Stop(); exiting = true; tray.Dispose(); Close();
        };
        Shown += async (_, _) => {
            try {
                if (File.Exists(SettingsPath)) {
                    settings = JsonSerializer.Deserialize<AgentSettings>(Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(SettingsPath), null, DataProtectionScope.CurrentUser)));
                    if (settings is not null) {
                        server.Text = settings.Server; name.Text = settings.Name;
                        try { AgentInstallation.EnableStartup(); await Start(); Hide(); }
                        catch (Exception error) { state.Text = "Démarrage automatique indisponible : " + error.Message; }
                    }
                } else if (AgentBootstrap.Load() is { } bootstrap) {
                    server.Text = bootstrap.Server; code.Text = bootstrap.Code;
                    connect.Enabled = false; state.Text = "Association automatique de ce PC…";
                    try {
                        settings = await AgentClient.Pair(bootstrap.Server, bootstrap.Code, Environment.MachineName);
                        Save(settings); code.Clear(); AgentInstallation.EnableStartup(); await Start(); Hide();
                    } catch (Exception error) {
                        connect.Enabled = true;
                        state.Text = "Association automatique impossible : " + error.Message + " Téléchargez un nouvel agent si ce fichier a déjà été utilisé sur un autre PC.";
                    }
                }
            } catch (Exception) { settings = null; state.Text = "Association illisible. Générez un nouveau code dans Bodet Player."; }
        };
    }

    private async Task Start()
    {
        if (settings is null || connectionTask is not null) return;
        localIdentity = new LocalAgentIdentity(settings);
        try { await localIdentity.Start(); }
        catch { await localIdentity.DisposeAsync(); localIdentity = null; throw; }
        server.Enabled = name.Enabled = code.Enabled = connect.Enabled = false; disconnect.Enabled = true;
        connectionCancellation = new CancellationTokenSource();
        var client = new AgentClient(settings, text => {
            if (!IsDisposed && IsHandleCreated) BeginInvoke(() => state.Text = text);
        });
        connectionTask = client.Run(connectionCancellation.Token);
    }

    private async Task Stop()
    {
        disconnect.Enabled = false; connectionCancellation?.Cancel();
        if (connectionTask is not null) { try { await connectionTask; } catch (Exception) { } }
        connectionTask = null; connectionCancellation?.Dispose(); connectionCancellation = null;
        if (localIdentity is not null) { await localIdentity.DisposeAsync(); localIdentity = null; }
        connect.Enabled = true; connect.Text = settings is null ? "Associer et connecter" : "Reconnecter";
    }

    private static void Save(AgentSettings settings)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath)!);
        var raw = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(settings));
        File.WriteAllBytes(SettingsPath, ProtectedData.Protect(raw, null, DataProtectionScope.CurrentUser));
    }
}
