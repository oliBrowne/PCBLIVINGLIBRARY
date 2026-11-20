"""Claude through Amazon Bedrock: the same Messages requests ecadlib sends to the Claude API, signed for AWS.

    [ai]
    provider = "bedrock"
    region = "us-east-1"        # "" = AWS_REGION / AWS_DEFAULT_REGION
    model = "claude-haiku-5-5"  # a Claude model name; ecadlib sends it as the region's inference profile
                                #   (us.anthropic.claude-haiku-5-5); a full Bedrock id or profile ARN is sent as given

No Anthropic key: the request is signed (AWS Signature Version 4) with your AWS credentials, read when a request is
made from the AWS environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN) or, when
botocore is installed, from anything the AWS CLI reads (profiles, SSO, instance roles). They are never written
anywhere or logged.

What differs from the Claude API, handled here and in datasheet.Client:
  - the body carries anthropic_version = "bedrock-2023-05-31" instead of the model (the model is in the URL), and
    beta headers go in the body as anthropic_beta;
  - no Message Batches (one synchronous request per part), no server-side refusal fallback, no PDF links (Bedrock
    does not fetch URLs: ecadlib must download the PDF first);
  - count_tokens goes to Bedrock's CountTokens; when that fails the rough page estimate is used, as for any refusal.
"""
import base64, datetime, hashlib, hmac, json, os, re
from urllib.parse import quote

KEY_PREFIX = "aws-bedrock:"           # the "key" a Claude client gets for Bedrock: names the region, holds no secret
BEDROCK_VERSION = "bedrock-2023-05-31"
SERVICE = "bedrock"
REGION_RE = re.compile(r"[a-z]{2}(-gov)?-[a-z]+-\d")


class BedrockError(ValueError):
    pass


def region_of(cfg_region=None, environ=None):
    env = os.environ if environ is None else environ
    r = (cfg_region or env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "").strip()
    if not r:
        raise BedrockError("Bedrock needs a region: set [ai] region (e.g. \"us-east-1\") or AWS_REGION")
    if not REGION_RE.fullmatch(r):
        raise BedrockError(f"Bedrock region {r!r} is not an AWS region name such as us-east-1")
    return r


def key_for(region):
    return KEY_PREFIX + region


def parse_key(key):
    """The region a Bedrock pseudo-key names, or None for a real Claude API key. A marker naming no AWS region is
    refused: the region becomes the host the signed request goes to."""
    if isinstance(key, str) and key.startswith(KEY_PREFIX):
        r = key[len(KEY_PREFIX):]
        if not REGION_RE.fullmatch(r):
            raise BedrockError("the Bedrock key names no AWS region (expected aws-bedrock:<region>)")
        return r
    return None


def host(region):
    return f"bedrock-runtime.{region}.amazonaws.com"


def geo(region):
    """The cross-region inference profile prefix of a region: us, eu, apac (Bedrock's naming), else None."""
    head = region.split("-")[0]
    return {"us": "us", "ca": "us", "eu": "eu", "ap": "apac"}.get(head)


def model_id(model, region):
    """'claude-haiku-5-5' -> 'us.anthropic.claude-haiku-5-5' (the region's inference profile; newer Claude models are
    served on Bedrock only through one). An id that already names Bedrock (anthropic.*, us.anthropic.*, an ARN) is
    sent as given."""
    m = str(model or "").strip()
    if not m:
        raise BedrockError("no model for Bedrock")
    if m.startswith("arn:") or "anthropic." in m:
        return m
    g = geo(region)
    return f"{g}.anthropic.{m}" if g else f"anthropic.{m}"


def body_for(body, headers=None):
    """A Claude API Messages body -> the Bedrock InvokeModel body (no model, anthropic_version, betas in the body,
    no server-side fallback). Raises BedrockError for a PDF sent as a link: Bedrock never fetches URLs."""
    if any(isinstance(t, dict) and str(t.get("type", "")).startswith(("web_search", "web_fetch"))
           for t in body.get("tools") or []):
        raise BedrockError("web search is a Claude API tool that Bedrock does not run")
    b = {k: v for k, v in body.items() if k not in ("model", "fallbacks")}
    b["anthropic_version"] = BEDROCK_VERSION
    beta = [x.strip() for x in str((headers or {}).get("anthropic-beta") or "").split(",") if x.strip()]
    beta = [x for x in beta if not x.startswith("server-side-fallback")]
    if beta:
        b["anthropic_beta"] = beta
    for m in b.get("messages") or []:
        for c in m.get("content") or [] if isinstance(m.get("content"), list) else []:
            if isinstance(c, dict) and isinstance(c.get("source"), dict) and c["source"].get("type") == "url":
                raise BedrockError("this PDF was not downloaded and Bedrock does not fetch links: download it (or put "
                                   "it in [datasheet] folder as <MPN>.pdf) to read it through Bedrock")
    return b


_BOTO = {"creds": None}


def credentials(environ=None):
    """(access key, secret key, session token or None): the AWS environment variables, else botocore's chain."""
    env = os.environ if environ is None else environ
    ak, sk = env.get("AWS_ACCESS_KEY_ID"), env.get("AWS_SECRET_ACCESS_KEY")
    if ak and sk:
        return ak, sk, env.get("AWS_SESSION_TOKEN") or None
    try:
        import botocore.session
    except ImportError:
        botocore = None
    if botocore is not None:
        try:
            if _BOTO["creds"] is None:                # kept: it refreshes itself (assume-role, SSO), asked once
                _BOTO["creds"] = botocore.session.get_session().get_credentials() or False
            if _BOTO["creds"]:
                f = _BOTO["creds"].get_frozen_credentials()
                return f.access_key, f.secret_key, f.token
        except Exception as e:                        # a missing profile, an expired SSO login: one part's error
            _BOTO["creds"] = None
            raise BedrockError(f"AWS credentials could not be loaded ({type(e).__name__}): check the AWS profile "
                               "or sign in again (aws sso login)")
    raise BedrockError("no AWS credentials: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN "
                       "for temporary ones), or install botocore to use an AWS CLI profile")


def _hmac(k, msg):
    return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()


def sign(method, url_host, path, payload, region, creds, now=None):
    """AWS Signature Version 4 headers for one request (no query string). path is the request path as sent (each
    segment already percent-encoded once); the canonical path encodes it again, as SigV4 asks for non-S3 services."""
    ak, sk, token = creds
    t = now or datetime.datetime.now(datetime.timezone.utc)
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    ph = hashlib.sha256(payload).hexdigest()
    hdrs = {"host": url_host, "x-amz-date": amz, "x-amz-content-sha256": ph}
    if token:
        hdrs["x-amz-security-token"] = token
    names = sorted(hdrs)
    canon_path = "/".join(quote(seg, safe="-_.~") for seg in path.split("/"))
    creq = "\n".join([method, canon_path, "", "".join(f"{n}:{hdrs[n].strip()}\n" for n in names), ";".join(names), ph])
    scope = f"{day}/{region}/{SERVICE}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + sk).encode(), day), region), SERVICE), "aws4_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    out = {n: hdrs[n] for n in names if n != "host"}
    out["authorization"] = f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    return out


def invoke_path(mid):
    return f"/model/{quote(mid, safe='')}/invoke"


def count_path(mid):
    return f"/model/{quote(mid, safe='')}/count-tokens"


def count_body(body):
    """Bedrock CountTokens wraps the InvokeModel body, base64-encoded."""
    b = dict(body)
    b.setdefault("max_tokens", 1)                     # InvokeModel's schema wants it; it does not change the count
    raw = json.dumps(body_for(b)).encode()
    return {"input": {"invokeModel": {"body": base64.b64encode(raw).decode()}}}


def error_of(payload, headers):
    """(type, message) of a Bedrock error: {"message": ...} with the type in x-amzn-ErrorType."""
    et = str((headers or {}).get("x-amzn-ErrorType") or "").split(":")[0] or "?"
    msg = payload.get("message") or payload.get("Message") or "" if isinstance(payload, dict) else ""
    return et, msg


HINTS = {"ExpiredTokenException": "the AWS session token has expired: get fresh credentials",
         "UnrecognizedClientException": "the AWS access key was not recognised: check the credentials",
         "AccessDeniedException": "these AWS credentials may not call this model: check the IAM policy and that "
                                  "model access is enabled in the Bedrock console for this region",
         "ThrottlingException": "Bedrock is throttling requests (quota per minute)",
         "ServiceQuotaExceededException": "the Bedrock quota for this model is used up"}
