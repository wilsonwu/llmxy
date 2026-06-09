from __future__ import annotations

OPENAI_CHAT = "openai.chat"
OPENAI_RESPONSES = "openai.responses"
OPENAI_EMBEDDINGS = "openai.embeddings"
OPENAI_IMAGES = "openai.images"
ANTHROPIC_MESSAGES = "anthropic.messages"
GEMINI_GENERATE_CONTENT = "gemini.generate_content"
GEMINI_EMBEDDINGS = "gemini.embeddings"
GEMINI_IMAGES = "gemini.images"

CHAT_PROTOCOLS = [OPENAI_CHAT, OPENAI_RESPONSES, ANTHROPIC_MESSAGES, GEMINI_GENERATE_CONTENT]
EMBEDDING_PROTOCOLS = [OPENAI_EMBEDDINGS, GEMINI_EMBEDDINGS]
IMAGE_PROTOCOLS = [OPENAI_IMAGES, GEMINI_IMAGES]

_ALIASES: dict[str, str] = {
    "openai": OPENAI_CHAT,
    "openai_chat": OPENAI_CHAT,
    "openai-chat": OPENAI_CHAT,
    "openai.chat_completions": OPENAI_CHAT,
    "openai.chat-completions": OPENAI_CHAT,
    "openai.chat.completions": OPENAI_CHAT,
    "chat": OPENAI_CHAT,
    "chat.completions": OPENAI_CHAT,
    "responses": OPENAI_RESPONSES,
    "response": OPENAI_RESPONSES,
    "openai_response": OPENAI_RESPONSES,
    "openai-response": OPENAI_RESPONSES,
    "openai.responses": OPENAI_RESPONSES,
    "openai.response": OPENAI_RESPONSES,
    "embedding": OPENAI_EMBEDDINGS,
    "embeddings": OPENAI_EMBEDDINGS,
    "openai.embedding": OPENAI_EMBEDDINGS,
    "openai.embeddings": OPENAI_EMBEDDINGS,
    "image": OPENAI_IMAGES,
    "images": OPENAI_IMAGES,
    "openai.image": OPENAI_IMAGES,
    "openai.images": OPENAI_IMAGES,
    "anthropic": ANTHROPIC_MESSAGES,
    "message": ANTHROPIC_MESSAGES,
    "messages": ANTHROPIC_MESSAGES,
    "anthropic.message": ANTHROPIC_MESSAGES,
    "anthropic.messages": ANTHROPIC_MESSAGES,
    "gemini": GEMINI_GENERATE_CONTENT,
    "gemini.generatecontent": GEMINI_GENERATE_CONTENT,
    "gemini.generate_content": GEMINI_GENERATE_CONTENT,
    "gemini.embedding": GEMINI_EMBEDDINGS,
    "gemini.embeddings": GEMINI_EMBEDDINGS,
    "gemini.image": GEMINI_IMAGES,
    "gemini.images": GEMINI_IMAGES,
    "azure": OPENAI_CHAT,
    "azure_openai": OPENAI_CHAT,
    "azure-openai": OPENAI_CHAT,
    "openai-compatible": OPENAI_CHAT,
}

_DEFAULT_BY_FAMILY_KIND: dict[tuple[str, str], str] = {
    ("openai", "chat"): OPENAI_CHAT,
    ("openai", "embedding"): OPENAI_EMBEDDINGS,
    ("openai", "image"): OPENAI_IMAGES,
    ("anthropic", "chat"): ANTHROPIC_MESSAGES,
    ("gemini", "chat"): GEMINI_GENERATE_CONTENT,
    ("gemini", "embedding"): GEMINI_EMBEDDINGS,
    ("gemini", "image"): GEMINI_IMAGES,
}


def normalize_protocol(protocol: str | None, *, kind: str | None = None) -> str:
    raw = (protocol or OPENAI_CHAT).lower().strip().replace("/", ".")
    value = _ALIASES.get(raw, raw)
    if kind:
        return protocol_for_kind(value, kind)
    return value


def protocol_family(protocol: str | None) -> str:
    return normalize_protocol(protocol).split(".", 1)[0]


def protocol_for_kind(protocol: str | None, kind: str | None) -> str:
    value = normalize_protocol(protocol)
    normalized_kind = (kind or "chat").lower().strip()
    if normalized_kind == "chat" and value in CHAT_PROTOCOLS:
        return value
    family = value.split(".", 1)[0]
    return _DEFAULT_BY_FAMILY_KIND.get((family, normalized_kind), value)


def protocol_label(protocol: str | None) -> str:
    return normalize_protocol(protocol).replace(".", " ")