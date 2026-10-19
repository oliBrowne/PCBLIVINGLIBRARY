# Bring your own model

Claude by default, any provider or a local model, and the guide rails that keep smaller models honest.

## Bring your own model (`ecadlib/llm/`): Claude by default, any provider, guide rails that scale

ecadlib is open source (MIT) and has no paid tier, account or hosted service: nothing is sent anywhere except to the
model endpoint you configure, with the key you export, and there is no telemetry. Every AI feature (datasheet reader,
`ds_route` ladder, visual proof, `eval`) goes through one small provider layer:

* **anthropic** (the default; nothing to set): Claude through the Messages API exactly as before: PDF document blocks,
  structured outputs, Message Batches, prompt caching, the refusal fallback. With no `[ai]` table the requests are
  byte-for-byte the ones ecadlib sent before this layer existed (a test compares them with hashes taken from the old code).
* **openai-compatible**: any `/v1/chat/completions` server: OpenAI, OpenRouter, Google's OpenAI-compatible Gemini
  endpoint, and local servers (Ollama, LM Studio, vLLM, llama.cpp). Presets fill in the usual `base_url` and key
  variable. A local server needs no key and costs $0 in the cost ledger. Common OpenAI, Gemini and DeepSeek models
  have built-in list prices; other unknown prices show tokens only (OpenRouter's `/models` prices are picked up when
  `detect` is on).

What it costs: [docs/costs.md](costs.md) has an estimate per job (datasheet read, 50-line BOM, 500-part library,
visual check) for every model above, and `ecadlib costs` prints the same for the model in your `ecadlib.toml`.

```toml
# ecadlib.toml: pick ONE [ai] block (store the key with `ecadlib keys set NAME`; only the NAME goes here)

[ai]                                   # Claude (the default: this block is the same as having none)
provider = "anthropic"                 # ecadlib keys set ANTHROPIC_API_KEY
model = "claude-opus-5-5"

[ai]                                   # OpenAI
provider = "openai"                    # ecadlib keys set OPENAI_API_KEY
model = "gpt-5"

[ai]                                   # OpenRouter (any model it serves)
provider = "openrouter"                # ecadlib keys set OPENROUTER_API_KEY
model = "anthropic/claude-opus-5-5"    # or "openai/gpt-5", "qwen/qwen-2.5-72b-instruct", ...

[ai]                                   # Ollama on this machine: free, no key, nothing leaves the machine
provider = "ollama"                    # ollama pull qwen2.5vl:7b   (base_url defaults to http://localhost:11434/v1)
model = "qwen2.5vl:7b"                 # OLLAMA_CONTEXT_LENGTH=16384 ollama serve: Ollama's default context is small

[ai]                                   # any other OpenAI-compatible server (LM Studio, vLLM, llama.cpp, a gateway)
provider = "openai-compatible"
base_url = "http://localhost:8000/v1"  # plain http:// only for this machine or your own network
api_key_env = ""                       # NAME of the variable with the key, if the server wants one
model = "Qwen/Qwen2.5-72B-Instruct"

[ai.profiles."Qwen/Qwen2.5-72B-Instruct"]   # optional: what ecadlib should assume about a model
tier = "mid"                           # frontier | mid | small
context = 32768
```

A cheap local model first and Claude when the checks fail (a mixed ladder; `name:model` names an endpoint):

```toml
[ai.endpoints.local]
provider = "ollama"
[datasheet]
route = "ladder"
ladder = ["local:qwen2.5vl:7b", "claude-sonnet-5-5", "claude-opus-5-5"]
```

`ecadlib ai show` prints the model, endpoint, whether its key variable is set (never the value), the capability
profile and the rails a read runs; `--detect` asks the server about the model; `ecadlib ai rails` lists every rail;
`ecadlib doctor` has an `ai model` line. `[datasheet]` and `[agent.proof]` may set their own `provider` /
`base_url` / `model` / `api_key_env` / `assist` (empty = `[ai]`).

**Capability profiles** (`ecadlib/llm/profiles.py`): per model, PDF input, image input, JSON-schema output, context
window and a tier (frontier / mid / small). From a built-in table (Claude, GPT-5 / 4.1 / 4o, o3/o4, Gemini, DeepSeek;
open-weight families by size: 7B is small, 70B is mid), then what the endpoint reports (`/models` on OpenRouter, vLLM,
LM Studio; `/api/show` on Ollama), then `[ai.profiles."<model>"]` in ecadlib.toml, which always wins. An unknown model
gets the conservative profile (small, text only, no schema, 4K context).

**Guide rails** (`ecadlib/llm/rails.py`) come in layers, so Claude keeps all the help that checks and never the help
that narrows:

| layer | rails | who gets them |
|---|---|---|
| support | page picking, datasheet's checks as the gate, **one feedback retry with the checks' exact errors**, escalation along the ladder, units and geometry computed in code, provenance | every model, Claude included |
| capability | PDF as page text (+ page pictures for vision models), schema in the prompt | only a model that lacks PDF input / JSON-schema output |
| guidance | JSON repair (fences, commas, numbers as text, unit and pin-type synonyms), worked examples, hold on doubt, standard parts first; for small models also page cap and **split steps** (which package -> dimension table -> pin table, three tiny schemas, each checked and retried with its exact error; BGA / connector pad tables are not attempted) | `assist = "auto"`: mid and small tiers; `"off"`: none; `"full"`: all |

Frontier models get today's single structured call with the PDF and nothing extra. The feedback retry is the one
support rail Claude gains: when an answer fails datasheet's checks *where the route would otherwise stop* (top of the
ladder, every page sent), the same request goes once more with the answer and the checks' exact errors appended
(append-only, so the cached PDF prefix is reused), within `[datasheet] max_attempts`; the better answer is kept, and
the prompt says never to change a printed value to pass a check. `[ai] feedback_retry = false` turns it off. A guided
model's answer that still fails the checks at the end of its route is not written: the part is held with the reason
(`hold_on_doubt`), and the visual proof never takes a small model's "pass" as proof. What ran is in every part's
`<part>.extraction.json` under `"ai"` (tier, profile and its source, rails, steps with their repairs and errors,
feedback before/after, which support rails ran).

Compare models on the same test set: `ecadlib eval run --provider ollama --model qwen2.5vl:7b --record rec/qwen -o out/eval/qwen`,
then `ecadlib eval compare out/eval/claude out/eval/qwen` (`--replay` re-scores offline; `--assist off|full` to see what the
rails are worth).

```
python3 -m tests.test_llm
```

**Not tested live:** no model of any provider is reachable from this sandbox (no keys; api.anthropic.com and the
OpenAI/OpenRouter/Google hosts are blocked; no local model server). The OpenAI-compatible client was run against a
local stdlib server answering in the documented Chat Completions and `/models` shapes, and Claude against fakes in
the Messages shapes. The built-in tiers are judgement calls, not measurements: measure with `ecadlib eval` and
override with `[ai.profiles]`. Gemini's OpenAI-compatible endpoint is assumed not to take PDFs (sent as text);
Ollama's `/v1` endpoint uses the server's context length, whatever `/api/show` reports.
