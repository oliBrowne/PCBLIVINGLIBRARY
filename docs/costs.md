# What it costs to run

ecadlib is free and open source. The AI features use **your own API key** (or a model on your own computer),
so you pay your model provider for what you use, and nothing to this project.

Most parts never touch a model. KiCad library parts, EasyEDA parts and standard packages (resistors, capacitors,
SOIC, QFN and the like) are found or built for free, and so are lifecycle, stock and KiCad upstream checks. A model
is used only to **read a datasheet** for a part nothing else covers, and for the optional **visual check** that
compares our footprint and symbol with the datasheet drawing.

## Estimated cost per job

| Model | $ in / MTok | $ out / MTok | Datasheet read | Read as batch | Visual check | 50-line BOM | 50-line BOM + checks | 500-part library | 500-part library, batch | Visual check on all 500 |
|---|---|---|---|---|---|---|---|---|---|---|
| Claude Fable 5.1 | $10 | $50 | $0.629 | $0.281 | $0.547 | $6.29 | $11.76 | $62.87 | $28.15 | $273.64 |
| Claude Opus 5.5 (default) | $4 | $20 | $0.251 | $0.113 | $0.219 | $2.51 | $4.70 | $25.15 | $11.26 | $109.46 |
| Claude Sonnet 5.5 | $2 | $10 | $0.126 | $0.056 | $0.109 | $1.26 | $2.35 | $12.57 | $5.63 | $54.73 |
| Claude Haiku 5.5 | $0.1 | $0.5 | $0.0063 | $0.0028 | $0.0055 | $0.063 | $0.118 | $0.629 | $0.281 | $2.74 |
| OpenAI GPT-6 Astra | $10 | $50 | $0.563 | n/a | $0.547 | $5.63 | $11.10 | $56.29 | n/a | $273.64 |
| OpenAI GPT-6.1 Sol | $2 | $10 | $0.113 | n/a | $0.109 | $1.13 | $2.22 | $11.26 | n/a | $54.73 |
| OpenAI GPT-5.6 Terra | $2 | $12 | $0.125 | n/a | $0.115 | $1.25 | $2.40 | $12.46 | n/a | $57.73 |
| OpenAI GPT-5.4 mini | $0.75 | $4.5 | $0.047 | n/a | $0.043 | $0.467 | $0.900 | $4.67 | n/a | $21.65 |
| OpenAI GPT-6 Luna | $0.1 | $0.5 | $0.0056 | n/a | $0.0055 | $0.056 | $0.111 | $0.563 | n/a | $2.74 |
| OpenAI GPT-5 nano | $0.05 | $0.4 | $0.0037 | n/a | $0.0032 | $0.037 | $0.069 | $0.371 | n/a | $1.59 |
| Google Gemini 4 Argon | $2 | $10 | $0.113 | n/a | $0.109 | $1.13 | $2.22 | $11.26 | n/a | $54.73 |
| Google Gemini 3.1 Pro | $2 | $12 | $0.125 | n/a | $0.115 | $1.25 | $2.40 | $12.46 | n/a | $57.73 |
| Google Gemini 3.8 Flash | $0.75 | $3.75 | $0.042 | n/a | $0.041 | $0.422 | $0.833 | $4.22 | n/a | $20.52 |
| Google Gemini 3.1 Flash-Lite | $0.25 | $1.5 | $0.016 | n/a | $0.014 | $0.156 | $0.300 | $1.56 | n/a | $7.22 |
| DeepSeek V4 Pro | $1.32 | $3.96 | $0.058 | n/a | n/a | $0.585 | n/a | $5.85 | n/a | n/a |
| DeepSeek V4.1 Flash | $0.3 | $1.2 | $0.015 | n/a | $0.016 | $0.151 | $0.306 | $1.51 | n/a | $7.76 |
| Local model (Ollama, LM Studio, vLLM) | $0 | $0 | $0 | n/a | $0 | $0 | $0 | $0 | n/a | $0 |

These are **estimates**, made from the request sizes the code builds and list prices checked on 2026-10-16. They
are not measured bills.

- **Datasheet read:** about 26,294 tokens in (6 picked datasheet pages, the prompt and the output format)
  and about 6,000 tokens out. On Claude a synchronous read bills its input as a prompt-cache write (1.25x).
- **Read as batch:** Claude's Message Batches API is half price. ecadlib sends a whole BOM as one batch
  (`ecadlib datasheet batch BOM`). Other providers are called one request at a time.
- **Visual check:** about 39,728 tokens in (8 images) and 3,000 out. It needs a model that reads images
  (n/a otherwise). Answers are cached, so re-running it costs nothing.
- **50-line BOM:** 10 of the 50 lines need a datasheet (20%), the rest cost $0.
  **500-part library:** 100 datasheet reads. "Visual check on all 500" is the extra cost of checking every part.
- **Retries cost more.** An incomplete answer is re-sent with more pages, and with `route = "ladder"` it moves
  to the next model. Budget up to about 2x for hard parts.
- **Smaller models are cheaper but weaker.** ecadlib adds guide rails for them (see `ecadlib ai rails`), and
  their answers are held for review more often.
- **Local models** (Ollama, LM Studio, vLLM, llama.cpp) cost nothing in API fees.

## Your model, your BOM

    ecadlib costs                         # these jobs for the model in your ecadlib.toml
    ecadlib costs --model gemini-3.8-flash
    ecadlib datasheet estimate BOM.csv    # this BOM: rough tokens, cost and worst case, nothing sent

Set `max_cost_usd` under `[datasheet]` and `[agent.proof]` for a hard cap on spending. If a price here is out of
date, set your own in `ecadlib.toml`:

    [ai.profiles."gpt-6-luna"]
    price_in = 0.10      # $ per million input tokens
    price_out = 0.50

Prices come from Anthropic's model table for Claude and from public price trackers for the others (benchlm.ai,
promptunit.ai). Gemini 4 Argon goes to $4/$20 on 2026-12-01 and Gemini 3.8 Flash to $1.50/$7.50 on
2027-01-01. DeepSeek bills half these rates off-peak. The figures in this page come from `ecadlib costs --all --md
docs/costs.md`.
