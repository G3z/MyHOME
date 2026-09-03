# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |

# Project: MyHOME

A Home Assistant custom integration (HACS "integration" category) that bridges BTicino/Legrand **MyHOME / OpenWebNet** gateways (e.g. F454, F455, MH200N, MyHomeServer1) to Home Assistant. It wraps the [`OWNd`](https://pypi.org/project/OWNd/) Python library, which implements the OpenWebNet protocol.

Project status: the original maintainer (see README.md) has stepped back and is looking for a new code owner — there is no active roadmap beyond keeping the integration working.

## Repository layout

- `custom_components/myhome/` — the entire integration; this is the only code in the repo.
- `hacs.json`, `.github/workflows/{validate,hassfest}.yml` — HACS/hassfest CI validation (see below).
- No app code, tests, or build tooling exist outside `custom_components/myhome/`.

## Commands

There is no local test suite, linter config, or build step in this repo — validation happens entirely through GitHub Actions on push/PR:

- **HACS validation** (`.github/workflows/validate.yml`): runs `hacs/action` against the `integration` category (checks `hacs.json`, repo structure, manifest requirements).
- **hassfest validation** (`.github/workflows/hassfest.yml`): runs Home Assistant's own `hassfest` action, which validates `manifest.json`, `services.yaml`, translation files, and other core-integration conventions.

To exercise this integration locally you need a working Home Assistant dev environment with this repo symlinked/copied into `<config>/custom_components/myhome`, plus network access to a real (or emulated) BTicino/Legrand gateway — there is no mock gateway or fixture data in the repo.

## Architecture

### Two-tier configuration

Configuration is deliberately split across two layers:

1. **Config entry (UI-driven, `config_flow.py`)** — holds only gateway connection info (host, port, password, MAC, SSDP metadata). `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` in `__init__.py` explicitly rejects `configuration.yaml` setup; a gateway can only be added via the UI (with SSDP auto-discovery for known models) or reauth flow.
2. **Device/entity definitions (external YAML file, default `/config/myhome.yaml`)** — loaded and validated in `async_setup_entry` (`__init__.py`) via `validate.config_schema` (`validate.py`, built on `voluptuous`). This file defines every light, switch, cover, climate zone, sensor, etc., keyed by gateway MAC, with WHO/WHERE OpenWebNet addressing (`CONF_WHO`/`CONF_WHERE`) plus per-entity metadata (icons, device class, dimmable, advanced shutter, heating/cooling/fan support, CEN/CEN+ pushbutton mappings, etc.). `validate.py` defines the address schema classes (`General`, `Area`, `Group`, `PointToPoint`, `SpecialWhere`, `BusInterface`, `MACAddress`) that encode OpenWebNet WHERE addressing rules.

Because entity/device config lives outside the config entry, **the set of platforms forwarded to Home Assistant is computed dynamically** from the keys present in the validated YAML (`hass.data[DOMAIN][mac][CONF_PLATFORMS].keys()`), not from the static `PLATFORMS` list at the top of `__init__.py` (that list is vestigial/unused).

### Runtime data shape

Everything lives under `hass.data[DOMAIN][mac]`, where `mac` is the gateway's MAC address (multiple gateways/config entries can coexist):

```
hass.data[DOMAIN][mac][CONF_ENTITY]              -> the MyHOMEGatewayHandler instance for this gateway
hass.data[DOMAIN][mac][CONF_PLATFORMS][platform][device_id][CONF_ENTITIES][entity_key] -> live entity instances
```

Entities register/unregister themselves into this structure in `MyHOMEEntity.async_added_to_hass` / `async_will_remove_from_hass` (`myhome_device.py`), which all platform entity classes inherit from. `_attr_unique_id` is always `f"{gateway.mac}-{device_id}"`.

### Gateway handler (`gateway.py`)

`MyHOMEGatewayHandler` owns two long-running asyncio tasks per gateway, started in `async_setup_entry`:

- **`listening_loop`** — a single worker holding an `OWNEventSession`; it receives all OpenWebNet event/status messages from the gateway, pattern-matches the message type from `OWNd.message` (e.g. `OWNLightingEvent`, `OWNAutomationEvent`, `OWNEnergyEvent`, `OWNHeatingEvent`, `OWNCENEvent`/`OWNCENPlusEvent`), and dispatches to the matching entity's `handle_event()` by looking it up in `hass.data[DOMAIN][mac][CONF_PLATFORMS][...]` via the message's WHO/WHERE. General/area/group light and automation events are additionally fired as HA bus events (`myhome_general_light_event`, `myhome_area_automation_event`, etc.), and CEN/CEN+ scenario pushbutton presses fire `myhome_cen_event`/`myhome_cenplus_event`. If `generate_events` is enabled (an options-flow toggle), every raw message is also fired as `myhome_message_event`.
- **`sending_loop`** — one or more workers (configurable via `CONF_WORKER_COUNT`) draining an `asyncio.Queue` (`send_buffer`) and writing commands out through an `OWNCommandSession`. All outgoing commands (from entity actions or the `send_message`/`sync_time` services in `__init__.py`) go through `MyHOMEGatewayHandler.send()` / `send_status_request()`, never directly through a session.

### Platform files

Each platform module (`light.py`, `switch.py`, `cover.py`, `climate.py`, `binary_sensor.py`, `sensor.py`, `button.py`) follows the same shape: `async_setup_entry` reads its slice of `hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]` (populated from the YAML config), instantiates one entity subclass per configured device, and calls `async_add_entities`. Entity classes subclass both `MyHOMEEntity` (`myhome_device.py`) and the relevant HA platform entity class, translating between HA state/service calls and `OWNd.message` command/event objects for their WHO. `button.py`'s `DisableCommandButtonEntity`/`EnableCommandButtonEntity` are special-cased in `gateway.py`'s event dispatch (excluded from generic `handle_event` fan-out).

### Services and translations

- `services.yaml` + `custom_components/myhome/translations/*.json` define the two services registered in `__init__.py` (`sync_time`, `send_message`) plus a `start_sending_instant_power` service (implemented in `sensor.py`). Translation files (`en`, `fr`, `it`, `nl`) must stay in sync with `hacs.json`'s `country` list and with `config_flow.py`/`services.yaml` strings — hassfest CI checks this.
