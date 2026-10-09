using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace BodetAgent;

internal sealed class LocalAgentIdentity : IAsyncDisposable
{
    private readonly WebApplication application;

    internal LocalAgentIdentity(AgentSettings settings, int port = 17861)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        if (port is < 1024 or > 65535) throw new ArgumentOutOfRangeException(nameof(port));
        builder.WebHost.UseUrls($"http://127.0.0.1:{port}");
        application = builder.Build();
        string origin = AgentClient.ServerUri(settings.Server).GetLeftPart(UriPartial.Authority);
        application.MapMethods("/identity", ["GET", "OPTIONS"], async context => {
            if (context.Request.Headers.Origin.ToString() != origin)
            {
                context.Response.StatusCode = 403; return;
            }
            context.Response.Headers.AccessControlAllowOrigin = origin;
            context.Response.Headers.AccessControlAllowMethods = "GET, OPTIONS";
            context.Response.Headers["Access-Control-Allow-Private-Network"] = "true";
            context.Response.Headers.CacheControl = "no-store";
            context.Response.Headers.Vary = "Origin";
            if (context.Request.Method == "OPTIONS") { context.Response.StatusCode = 204; return; }
            string challenge = context.Request.Query["challenge"].ToString();
            if (challenge.Length != 64 || !challenge.All(Uri.IsHexDigit))
            {
                context.Response.StatusCode = 400; return;
            }
            byte[] key = SHA256.HashData(Encoding.UTF8.GetBytes(settings.Token));
            string proof = Convert.ToHexString(HMACSHA256.HashData(key, Encoding.UTF8.GetBytes(challenge))).ToLowerInvariant();
            await context.Response.WriteAsJsonAsync(new {challenge, proof});
        });
    }

    internal Task Start() => application.StartAsync();
    public async ValueTask DisposeAsync() { await application.StopAsync(); await application.DisposeAsync(); }
}
