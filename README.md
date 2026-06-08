# hermes-linear-ext

**Linear Agent Sessions for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — a zero-patch plugin.**

Make your Hermes agent a first-class **Linear Agent**: `@mention`-able and
assignable, opening **Agent Sessions** on issues and streaming its work back as
Linear *agent activities* (thought → action → response). It installs as a
standard Hermes **platform plugin** — **no core patch, no fork** — and uses a
**bring-your-own Linear OAuth app** with the OAuth callback self-hosted on your
deployment's own public URL. **No Cloudflare Worker required.**

```
Linear ──webhook(HMAC)──▶  your funnel ─▶ 127.0.0.1:<port>  (this plugin, in Hermes)
   ▲                                            │  verify · ack <5s · first thought <10s
   │                                            ▼
   └────── agent activities ◀── Linear GraphQL  ── Hermes agent run (tools, reasoning)
```

## Status — v0.1.0

| Validated (no live creds) | Pending (the final credentials step) |
|---|---|
| Loads + registers the `linear` platform on a **stock** `nousresearch/hermes-agent` image (no patch); `Platform("linear")` resolves with no enum edit | Real OAuth `actor=app` consent against a live workspace |
| Adapter conforms to `BasePlatformAdapter` (connect/disconnect/send/get_chat_info) | Real webhook delivery + `agentActivityCreate` acceptance (exact activity content schema) |
| HMAC signature + replay verification; OAuth URL/state/token-store; activity mapping — **unit-tested** | End-to-end `@mention → session → streamed activities → response` |
| | Per-step activity streaming correlation under load (see [`docs/DESIGN.md`](docs/DESIGN.md#hook-correlation)) |

## Requirements

- A running Hermes Agent deployment you control (this is a plugin for it).
- A **public HTTPS URL** that reaches the deployment — e.g. a **Tailscale Funnel**
  port. Linear's webhook + the OAuth redirect both point at it.
- A Linear workspace where you can create an **OAuth application** and a webhook.

## Install (≈ copy a folder + enable)

1. **Drop the plugin onto the deployment's data volume:**
   ```sh
   cp -r linear "$HERMES_HOME/plugins/linear"      # $HERMES_HOME is /opt/data in the Docker image
   ```
2. **Enable it** (user plugins are opt-in):
   ```sh
   hermes plugins enable linear-platform
   ```
   …or add `linear-platform` to `plugins.enabled` in `config.yaml`.
3. **Set the env** (below) and restart the gateway. The platform stays dormant
   until `LINEAR_WEBHOOK_SECRET` + an identity (OAuth or API key) are present.

> Coflounder's own deployment installs this via `config-init`/bootstrap into the
> `hermes-data` volume (pinned + SHA-verified, like the CLI toolbox) — same idea,
> automated.

## Configure (bring your own Linear OAuth app)

1. **Linear → Settings → API → OAuth applications → New.** Set the redirect URI to
   `https://<your-public-host>/oauth/linear/callback`. Enable it as an **Agent**
   (so it's mentionable/assignable). Copy the **client id** + **client secret**.
2. **Linear → Settings → API → Webhooks → New.** URL
   `https://<your-public-host>/hooks/linear`; subscribe to **Agent session events**
   (and Comments/Issues for the fallback). Copy the **signing secret**.
3. **Set env on the Hermes service:**

   | Var | Required | Purpose |
   |---|---|---|
   | `LINEAR_WEBHOOK_SECRET` | ✅ | HMAC signing secret from the webhook |
   | `LINEAR_CLIENT_ID` / `LINEAR_CLIENT_SECRET` | ✅ (Agent Sessions) | Your OAuth app — mints the `actor=app` token |
   | `LINEAR_PUBLIC_URL` | ✅ | Public HTTPS base (your funnel host); webhook + redirect derive from it |
   | `LINEAR_API_KEY` | alt | Use **instead** of OAuth for the bot fallback (plain comments, no Agent Sessions) |
   | `LINEAR_PORT` | opt | Local HTTP port (default `8650`) |
   | `LINEAR_TEAM_IDS` | opt | Comma-separated team keys to filter |

4. **Install the agent app:** visit `https://<your-public-host>/oauth/linear/authorize`
   once and approve — the `actor=app` token is stored `0600` on the volume.
5. `@mention` your agent on a Linear issue. ✅

**Security:** the webhook is authenticated by HMAC; Linear events are not bound to
the Telegram allowlist, so **never set `LINEAR_ALLOW_ALL_USERS`** and gate
write-capable tools behind your governance policy first. See
[`docs/DESIGN.md`](docs/DESIGN.md#security).

## Why no Cloudflare Worker?

Earlier designs bridged Linear through a Worker because the stock image couldn't
verify Linear's `Linear-Signature` header. This plugin verifies it natively and
self-hosts the OAuth callback on your existing funnel — so with a bring-your-own
OAuth app there is nothing left for a Worker to do. (A hosted/shared-app model
would reintroduce one; that's deliberately out of scope.)

## Develop

```sh
pip install pytest
pytest -q                       # pure-logic tests (no Hermes needed)
bash scripts/validate_load.sh   # loads+registers in a stock Hermes image (needs Docker + the image)
```

## License

MIT © Coflounder, LLC. See [LICENSE](LICENSE).
