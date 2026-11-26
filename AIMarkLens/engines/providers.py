"""Knowledge base of AI providers and the provenance / watermark technologies they use.

Patterns are matched against metadata text (images) or content artefacts (text).
"strong" patterns name an AI product explicitly; "context" patterns only name a
vendor that also makes non-AI software (for example a phone camera), so they
are reported but never flip the verdict on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STRONG, CONTEXT = "strong", "context"


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    products: str
    image_tech: tuple[str, ...]
    text_tech: tuple[str, ...]
    invisible_check: str
    links: tuple[tuple[str, str], ...] = ()
    patterns: tuple[tuple[str, str], ...] = ()
    signer_orgs: tuple[str, ...] = ()
    compiled: tuple[tuple[re.Pattern, str], ...] = field(default=(), compare=False, repr=False)


def _provider(**kwargs) -> Provider:
    patterns = kwargs.get("patterns", ())
    compiled = tuple((re.compile(p, re.IGNORECASE), s) for p, s in patterns)
    return Provider(compiled=compiled, **kwargs)


PROVIDERS: tuple[Provider, ...] = (
    _provider(
        key="google", name="Google", products="Gemini, Imagen, Veo, Pixel and Google Photos AI editing",
        image_tech=("SynthID: an invisible watermark woven into the pixels at generation time.",
                    "IPTC DigitalSourceType in XMP and an 'Edited with Google AI' or 'Made with Google AI' credit.",
                    "C2PA Content Credentials on newer products (for example Pixel cameras and Gemini images)."),
        text_tech=("SynthID Text: a statistical watermark that biases token choices during generation. "
                   "Detection needs the generating model's tokenizer and Google's private keys.",),
        invisible_check=("SynthID cannot be read offline: only Google's own verifier can confirm it. Use Verify "
                         "online (Google Vertex AI watermark verification, set up in Settings), or upload the image "
                         "to the Gemini app and ask whether it was made with Google AI."),
        links=(("About SynthID", "https://deepmind.google/models/synthid/"),
               ("Open Gemini", "https://gemini.google.com/")),
        patterns=((r"\bsynthid\b", STRONG), (r"made with google ai", STRONG), (r"edited with google ai", STRONG),
                  (r"\bgemini\b", STRONG), (r"\bimagen\b", STRONG), (r"\bveo\b", STRONG),
                  (r"nano[\s-]?banana", STRONG), (r"\bgoogle\b", CONTEXT)),
        signer_orgs=("google",),
    ),
    _provider(
        key="openai", name="OpenAI", products="ChatGPT images, GPT Image, DALL·E 3 and Sora",
        image_tech=("C2PA Content Credentials embedded in images from ChatGPT, DALL·E 3 and the API.",
                    "Sora video adds a visible watermark and C2PA metadata."),
        text_tech=("OpenAI has said it built a text watermarking method but has not released it publicly. "
                   "Copied ChatGPT answers can carry artefacts such as 'utm_source=chatgpt.com' link parameters "
                   "or private-use citation markers."),
        invisible_check=("OpenAI offers no public invisible-watermark detector for images. Provenance relies on "
                         "C2PA metadata, which screenshots and most re-encoding remove."),
        links=(("Content Credentials Verify", "https://contentcredentials.org/verify"),),
        patterns=((r"\bopenai\b", STRONG), (r"\bchatgpt\b", STRONG), (r"dall[\s·.\-]*e", STRONG),
                  (r"\bsora\b", STRONG), (r"gpt[\s-]?image", STRONG), (r"gpt-4o", STRONG)),
        signer_orgs=("openai",),
    ),
    _provider(
        key="adobe", name="Adobe", products="Firefly, Photoshop Generative Fill and Express",
        image_tech=("C2PA Content Credentials applied automatically to Firefly-generated content.",
                    "Generative edits are recorded as C2PA actions with an AI digital source type."),
        text_tech=(),
        invisible_check="Adobe relies on Content Credentials; inspect the C2PA tab or use the Verify site.",
        links=(("Content Credentials Verify", "https://contentcredentials.org/verify"),),
        patterns=((r"\bfirefly\b", STRONG), (r"generative (fill|expand|remove)", STRONG), (r"\badobe\b", CONTEXT)),
        signer_orgs=("adobe",),
    ),
    _provider(
        key="microsoft", name="Microsoft", products="Copilot, Designer, Bing Image Creator and Azure OpenAI",
        image_tech=("C2PA Content Credentials on images from Designer, Bing Image Creator and Azure OpenAI.",),
        text_tech=("No text watermark announced for Copilot. Copied answers may include links with tracking "
                   "parameters that name the service.",),
        invisible_check="Check the C2PA manifest; Microsoft does not offer a public invisible-watermark detector.",
        links=(("Content Credentials Verify", "https://contentcredentials.org/verify"),),
        patterns=((r"bing image creator", STRONG), (r"image creator from (microsoft|designer)", STRONG),
                  (r"microsoft designer", STRONG),
                  (r"\bcopilot\b", STRONG), (r"azure openai", STRONG), (r"\bmicrosoft\b", CONTEXT)),
        signer_orgs=("microsoft",),
    ),
    _provider(
        key="meta", name="Meta", products="Meta AI (Imagine) across Facebook, Instagram and WhatsApp",
        image_tech=("Visible 'Imagined with AI' label, IPTC metadata and an invisible watermark.",
                    "Meta has published research watermarks (Stable Signature, Video Seal)."),
        text_tech=(),
        invisible_check="Meta's invisible watermark has no public detector for general use.",
        patterns=((r"\bmeta ai\b", STRONG), (r"imagine(d)? with (meta|ai)", STRONG), (r"\bmeta\b", CONTEXT)),
        signer_orgs=("meta platforms",),
    ),
    _provider(
        key="midjourney", name="Midjourney", products="Midjourney image generation",
        image_tech=("Generation details written to metadata (description, author and job identifiers).",
                    "IPTC DigitalSourceType on newer exports."),
        text_tech=(),
        invisible_check="No public invisible watermark is documented.",
        patterns=((r"\bmidjourney\b", STRONG), (r"\bjob id\b", CONTEXT)),
    ),
    _provider(
        key="stability", name="Stability AI and Stable Diffusion tools",
        products="Stable Diffusion, SDXL, DreamStudio, AUTOMATIC1111, ComfyUI, Fooocus, InvokeAI",
        image_tech=("Generation parameters stored in PNG text chunks ('parameters', 'prompt', 'workflow').",
                    "Reference scripts add an invisible DWT-DCT watermark (the invisible-watermark library).",
                    "Stealth metadata hidden in alpha or colour least-significant bits."),
        text_tech=(),
        invisible_check=("The reference DWT-DCT watermark is easily destroyed; AIMarkLens reports parameter chunks "
                         "and stealth LSB metadata, which it can read directly."),
        patterns=((r"stable[\s-]?diffusion", STRONG), (r"\bsdxl\b", STRONG), (r"stability ai", STRONG),
                  (r"dreamstudio", STRONG), (r"steps: \d+, sampler:", STRONG), (r"\bcomfyui\b", STRONG),
                  (r"\"class_type\"", STRONG), (r"\bfooocus\b", STRONG), (r"invokeai", STRONG),
                  (r"model hash:", STRONG), (r"negative prompt:", STRONG)),
    ),
    _provider(
        key="novelai", name="NovelAI", products="NovelAI image generation",
        image_tech=("Generation details in PNG text chunks and compressed stealth metadata in alpha LSBs.",),
        text_tech=(),
        invisible_check="Stealth alpha-channel metadata is checked automatically.",
        patterns=((r"\bnovelai\b", STRONG),),
    ),
    _provider(
        key="bfl", name="Black Forest Labs", products="FLUX models and API",
        image_tech=("C2PA metadata on API outputs; generation parameters when run in local tools.",),
        text_tech=(),
        invisible_check="No public invisible-watermark detector.",
        patterns=((r"black forest labs", STRONG), (r"flux[.\s-]?1", STRONG), (r"flux[.\s-]?(pro|dev|schnell|kontext)", STRONG)),
        signer_orgs=("black forest labs",),
    ),
    _provider(
        key="xai", name="xAI", products="Grok image generation (Aurora)",
        image_tech=("Metadata only; no public watermark documentation.",),
        text_tech=(),
        invisible_check="No public invisible-watermark detector.",
        patterns=((r"\bgrok\b", STRONG), (r"\bxai\b", CONTEXT)),
    ),
    _provider(
        key="others", name="Other AI tools", products="Ideogram, Leonardo, Runway, Krea, Recraft, Canva, Kling, Luma and more",
        image_tech=("Mostly metadata (software names, C2PA manifests) rather than documented invisible watermarks.",),
        text_tech=(),
        invisible_check="Varies by provider; most have no public detector.",
        links=(("Content Credentials Verify", "https://contentcredentials.org/verify"),),
        patterns=((r"\bideogram\b", STRONG), (r"leonardo[\s.]?ai", STRONG), (r"\brunway(ml)?\b", STRONG),
                  (r"\bkrea\b", STRONG), (r"\brecraft\b", STRONG), (r"magic media", STRONG), (r"\bcanva\b", CONTEXT),
                  (r"\bkling\b", STRONG), (r"luma ai|dream machine", STRONG), (r"\bpika\b", STRONG),
                  (r"\bplayground ?(ai|v\d)", STRONG), (r"\bdreamina\b", STRONG), (r"\bhunyuan\b", STRONG),
                  (r"\bqwen[\s-]?image\b", STRONG), (r"\bseedream\b", STRONG), (r"\bhailuo\b", STRONG)),
    ),
)

TEXT_ONLY_PROVIDERS: tuple[Provider, ...] = (
    _provider(
        key="anthropic", name="Anthropic", products="Claude",
        image_tech=(),
        text_tech=("Anthropic has not announced a watermark for Claude's text output, and there is no public "
                   "detector.",),
        invisible_check="Not applicable: no text watermark has been announced.",
    ),
    _provider(
        key="perplexity", name="Perplexity", products="Perplexity answers",
        image_tech=(),
        text_tech=("No text watermark announced. Copied answers can carry numbered citations and links tagged "
                   "with 'utm_source=perplexity'.",),
        invisible_check="Not applicable: no text watermark has been announced.",
    ),
)

PROVIDER_BY_KEY = {p.key: p for p in PROVIDERS + TEXT_ONLY_PROVIDERS}


def text_providers() -> list[Provider]:
    keys = ("google", "openai", "anthropic", "microsoft", "perplexity")
    return [PROVIDER_BY_KEY[k] for k in keys]


@dataclass
class SignalMatch:
    source: str
    field: str
    excerpt: str
    strength: str


def match_text(provider: Provider, text: str) -> list[tuple[str, str]]:
    """Return (matched text, strength) for each pattern found in text."""
    sample = text[:20000]
    found = []
    for regex, strength in provider.compiled:
        m = regex.search(sample)
        if m:
            found.append((m.group(0), strength))
    return found


def excerpt(text: str, needle: str, width: int = 60) -> str:
    lower = text.lower()
    idx = lower.find(needle.lower())
    if idx < 0:
        return text[:width * 2]
    start = max(0, idx - width)
    end = min(len(text), idx + len(needle) + width)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")
