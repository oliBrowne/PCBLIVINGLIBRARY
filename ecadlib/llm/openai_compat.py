"""An OpenAI-compatible /v1/chat/completions endpoint behind the same interface as datasheet.Client.

messages(body, headers) takes our Messages-API-shaped request and returns a Messages-API-shaped answer, so the
datasheet reader, visual check, cache and cost ledger need no second code path:

  request   system blocks -> one system message; text -> text parts; image (base64) -> image_url data URL; a PDF
            document -> a file part (only when the profile says the endpoint reads PDFs; otherwise the rails turn it
            into page text first: ecadlib.llm.rails); assistant turns (a feedback retry) -> their text.
            output_config.format json_schema -> response_format json_schema (strict) when the profile has json_schema,
            else json_object; effort, cache_control, fallbacks and other Anthropic-only fields are dropped.
  answer    choices[0].message.content -> one text block; finish_reason stop/length/content_filter -> stop_reason
            end_turn/max_tokens/refusal (message.refusal too); usage prompt/completion tokens -> input/output tokens.

Retries 408/409/429/5xx and network errors with backoff (retry-after honoured), like datasheet.Client. A server that
rejects response_format with HTTP 400 is asked once more without it (json_object, then none), and the answer goes
through the JSON-repair rail. Every request is logged as one JSON line without the key or the image/PDF bytes.
The key is only ever sent to the configured base_url."""
import datetime, json, random, time
from pathlib import Path

import requests

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
FINISH = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal", "tool_calls": "tool_use"}


class OpenAICompatClient:
    def __init__(self, base_url, key=None, profile=None, retries=4, backoff=2.0, max_wait=60.0, timeout=600, sleep=None,
                 log_file=None, say=None, post=None):
        from .. import datasheet as D
        self.base, self.key = base_url.rstrip("/"), key
        self.profile = dict(profile or {})
        self.retries, self.backoff, self.max_wait, self.timeout = retries, backoff, max_wait, timeout
        self.sleep = sleep or time.sleep
        self.log_file, self.say = log_file or D._LOG["file"], say or D._LOG["say"]
        self._post = post
        self.format_fallback = None                        # set when the server rejected json_schema once

    # --- translation
    def to_chat(self, body):
        from ..datasheet import DatasheetError
        msgs = []
        sys_txt = "\n\n".join(b.get("text", "") for b in body.get("system") or [] if isinstance(b, dict)) \
            if isinstance(body.get("system"), list) else (body.get("system") or "")
        if sys_txt:
            msgs.append({"role": "system", "content": sys_txt})
        for m in body.get("messages") or []:
            content = m.get("content")
            if isinstance(content, str):
                msgs.append({"role": m["role"], "content": content})
                continue
            if m["role"] == "assistant":
                msgs.append({"role": "assistant", "content": "".join(b.get("text", "") for b in content if b.get("type") == "text")})
                continue
            parts = []
            for b in content:
                t = b.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    src = b["source"]
                    url = src["url"] if src.get("type") == "url" else f"data:{src['media_type']};base64,{src['data']}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                elif t == "document":
                    src = b["source"]
                    if not self.profile.get("pdf"):
                        raise DatasheetError("this model does not take PDF input and the PDF was not turned into page text "
                                             "(set [ai.profiles] pdf = true if it does)")
                    if src.get("type") != "base64":
                        raise DatasheetError("this endpoint can't fetch a PDF by URL: the datasheet must be downloaded here")
                    parts.append({"type": "file", "file": {"filename": "datasheet.pdf",
                                                           "file_data": f"data:application/pdf;base64,{src['data']}"}})
            msgs.append({"role": m["role"], "content": parts})
        out = {"model": body["model"], "messages": msgs}
        mt = body.get("max_tokens")
        if mt:
            out[self.profile.get("max_tokens_param") or "max_tokens"] = mt
        fmt = ((body.get("output_config") or {}).get("format") or {})
        mode = self.format_fallback or ("json_schema" if self.profile.get("json_schema") else "json_object")
        if fmt.get("type") == "json_schema" and mode == "json_schema":
            out["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "answer", "schema": fmt["schema"], "strict": True}}
        elif fmt.get("type") == "json_schema" and mode == "json_object":
            out["response_format"] = {"type": "json_object"}
        if "temperature" in body:
            out["temperature"] = body["temperature"]
        return out

    @staticmethod
    def to_message(resp, model):
        ch = (resp.get("choices") or [{}])[0]
        m = ch.get("message") or {}
        text = m.get("content")
        if isinstance(text, list):                           # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        stop = FINISH.get(ch.get("finish_reason"), "end_turn")
        if m.get("refusal"):
            stop = "refusal"
        u = resp.get("usage") or {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        return {"id": resp.get("id"), "type": "message", "role": "assistant", "model": resp.get("model") or model,
                "content": [{"type": "text", "text": text or ""}], "stop_reason": stop,
                "stop_details": {"type": "refusal", "category": None, "explanation": m.get("refusal")} if stop == "refusal" else None,
                "usage": {"input_tokens": int(u.get("prompt_tokens") or 0) - int(cached), "cache_read_input_tokens": int(cached),
                          "output_tokens": int(u.get("completion_tokens") or 0)}}

    # --- HTTP
    def _log(self, rec):
        if self.log_file:
            Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def _summary(self, chat):
        def s(v):
            if isinstance(v, dict):
                return {k: (f"<{len(x)} chars>" if k in ("url", "file_data") and isinstance(x, str) and x.startswith("data:")
                            else f"<json schema>" if k == "schema" else s(x)) for k, x in v.items()}
            if isinstance(v, list):
                return [s(x) for x in v]
            if isinstance(v, str) and len(v) > 300:
                return v[:120] + f"... <{len(v)} chars>"
            return v
        return s(chat)

    def chat(self, chat):
        from ..datasheet import APIError
        url = self.base + "/chat/completions"
        h = {"content-type": "application/json"}
        if self.key and self.key != "no-key-needed":
            h["authorization"] = f"Bearer {self.key}"
        post = self._post or requests.post
        for attempt in range(self.retries + 1):
            t0 = time.time()
            rec = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "method": "POST", "url": url,
                   "attempt": attempt + 1, "body": self._summary(chat)}
            retry_after = None
            try:
                r = post(url, headers=h, json=chat, timeout=self.timeout)
            except requests.RequestException as e:
                err = APIError(f"{self.base}: request failed: {type(e).__name__}: {str(e)[:200]}", retryable=True)
                rec.update(error=str(err), ms=int((time.time() - t0) * 1000))
            else:
                try:
                    payload = r.json()
                except ValueError:
                    payload = None
                rec.update(status=r.status_code, ms=int((time.time() - t0) * 1000))
                if r.status_code == 200 and isinstance(payload, dict) and "choices" in payload:
                    rec["usage"] = payload.get("usage")
                    self._log(rec)
                    return payload
                e = (payload or {}).get("error") if isinstance(payload, dict) else None
                emsg = (e.get("message") if isinstance(e, dict) else e) or (getattr(r, "text", "") or "")[:200]
                hint = {401: " (the key was rejected: check it and the variable it is read from)",
                        404: " (unknown model or endpoint: check [ai] model and base_url)",
                        429: " (rate limited)"}.get(r.status_code, "")
                err = APIError(f"{self.base}: HTTP {r.status_code}: {str(emsg)[:300]}{hint}", r.status_code,
                               (e or {}).get("type") if isinstance(e, dict) else None, r.status_code in RETRY_STATUS)
                rec["error"] = str(emsg)[:300]
                retry_after = (getattr(r, "headers", None) or {}).get("retry-after")
            self._log(rec)
            if not err.retryable or attempt == self.retries:
                raise err
            try:
                wait = float(retry_after) if retry_after is not None else None
            except ValueError:
                wait = None
            wait = min(self.max_wait * 5, wait) if wait is not None else min(self.max_wait, self.backoff * 2 ** attempt + random.uniform(0, 1))
            if self.say:
                self.say(f"  model API: {err.args[0][:120]}; retry {attempt + 1}/{self.retries} in {wait:.0f}s")
            self.sleep(wait)

    def messages(self, body, headers=None):
        """Messages-shaped body -> Messages-shaped answer (see the module docstring)."""
        from ..datasheet import APIError
        chat = self.to_chat(body)
        try:
            resp = self.chat(chat)
        except APIError as e:
            if e.status == 400 and "response_format" in chat and self.format_fallback != "none":
                # the server does not take this response format: ask once more with a looser one (the rails repair JSON)
                self.format_fallback = "json_object" if chat["response_format"]["type"] == "json_schema" else "none"
                return self.messages(body, headers)
            raise
        return self.to_message(resp, body["model"])

    def count_tokens(self, body):
        raise NotImplementedError("count_tokens is an Anthropic endpoint")
