using Microsoft.Win32;

namespace BodetAgent;

internal static class AgentInstallation
{
    internal static string DataDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BodetPlayer");
    private const string StartupName = "BodetPlayerAgent";

    internal static string Install(string source, string directory)
    {
        Directory.CreateDirectory(directory);
        string target = Path.Combine(directory, "BodetAgent.exe");
        if (!string.Equals(Path.GetFullPath(source), Path.GetFullPath(target), StringComparison.OrdinalIgnoreCase))
        {
            // Stage the complete single-file executable before replacing the old version.
            string temporary = target + ".new";
            File.Copy(source, temporary, true);
            File.Move(temporary, target, true);
        }
        return target;
    }

    internal static string StartupCommand(string path) => $"\"{path}\" --background";

    internal static void EnableStartup()
    {
        string executable = Install(Environment.ProcessPath
            ?? throw new IOException("Impossible de trouver l’exécutable de l’agent."), DataDirectory);
        using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
        key.SetValue(StartupName, StartupCommand(executable), RegistryValueKind.String);
    }

    internal static void DisableStartup()
    {
        using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true);
        key?.DeleteValue(StartupName, false);
    }

    internal static void SelfTest()
    {
        string directory = Path.Combine(Path.GetTempPath(), "BodetAgent-test-" + Guid.NewGuid());
        try
        {
            Directory.CreateDirectory(directory);
            string source = Path.Combine(directory, "download.exe");
            File.WriteAllBytes(source, [1, 2, 3]);
            string target = Install(source, Path.Combine(directory, "Profil avec espaces é"));
            File.Delete(source);
            if (!File.ReadAllBytes(target).SequenceEqual(new byte[] {1, 2, 3}))
                throw new Exception("Installation persistante invalide.");
            if (StartupCommand(target) != $"\"{target}\" --background")
                throw new Exception("Commande de démarrage invalide.");
            Install(target, Path.GetDirectoryName(target)!);
            File.WriteAllBytes(source, [4, 5]);
            Install(source, Path.GetDirectoryName(target)!);
            if (!File.ReadAllBytes(target).SequenceEqual(new byte[] {4, 5}))
                throw new Exception("Mise à jour invalide.");
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
    }
}
