"""[ai] provider = "bedrock": Claude's own requests sent through Amazon Bedrock, signed with AWS credentials."""
import datetime, json

from ecadlib import datasheet as D
from ecadlib import llm as LL
from ecadlib.llm import bedrock as BR
from tests.fixtures_lite import lite

AWS = {"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
       "AWS_SESSION_TOKEN": "tok", "AWS_REGION": "us-east-1"}
RULES = {"ai": {"provider": "bedrock", "model": "claude-haiku-5-5"}}


def _env(monkeypatch, **extra):
    for k, v in {**AWS, **extra}.items():
        monkeypatch.setenv(k, v)


def test_endpoint_is_claude_with_no_key_or_url():
    ep = LL.endpoint({"ai": {"provider": "bedrock", "region": "eu-west-1"}}, "datasheet")
    assert ep["provider"] == "anthropic" and ep["preset"] == "bedrock"
    assert ep["base_url"] == "" and ep["key_env"] == "" and ep["region"] == "eu-west-1"
    assert ep["model"] == "claude-opus-5-5"            # the Claude default, as for the Claude API
    assert LL.key_for(ep, environ={}) == "aws-bedrock:eu-west-1"
    assert BR.parse_key("aws-bedrock:eu-west-1") == "eu-west-1" and BR.parse_key("sk-ant-x") is None


def test_region_from_environment_and_refused_when_missing_or_odd():
    ep = LL.endpoint(RULES, "datasheet")
    assert LL.key_for(ep, environ={"AWS_DEFAULT_REGION": "us-west-2"}) == "aws-bedrock:us-west-2"
    assert LL.key_for(ep, environ={}) is None
    assert "region" in LL.needs_key_message(ep)
    try:
        BR.region_of("us-east-1/../x")
        raise AssertionError("an odd region must be refused")
    except BR.BedrockError:
        pass


def test_model_ids():
    assert BR.model_id("claude-haiku-5-5", "us-east-1") == "us.anthropic.claude-haiku-5-5"
    assert BR.model_id("claude-opus-5-5", "eu-central-1") == "eu.anthropic.claude-opus-5-5"
    assert BR.model_id("claude-opus-5-5", "ap-northeast-1") == "apac.anthropic.claude-opus-5-5"
    assert BR.model_id("global.anthropic.claude-haiku-5-5", "us-east-1") == "global.anthropic.claude-haiku-5-5"
    arn = "arn:aws:bedrock:us-east-1:1:application-inference-profile/abc"
    assert BR.model_id(arn, "us-east-1") == arn


def test_body_translation():
    body = {"model": "claude-opus-5-5", "max_tokens": 10, "fallbacks": "default",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    got = BR.body_for(body, {"anthropic-beta": "server-side-fallback-2026-07-01, other-beta"})
    assert "model" not in got and "fallbacks" not in got
    assert got["anthropic_version"] == "bedrock-2023-05-31" and got["anthropic_beta"] == ["other-beta"]
    url_doc = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "url", "url": "https://x/a.pdf"}}]}]}
    try:
        BR.body_for(url_doc)
        raise AssertionError("Bedrock does not fetch links: a url source must be refused")
    except BR.BedrockError as e:
        assert "download" in str(e)


def test_signature_matches_botocore_when_installed():
    t = datetime.datetime(2026, 11, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    creds = (AWS["AWS_ACCESS_KEY_ID"], AWS["AWS_SECRET_ACCESS_KEY"], "tok")
    path = BR.invoke_path("anthropic.claude-haiku-4-5-20251001-v1:0")
    assert path == "/model/anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke"
    h = BR.sign("POST", "bedrock-runtime.us-east-1.amazonaws.com", path, b"{}", "us-east-1", creds, now=t)
    assert h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20261120/us-east-1/bedrock/aws4_request")
    assert h["x-amz-security-token"] == "tok" and h["x-amz-date"] == "20261120T120000Z"
    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
    except ImportError:
        return                                         # CI has no botocore: the format checks above still ran
    req = AWSRequest(method="POST", url="https://bedrock-runtime.us-east-1.amazonaws.com" + path, data=b"{}",
                     headers={"x-amz-content-sha256": h["x-amz-content-sha256"]})
    SigV4Auth(Credentials(*creds), "bedrock", "us-east-1").add_auth(req)       # signs at its own clock time
    when = datetime.datetime.strptime(req.headers["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    ours = BR.sign("POST", "bedrock-runtime.us-east-1.amazonaws.com", path, b"{}", "us-east-1", creds, now=when)
    assert req.headers["Authorization"] == ours["authorization"]


class _Resp:
    def __init__(self, status, payload, headers=None):
        self.status_code, self._p, self.headers = status, payload, headers or {}

    def json(self):
        return self._p


@lite
def test_client_sends_signed_invoke_without_api_key(monkeypatch):
    _env(monkeypatch)
    sent = []
    answer = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
              "content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn",
              "usage": {"input_tokens": 5, "output_tokens": 2}}

    def post(url, **kw):
        sent.append((url, kw))
        return _Resp(200, answer)
    monkeypatch.setattr(D.requests, "post", post)
    key = D.require_key(None, RULES)
    assert key == "aws-bedrock:us-east-1"
    c = D.Client(key, sleep=lambda s: None)
    got = c.messages({"model": "claude-haiku-5-5", "max_tokens": 5,
                      "messages": [{"role": "user", "content": "hi"}]})
    assert got == answer
    url, kw = sent[0]
    assert url == "https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-haiku-5-5/invoke"
    assert "x-api-key" not in kw["headers"] and kw["headers"]["authorization"].startswith("AWS4-HMAC-SHA256")
    assert AWS["AWS_SECRET_ACCESS_KEY"] not in json.dumps(kw["headers"])
    body = json.loads(kw["data"])
    assert "model" not in body and body["anthropic_version"] == BR.BEDROCK_VERSION


@lite
def test_client_errors_retries_and_no_batches(monkeypatch):
    _env(monkeypatch)
    calls = []

    def post(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return _Resp(429, {"message": "slow down"}, {"x-amzn-ErrorType": "ThrottlingException:http://x"})
        return _Resp(403, {"message": "The security token included in the request is expired"},
                     {"x-amzn-ErrorType": "ExpiredTokenException"})
    monkeypatch.setattr(D.requests, "post", post)
    c = D.Client("aws-bedrock:us-east-1", sleep=lambda s: None)
    try:
        c.messages({"model": "claude-haiku-5-5", "max_tokens": 5, "messages": []})
        raise AssertionError("an expired token must raise")
    except D.APIError as e:
        assert len(calls) == 2                         # the throttle was retried, the expired token was not
        assert e.status == 403 and "ExpiredTokenException" in str(e) and "fresh credentials" in str(e)
    try:
        c.batch_create([])
        raise AssertionError("Bedrock has no Message Batches")
    except D.APIError as e:
        assert "Batches" in str(e)


@lite
def test_count_tokens_through_bedrock(monkeypatch):
    _env(monkeypatch)
    seen = []

    def post(url, **kw):
        seen.append((url, json.loads(kw["data"])))
        return _Resp(200, {"inputTokens": 1234})
    monkeypatch.setattr(D.requests, "post", post)
    c = D.Client("aws-bedrock:us-east-1")
    assert c.count_tokens({"model": "claude-haiku-5-5", "messages": [{"role": "user", "content": "hi"}]}) == 1234
    assert seen[0][0].endswith("/model/us.anthropic.claude-haiku-5-5/count-tokens")
    assert "invokeModel" in seen[0][1]["input"]


def test_rules_accept_bedrock():
    errs = []
    LL.validate("x", {"provider": "bedrock", "region": "us-east-1"}, [], lambda *a: errs.append(a))
    assert errs == []


@lite
def test_reader_on_bedrock_sends_no_batch_and_no_web_search(tmp_path, monkeypatch):
    _env(monkeypatch)
    r = D.Reader(tmp_path / "out", tmp_path / "cache", rules=RULES, batch=True, log=None, handoff=False)
    assert r.key == "aws-bedrock:us-east-1" and r.batch is False
    attempts = []
    assert r._web_search("X1", None, attempts) == (None, None)
    assert "Bedrock" in attempts[-1]["error"]
