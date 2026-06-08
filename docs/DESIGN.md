# Design — hermes-linear-ext

A zero-patch Hermes **platform plugin** that delivers Linear **Agent Sessions**.
It registers via `PluginContext.register_platform` (the same contract IRC, LINE,
Discord, Mattermost, and Google Chat use), so it needs no change to Hermes core.

## Components

| Module | Responsibility | Hermes-coupled? |
|---|---|---|
| `adapter.py` | `BasePlatformAdapter` subclass: aiohttp server (webhook + OAuth callback), dispatch into the agent, reply via GraphQL, `register(ctx)` | yes |
| `signature.py` | `Linear-Signature` HMAC-SHA256 + replay-window verification | no (pure) |
| `oauth.py` | `actor=app` authorize URL / state / token exchange params / `0600` token store | no (pure) |
| `graphql.py` | Linear GraphQL client + `agentActivityCreate` / `commentCreate` / `viewer` builders | no (pure builders) |
| `activities.py` | run-event → activity mapping + `SessionRegistry` (session→issue) | no (pure) |

Keeping the pure logic free of Hermes imports lets it be unit-tested anywhere; the
adapter is smoke-tested by loading + registering it inside a stock Hermes image.

## Request flow & the timing invariant

Linear's Agent Interaction contract is strict: a webhook must be **acked < 5 s**,
and an Agent Session that sees **no activity < 10 s** of `created` is marked
unresponsive. With no always-hot edge (no Worker), this rests on the gateway —
so the invariant is:

> On `AgentSessionEvent`, the webhook handler posts a placeholder `thought`
> activity **immediately**, returns `200` right away, and dispatches the agent run
> **off the request path** (`asyncio.create_task(handle_message(...))`). The
> deadline is met by the ack path, never by the (slow) agent.

The agent's final message is delivered by Hermes calling the adapter's `send()`,
which posts a `response` activity to the session (or a comment, in the API-key
fallback).

## Hook correlation

Per-step streaming (a Linear `action` activity per tool call) uses
`register_hook("post_tool_call", …)`. **Caveat:** Hermes' per-step hooks carry
only a `session_id`/`session_key` string — *not* the originating Linear issue. So
the adapter maintains a `SessionRegistry` keyed by the session, bound when the
webhook opens the session. This binding/lookup is implemented and the mapping is
unit-tested, but the exact key the gateway threads into the tool hooks is the one
thing best confirmed against a live run — until then the hook **no-ops safely**
(never breaks a run), and the reliable backbone is the immediate ack + the final
`response` via `send()`.

## Security

- **Auth = HMAC.** The webhook secret is the gate; Linear events are not bound to
  any messaging allowlist. **Never set `LINEAR_ALLOW_ALL_USERS`.**
- **BYO OAuth, self-hosted callback.** The client secret lives only in the
  deployment's env; the `actor=app` token is stored `0600` on the private volume.
- **Gate write tools.** Treat any write-capable tool reachable from a
  Linear-triggered session as in-scope for your governance policy before enabling.

## Roadmap

- **M1 (this release):** loads/registers on a stock image; webhook+HMAC, OAuth
  wiring, dispatch, ack invariant, reply path; pure logic unit-tested.
- **M2 (final credentials step):** live OAuth consent; confirm `agentActivityCreate`
  content schema; end-to-end `@mention → session → streamed activities → response`;
  validate hook-correlation under load.
- **Later:** packaged pip install (`hermes_agent.plugins` entry point); richer
  activity types (`elicitation`); per-team routing.
