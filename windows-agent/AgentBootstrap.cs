using System.Text;
using System.Text.Json;

namespace BodetAgent;

internal sealed record AgentBootstrap(string Server, string Code)
{
    internal static AgentBootstrap? Load()
    {
        using var file = File.OpenRead(Environment.ProcessPath!);
        if (file.Length < 20) return null;
        file.Seek(-20, SeekOrigin.End);
        using var reader = new BinaryReader(file, Encoding.UTF8, true);
        int length = reader.ReadInt32();
        if (Encoding.ASCII.GetString(reader.ReadBytes(16)) != "BODET_CONFIG_V1!") return null;
        if (length is < 1 or > 4096 || length > file.Length - 20)
            throw new IOException("Configuration intégrée invalide.");
        file.Seek(-20 - length, SeekOrigin.End);
        return JsonSerializer.Deserialize<AgentBootstrap>(reader.ReadBytes(length));
    }
}
