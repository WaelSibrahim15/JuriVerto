import asyncio
import html
import os
import json
import re
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

APP_TITLE = "JuriVerto API"
PORT = int(os.getenv("PORT", "8001"))

DEFAULT_PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", "openai")
DEFAULT_FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "deepl")
SIMULATE_PRIMARY_FAILURE = os.getenv("SIMULATE_PRIMARY_FAILURE", "false").lower() in {"1", "true", "yes"}

PROVIDER_CATALOG: dict[str, list[str]] = {
    "openai": ["gpt-4.1", "gpt-4o", "gpt-5"],
    "deepl": ["deepl-pro", "deepl-next-gen"],
}
ARBITER_MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-opus-4.6": "claude-opus-4-6",
}
ARBITER_MODEL_CATALOG = ["claude-sonnet-4-6", "claude-opus-4-6"]
DEFAULT_ARBITER_MODEL = "claude-opus-4-6"
ARBITER_FALLBACK_MODES = {"strict_legal", "balanced"}
DEFAULT_ARBITER_FALLBACK_MODE = "strict_legal"

app = FastAPI(title=APP_TITLE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranslateRequest(BaseModel):
    sourceText: str = Field(min_length=1)
    sourceLang: str
    targetLang: str
    domain: str = "legal"
    strictness: str = "strict"
    selectedProvider: str = DEFAULT_PRIMARY_PROVIDER
    selectedModel: str | None = None
    fallbackProvider: str = DEFAULT_FALLBACK_PROVIDER
    providerApiKeys: dict[str, str] | None = None
    modelApiKeys: dict[str, str] | None = None
    arbiter: dict[str, Any] | None = None
    debug: bool = False


class TraceStep(BaseModel):
    step: str
    status: str
    provider: str | None = None
    durationMs: int = 0
    message: str | None = None
    metadata: dict[str, Any] | None = None


class KeyValidationRequest(BaseModel):
    provider: str
    model: str
    apiKey: str


DEEPL_LANGUAGE_CODES: dict[str, str] = {
    "arabic": "AR",
    "chinese": "ZH",
    "english": "EN",
    "french": "FR",
    "german": "DE",
    "italian": "IT",
    "japanese": "JA",
    "portuguese": "PT",
    "spanish": "ES",
    "turkish": "TR",
}


def _clean_text(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:[\w-]+)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def normalize_arbiter_model(model_name: str) -> str:
    normalized = model_name.strip()
    return ARBITER_MODEL_ALIASES.get(normalized, normalized)


def normalize_arbiter_fallback_mode(mode_name: str) -> str:
    normalized = str(mode_name or "").strip().lower()
    if normalized in ARBITER_FALLBACK_MODES:
        return normalized
    return DEFAULT_ARBITER_FALLBACK_MODE


def _build_translation_prompt(source_lang: str, target_lang: str, domain: str, text: str) -> str:
    return (
        f"Translate from {source_lang} to {target_lang}.\n"
        f"Domain: {domain}.\n"
        "Preserve meaning, legal precision, numbering, punctuation, and line breaks.\n"
        "Preserve paragraph/section numbering and references exactly (examples: 1., 1.1, (a), Section 301, Article 12, § 4).\n"
        "Preserve formatting markers exactly when present: HTML tags (<b>, <strong>, <u>, <em>, <i>, <br>) and markdown emphasis markers.\n"
        "If you see placeholders like __FMT_0__, keep them unchanged and in the correct relative position.\n"
        "If the input contains a table (HTML, markdown, or tabular text), preserve row/column structure "
        "and output the table as HTML <table>.\n"
        "Output only translated text with no explanation.\n\n"
        f"Text:\n{text}"
    )


def _deepl_language_code(language_name: str) -> str:
    code = DEEPL_LANGUAGE_CODES.get(language_name.strip().lower())
    if code:
        return code
    cleaned = re.sub(r"[^A-Za-z]", "", language_name)
    return cleaned[:2].upper() if cleaned else "EN"


def _contains_html_table(text: str) -> bool:
    return bool(re.search(r"<\s*table\b", text, flags=re.IGNORECASE))


def _contains_html_table_fragment(text: str) -> bool:
    if _contains_html_table(text):
        return True
    has_row = bool(re.search(r"<\s*tr\b", text, flags=re.IGNORECASE))
    has_cell = bool(re.search(r"<\s*t[dh]\b", text, flags=re.IGNORECASE))
    return has_row and has_cell


def _normalize_html_table_fragment(text: str) -> str:
    raw = text.strip()
    if not raw:
        return text
    if _contains_html_table(raw):
        return raw
    if not _contains_html_table_fragment(raw):
        return raw

    inner = raw
    has_section = bool(re.search(r"<\s*(thead|tbody|tfoot)\b", inner, flags=re.IGNORECASE))
    if not has_section:
        inner = f"<tbody>{inner}</tbody>"
    return f"<table>{inner}</table>"


def _looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if "|" not in lines[0] or "|" not in lines[1]:
        return False
    separator = lines[1].replace(" ", "")
    return bool(re.fullmatch(r"\|?[:\-|]+\|?", separator) and "-" in separator)


def _split_markdown_cells(line: str) -> list[str]:
    raw_cells = [cell.strip() for cell in line.strip().split("|")]
    if raw_cells and raw_cells[0] == "":
        raw_cells = raw_cells[1:]
    if raw_cells and raw_cells[-1] == "":
        raw_cells = raw_cells[:-1]
    return raw_cells


def _looks_like_tsv_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    tab_lines = [line for line in lines if "\t" in line]
    if len(tab_lines) < 2:
        return False
    widths = [len(line.split("\t")) for line in tab_lines[:12]]
    return min(widths) >= 2 and (max(widths) - min(widths) <= 1)


def _rows_to_html_table(rows: list[list[str]], has_header: bool) -> str:
    if not rows:
        return ""
    safe_rows = [[html.escape(cell.strip()) for cell in row] for row in rows]
    parts: list[str] = ['<table><tbody>']
    start_index = 0
    if has_header and safe_rows:
        parts.append("<tr>")
        for cell in safe_rows[0]:
            parts.append(f"<th>{cell}</th>")
        parts.append("</tr>")
        start_index = 1
    for row in safe_rows[start_index:]:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{cell}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _markdown_table_to_html(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return text
    header = _split_markdown_cells(lines[0])
    body_lines = [line for line in lines[2:] if "|" in line]
    rows = [header] + [_split_markdown_cells(line) for line in body_lines]
    if not rows or not rows[0]:
        return text
    return _rows_to_html_table(rows, has_header=True)


def _tsv_table_to_html(text: str) -> str:
    rows = [line.split("\t") for line in text.splitlines() if line.strip()]
    if not rows or max(len(row) for row in rows) < 2:
        return text
    max_width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (max_width - len(row)) for row in rows]
    return _rows_to_html_table(normalized_rows, has_header=False)


def _prepare_text_for_table_translation(text: str) -> str:
    raw = text.strip()
    if not raw:
        return text
    if _contains_html_table_fragment(raw):
        return _normalize_html_table_fragment(raw)
    if _looks_like_markdown_table(raw):
        return _markdown_table_to_html(raw)
    if _looks_like_tsv_table(raw):
        return _tsv_table_to_html(raw)
    return raw


TABLE_CELL_RE = re.compile(r"<(td|th)(\b[^>]*)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
TABLE_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", flags=re.IGNORECASE | re.DOTALL)
FORMAT_TAG_RE = re.compile(
    r"</?(?:b|strong|u|em|i|sup|sub|span)(?:\s+[^>]*)?>|<br\s*/?>",
    flags=re.IGNORECASE,
)


def _html_fragment_to_plain_text(fragment: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _plain_text_to_html_fragment(text: str) -> str:
    escaped = html.escape(text.strip())
    return escaped.replace("\n", "<br>")


def _protect_format_tokens(fragment: str) -> tuple[str, dict[str, str]]:
    tokens: dict[str, str] = {}

    def _repl(match: re.Match[str]) -> str:
        token = f"__FMT_{len(tokens)}__"
        tokens[token] = match.group(0)
        return token

    protected = FORMAT_TAG_RE.sub(_repl, fragment)
    return protected, tokens


def _restore_format_tokens(text: str, tokens: dict[str, str]) -> str:
    restored = text
    for token, original_tag in tokens.items():
        restored = restored.replace(token, original_tag)
    return restored


async def _translate_preserving_html_table_cells(
    *,
    provider: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    model: str | None,
    api_key: str,
) -> str:
    matches = list(TABLE_CELL_RE.finditer(source_text))
    if not matches:
        return source_text

    rendered_parts: list[str] = []
    last_end = 0
    for match in matches:
        tag = match.group(1)
        attrs = match.group(2) or ""
        inner_html = match.group(3) or ""
        rendered_parts.append(source_text[last_end:match.start()])

        protected_inner_html, tokens = _protect_format_tokens(inner_html)
        cell_text = _html_fragment_to_plain_text(protected_inner_html)
        if cell_text:
            translated_cell = await translate_with_provider(
                provider=provider,
                text=cell_text,
                source_lang=source_lang,
                target_lang=target_lang,
                domain=domain,
                model=model,
                api_key=api_key,
                preserve_html_tables=False,
            )
            translated_inner_html = _plain_text_to_html_fragment(_clean_text(translated_cell))
            translated_inner_html = _restore_format_tokens(translated_inner_html, tokens)
        else:
            translated_inner_html = ""

        rendered_parts.append(f"<{tag}{attrs}>{translated_inner_html}</{tag}>")
        last_end = match.end()

    rendered_parts.append(source_text[last_end:])
    return "".join(rendered_parts)


def _table_shape_signature(table_html: str) -> dict[str, Any]:
    normalized = _normalize_html_table_fragment(_prepare_text_for_table_translation(table_html))
    row_matches = TABLE_ROW_RE.findall(normalized)
    cols_per_row: list[int] = []
    for row_inner in row_matches:
        cols = len(re.findall(r"<t[dh]\b", row_inner, flags=re.IGNORECASE))
        cols_per_row.append(cols)
    return {
        "rows": len(cols_per_row),
        "colsPerRow": cols_per_row,
        "cells": sum(cols_per_row),
    }


def _extract_first_table_html(text: str) -> str | None:
    prepared = _prepare_text_for_table_translation(text)
    if not _contains_html_table(prepared):
        return None
    match = re.search(r"<table\b[^>]*>.*?</table>", prepared, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(0)
    return _normalize_html_table_fragment(prepared)


def _extract_openai_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text", "")).strip()
    if direct:
        return direct

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = str(content.get("text", "")).strip()
            if content.get("type") in {"output_text", "text"} and text:
                parts.append(text)
    if parts:
        return "\n".join(parts).strip()

    # Fallback parser for chat-style payloads.
    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    collected: list[str] = []
                    for block in content:
                        if isinstance(block, dict):
                            maybe_text = str(block.get("text", "")).strip()
                            if maybe_text:
                                collected.append(maybe_text)
                    if collected:
                        return "\n".join(collected).strip()

    return ""


async def translate_with_openai(
    *,
    text: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    model: str,
    api_key: str,
) -> str:
    if not api_key.strip():
        raise ValueError("Missing OpenAI API key.")

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "content-type": "application/json",
    }
    system_prompt = (
        "You are a legal translator. Translate faithfully with precise legal terminology. "
        "Do not add commentary. Return only the translated text."
    )
    prepared_text = _prepare_text_for_table_translation(text)
    user_prompt = _build_translation_prompt(source_lang, target_lang, domain, prepared_text)

    async with httpx.AsyncClient(timeout=45.0) as client:
        responses_req = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_output_tokens": 2400,
        }
        responses_resp = await client.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=responses_req,
        )
        if responses_resp.status_code == 200:
            output = _extract_openai_text(responses_resp.json())
            cleaned = _clean_text(output)
            if cleaned:
                return cleaned
            raise RuntimeError("OpenAI returned an empty translation.")

        # Some model/accounts can reject /responses; fallback to chat/completions.
        chat_req = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        chat_resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=chat_req,
        )
        if chat_resp.status_code == 200:
            output = _extract_openai_text(chat_resp.json())
            cleaned = _clean_text(output)
            if cleaned:
                return cleaned
            raise RuntimeError("OpenAI chat endpoint returned an empty translation.")

    responses_body = responses_resp.text[:220]
    chat_body = chat_resp.text[:220]
    raise RuntimeError(
        "OpenAI translation failed. "
        f"/responses HTTP {responses_resp.status_code}: {responses_body} "
        f"| /chat/completions HTTP {chat_resp.status_code}: {chat_body}"
    )


async def translate_with_deepl(
    *,
    text: str,
    source_lang: str,
    target_lang: str,
    api_key: str,
) -> str:
    if not api_key.strip():
        raise ValueError("Missing DeepL API key.")

    prepared_text = _prepare_text_for_table_translation(text)
    source_code = _deepl_language_code(source_lang)
    target_code = _deepl_language_code(target_lang)
    headers = {"Authorization": f"DeepL-Auth-Key {api_key.strip()}"}
    html_like = bool(re.search(r"<\s*(table|tr|td|th|p|div|span|br|b|strong|u|em|i|ol|ul|li)\b", prepared_text, flags=re.IGNORECASE))

    is_free_key = api_key.strip().endswith(":fx")
    endpoints = (
        ["https://api-free.deepl.com/v2/translate", "https://api.deepl.com/v2/translate"]
        if is_free_key
        else ["https://api.deepl.com/v2/translate", "https://api-free.deepl.com/v2/translate"]
    )

    failures: list[str] = []
    async with httpx.AsyncClient(timeout=45.0) as client:
        for url in endpoints:
            form_data: dict[str, str] = {
                "text": prepared_text,
                "target_lang": target_code,
            }
            if source_code:
                form_data["source_lang"] = source_code
            if html_like:
                form_data["tag_handling"] = "html"

            resp = await client.post(url, headers=headers, data=form_data)
            if resp.status_code == 200:
                payload = resp.json()
                translations = payload.get("translations", [])
                if not translations:
                    raise RuntimeError("DeepL returned no translations.")
                translated = _clean_text(str(translations[0].get("text", "")))
                if not translated:
                    raise RuntimeError("DeepL returned an empty translation.")
                return translated

            failures.append(f"{url} => HTTP {resp.status_code}: {resp.text[:180]}")

    raise RuntimeError("DeepL translation failed on all endpoints. " + " | ".join(failures))


async def translate_with_provider(
    *,
    provider: str,
    text: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    model: str | None,
    api_key: str,
    preserve_html_tables: bool = True,
) -> str:
    if preserve_html_tables:
        prepared_text = _prepare_text_for_table_translation(text)
        if _contains_html_table(prepared_text):
            return await _translate_preserving_html_table_cells(
                provider=provider,
                source_text=prepared_text,
                source_lang=source_lang,
                target_lang=target_lang,
                domain=domain,
                model=model,
                api_key=api_key,
            )

    if provider == "openai":
        effective_model = model or PROVIDER_CATALOG["openai"][0]
        return await translate_with_openai(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            domain=domain,
            model=effective_model,
            api_key=api_key,
        )
    if provider == "deepl":
        return await translate_with_deepl(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            api_key=api_key,
        )
    raise ValueError(f"Unsupported translation provider: {provider}")


async def _recover_table_structure_if_needed(
    *,
    source_text: str,
    translated_text: str,
    provider: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    model: str | None,
    api_key: str,
) -> tuple[str, bool, str]:
    prepared_source = _prepare_text_for_table_translation(source_text)
    if not _contains_html_table(prepared_source):
        return translated_text, False, "source_not_table"
    source_shape = _table_shape_signature(prepared_source)
    if _contains_html_table(translated_text):
        translated_shape = _table_shape_signature(translated_text)
        if translated_shape == source_shape:
            return translated_text, False, "already_table_shape_match"
        if not api_key.strip():
            return translated_text, False, "shape_mismatch_missing_api_key"
        recovered = await _translate_preserving_html_table_cells(
            provider=provider,
            source_text=prepared_source,
            source_lang=source_lang,
            target_lang=target_lang,
            domain=domain,
            model=model,
            api_key=api_key,
        )
        if _contains_html_table(recovered):
            recovered_shape = _table_shape_signature(recovered)
            if recovered_shape == source_shape:
                return recovered, True, "shape_mismatch_recovered"
        return translated_text, False, "shape_mismatch_recovery_no_match"
    if not api_key.strip():
        return translated_text, False, "missing_api_key"

    recovered = await _translate_preserving_html_table_cells(
        provider=provider,
        source_text=prepared_source,
        source_lang=source_lang,
        target_lang=target_lang,
        domain=domain,
        model=model,
        api_key=api_key,
    )
    if _contains_html_table(recovered):
        return recovered, True, "recovered"
    return translated_text, False, "recovery_no_table"



def proofread_translation(text: str, strictness: str) -> str:
    """M0 stub for proofreading integration point (replace with your service)."""
    if "<" in text and ">" in text:
        # Keep HTML structure untouched.
        return text.strip()
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()



def run_invariant_checks(source_text: str, output_text: str) -> list[dict[str, Any]]:
    source_numbers = re.findall(r"\d+(?:[\.,]\d+)?", source_text)
    output_numbers = re.findall(r"\d+(?:[\.,]\d+)?", output_text)
    number_ok = all(num in output_numbers for num in source_numbers)
    source_section_refs = re.findall(
        r"(?:\b(?:Section|Article)\s+\d+[A-Za-z0-9\.-]*\b|§\s*\d+[A-Za-z0-9\.-]*)",
        source_text,
        flags=re.IGNORECASE,
    )
    output_section_refs = re.findall(
        r"(?:\b(?:Section|Article)\s+\d+[A-Za-z0-9\.-]*\b|§\s*\d+[A-Za-z0-9\.-]*)",
        output_text,
        flags=re.IGNORECASE,
    )
    section_ref_ok = True
    if source_section_refs:
        source_ref_l = [s.lower() for s in source_section_refs]
        output_ref_l = [s.lower() for s in output_section_refs]
        section_ref_ok = all(ref in output_ref_l for ref in source_ref_l)

    source_numbering_tokens = re.findall(r"(?m)^\s*(?:\(?\d+\)|\d+\.\d+|\d+\.|[A-Za-z]\)|[ivxlcdm]+\.)", source_text, flags=re.IGNORECASE)
    output_numbering_tokens = re.findall(r"(?m)^\s*(?:\(?\d+\)|\d+\.\d+|\d+\.|[A-Za-z]\)|[ivxlcdm]+\.)", output_text, flags=re.IGNORECASE)
    numbering_ok = True
    if source_numbering_tokens:
        numbering_ok = len(output_numbering_tokens) >= len(source_numbering_tokens)

    source_emphasis_count = len(re.findall(r"<\s*(?:b|strong|u)\b", source_text, flags=re.IGNORECASE))
    output_emphasis_count = len(re.findall(r"<\s*(?:b|strong|u)\b", output_text, flags=re.IGNORECASE))
    emphasis_ok = True
    if source_emphasis_count:
        emphasis_ok = output_emphasis_count >= source_emphasis_count

    return [
        {
            "name": "numbers_preserved",
            "status": "pass" if number_ok else "warn",
            "details": {
                "sourceCount": len(source_numbers),
                "outputCount": len(output_numbers),
            },
        },
        {
            "name": "section_refs_preserved",
            "status": "pass" if section_ref_ok else "warn",
            "details": {
                "sourceRefs": len(source_section_refs),
                "outputRefs": len(output_section_refs),
            },
        },
        {
            "name": "numbering_layout_preserved",
            "status": "pass" if numbering_ok else "warn",
            "details": {
                "sourceMarkers": len(source_numbering_tokens),
                "outputMarkers": len(output_numbering_tokens),
            },
        },
        {
            "name": "bold_underline_preserved",
            "status": "pass" if emphasis_ok else "warn",
            "details": {
                "sourceEmphasisTags": source_emphasis_count,
                "outputEmphasisTags": output_emphasis_count,
            },
        },
    ]


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Parse arbiter JSON output; accepts plain JSON or fenced blocks."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    # try direct JSON first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # fallback: grab first {...} object
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Arbiter response did not contain JSON object.")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Arbiter JSON payload is not an object.")
    return parsed


def _normalize_winner(value: Any) -> str | None:
    winner = str(value or "").strip().upper()
    return winner if winner in {"A", "B"} else None


def _normalize_confidence(value: Any, default: float = 0.5) -> float:
    try:
        confidence = float(value)
    except Exception:
        confidence = default
    if confidence > 1:
        confidence = confidence / 100.0
    return max(0.0, min(1.0, confidence))


def _candidate_quality_score(source_text: str, candidate: str, mode: str) -> float:
    text = candidate.strip()
    if not text:
        return -999.0

    mode_normalized = normalize_arbiter_fallback_mode(mode)
    source_numbers = re.findall(r"\d+(?:[\.,]\d+)?", source_text)
    candidate_numbers = re.findall(r"\d+(?:[\.,]\d+)?", text)
    number_preservation = 1.0
    if source_numbers:
        number_preservation = sum(1 for num in source_numbers if num in candidate_numbers) / len(source_numbers)

    source_words = max(1, len(source_text.split()))
    candidate_words = len(text.split())
    length_ratio = candidate_words / source_words
    length_score = max(0.0, 1.0 - min(abs(1 - length_ratio), 1.0))

    source_struct = len(re.findall(r"[:;§\(\)\[\]\"“”]", source_text))
    candidate_struct = len(re.findall(r"[:;§\(\)\[\]\"“”]", text))
    if source_struct > 0:
        struct_score = max(0.0, 1.0 - min(abs(candidate_struct - source_struct) / source_struct, 1.0))
    else:
        struct_score = 1.0

    source_lines = max(1, source_text.count("\n") + 1)
    candidate_lines = max(1, text.count("\n") + 1)
    line_score = max(0.0, 1.0 - min(abs(candidate_lines - source_lines) / source_lines, 1.0))

    source_numbering_tokens = re.findall(
        r"(?m)^\s*(?:\(?\d+\)|\d+\.\d+|\d+\.|[A-Za-z]\)|[ivxlcdm]+\.)",
        source_text,
        flags=re.IGNORECASE,
    )
    candidate_numbering_tokens = re.findall(
        r"(?m)^\s*(?:\(?\d+\)|\d+\.\d+|\d+\.|[A-Za-z]\)|[ivxlcdm]+\.)",
        text,
        flags=re.IGNORECASE,
    )
    numbering_layout_score = 1.0
    if source_numbering_tokens:
        numbering_layout_score = min(1.0, len(candidate_numbering_tokens) / len(source_numbering_tokens))

    source_emphasis_count = len(re.findall(r"<\s*(?:b|strong|u)\b", source_text, flags=re.IGNORECASE))
    candidate_emphasis_count = len(re.findall(r"<\s*(?:b|strong|u)\b", text, flags=re.IGNORECASE))
    emphasis_score = 1.0
    if source_emphasis_count:
        emphasis_score = min(1.0, candidate_emphasis_count / source_emphasis_count)

    lowered = text.lower()
    penalty = 0.0
    if lowered.startswith("[") and " via " in lowered[:80]:
        penalty += 1.2
    if "translation output appears here" in lowered:
        penalty += 2.0
    if re.search(r"\b(i cannot|i can't|sorry|unable to|i won't)\b", lowered):
        penalty += 2.0

    if mode_normalized == "strict_legal":
        return (
            number_preservation * 5.0
            + length_score * 1.8
            + struct_score * 2.6
            + line_score * 1.4
            + numbering_layout_score * 2.2
            + emphasis_score * 1.6
            - penalty * 1.25
        )

    return (
        number_preservation * 3.2
        + length_score * 2.8
        + struct_score * 1.4
        + line_score * 1.4
        + numbering_layout_score * 1.4
        + emphasis_score * 0.8
        - penalty
    )


def _fallback_arbiter_decision(
    *,
    source_text: str,
    candidate_a: str,
    candidate_b: str,
    mode: str,
    reason: str,
) -> dict[str, Any]:
    mode_normalized = normalize_arbiter_fallback_mode(mode)
    score_a = _candidate_quality_score(source_text, candidate_a, mode_normalized)
    score_b = _candidate_quality_score(source_text, candidate_b, mode_normalized)
    winner = "B" if score_b > score_a else "A"
    diff = abs(score_a - score_b)
    confidence = max(0.55, min(0.9, 0.55 + diff * 0.08))
    rationale = (
        "Applied deterministic fallback selection because arbiter response was not parseable. "
        f"Mode={mode_normalized}. ScoreA={score_a:.2f}, ScoreB={score_b:.2f}. Reason: {reason}"
    )
    return {
        "winner": winner,
        "confidence": confidence,
        "rationale": rationale[:420],
        "parseMode": "heuristic_fallback",
        "fallbackUsed": True,
        "fallbackMode": mode_normalized,
    }


def _parse_arbiter_response_relaxed(raw: str) -> dict[str, Any]:
    text = _clean_text(raw)
    if not text:
        raise ValueError("Empty arbiter text response.")

    # Strict JSON mode first.
    try:
        parsed = _extract_json_object(text)
        winner = _normalize_winner(parsed.get("winner"))
        if winner:
            return {
                "winner": winner,
                "confidence": _normalize_confidence(parsed.get("confidence", 0.5)),
                "rationale": str(parsed.get("rationale", "")).strip()[:420],
                "parseMode": "json",
                "fallbackUsed": False,
            }
    except Exception:
        pass

    # Relaxed text extraction fallback.
    winner_match = None
    winner_patterns = [
        r"\bwinner\b[^A-Za-z0-9]{0,20}\b([AB])\b",
        r"\bchoose\b[^A-Za-z0-9]{0,20}\b([AB])\b",
        r"\bcandidate\b[^A-Za-z0-9]{0,20}\b([AB])\b",
        r"\b([AB])\b\s*(?:is|was)?\s*better",
    ]
    for pattern in winner_patterns:
        winner_match = re.search(pattern, text, flags=re.IGNORECASE)
        if winner_match:
            break
    winner = _normalize_winner(winner_match.group(1) if winner_match else None)
    if not winner:
        raise ValueError("Arbiter response did not contain winner A/B.")

    confidence = 0.6
    conf_match = re.search(r"\bconfidence\b[^0-9]{0,20}([0-9]+(?:\.[0-9]+)?)\s*%?", text, flags=re.IGNORECASE)
    if conf_match:
        confidence = _normalize_confidence(conf_match.group(1), default=0.6)
    else:
        percent_match = re.search(r"\b([0-9]{1,3})\s*%", text)
        if percent_match:
            confidence = _normalize_confidence(percent_match.group(1), default=0.6)

    rationale_match = re.search(r"\brationale\b\s*[:\-]\s*(.+)", text, flags=re.IGNORECASE)
    rationale = rationale_match.group(1).strip() if rationale_match else text.splitlines()[0].strip()
    return {
        "winner": winner,
        "confidence": confidence,
        "rationale": rationale[:420],
        "parseMode": "relaxed_text",
        "fallbackUsed": False,
    }


async def run_claude_arbiter(
    *,
    source_text: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    candidate_a: str,
    candidate_b: str,
    fallback_mode: str,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    fallback_mode_normalized = normalize_arbiter_fallback_mode(fallback_mode)
    if not api_key.strip():
        return _fallback_arbiter_decision(
            source_text=source_text,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
            mode=fallback_mode_normalized,
            reason="Missing arbiter API key.",
        )

    system_prompt = (
        "You are a strict legal translation arbiter. Compare two candidate translations (A and B). "
        "Evaluate faithfulness to source meaning, legal terminology precision, completeness, and fluency. "
        "Strongly prioritize format fidelity: preserve numbering/section references, paragraph boundaries, "
        "and emphasis markers (<b>, <strong>, <u>) when present. "
        "Return ONLY valid JSON with keys: winner (A or B), confidence (0 to 1), rationale (short text). "
        "Do not output markdown."
    )
    user_prompt = (
        f"Source language: {source_lang}\n"
        f"Target language: {target_lang}\n"
        f"Domain: {domain}\n\n"
        "Source text:\n"
        f"{source_text}\n\n"
        "Candidate A:\n"
        f"{candidate_a}\n\n"
        "Candidate B:\n"
        f"{candidate_b}\n"
    )

    max_attempts = 2
    attempt_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 320,
                        "temperature": 0,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )
        except Exception as exc:
            attempt_errors.append(f"Attempt {attempt}: HTTP request failed ({exc})")
            if attempt < max_attempts:
                await asyncio.sleep(0.35 * attempt)
                continue
            break

        if resp.status_code != 200:
            body = resp.text[:240]
            attempt_errors.append(f"Attempt {attempt}: Anthropic arbiter HTTP {resp.status_code}: {body}")
            if attempt < max_attempts:
                await asyncio.sleep(0.35 * attempt)
                continue
            break

        payload = resp.json()
        text_blocks = payload.get("content", [])
        arbiter_text = "\n".join(
            block.get("text", "")
            for block in text_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not arbiter_text:
            attempt_errors.append(f"Attempt {attempt}: Anthropic arbiter returned empty text content.")
            if attempt < max_attempts:
                await asyncio.sleep(0.35 * attempt)
                continue
            break

        try:
            parsed = _parse_arbiter_response_relaxed(arbiter_text)
            parsed["attempts"] = attempt
            parsed["fallbackMode"] = fallback_mode_normalized
            return parsed
        except Exception as exc:
            snippet = arbiter_text.replace("\n", " ")[:180]
            attempt_errors.append(f"Attempt {attempt}: Parse failed ({exc}). Raw: {snippet}")
            if attempt < max_attempts:
                await asyncio.sleep(0.35 * attempt)
                continue
            break

    reason = " | ".join(attempt_errors[:3]) if attempt_errors else "Unknown arbiter failure."
    fallback = _fallback_arbiter_decision(
        source_text=source_text,
        candidate_a=candidate_a,
        candidate_b=candidate_b,
        mode=fallback_mode_normalized,
        reason=reason,
    )
    fallback["attempts"] = max_attempts
    return fallback


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "JuriVerto API is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/providers")
async def provider_catalog() -> dict[str, Any]:
    return {
        "defaultPrimary": DEFAULT_PRIMARY_PROVIDER,
        "defaultFallback": DEFAULT_FALLBACK_PROVIDER,
        "arbiterModels": ARBITER_MODEL_CATALOG,
        "arbiterFallbackModes": sorted(ARBITER_FALLBACK_MODES),
        "defaultArbiterFallbackMode": DEFAULT_ARBITER_FALLBACK_MODE,
        "providers": [
            {"id": provider, "models": models}
            for provider, models in PROVIDER_CATALOG.items()
        ],
    }


@app.get("/api/v1/providers/health")
async def providers_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "providers": [
            {"name": DEFAULT_PRIMARY_PROVIDER, "status": "up"},
            {"name": DEFAULT_FALLBACK_PROVIDER, "status": "up"},
        ],
    }


@app.post("/api/v1/keys/validate")
async def validate_key(req: KeyValidationRequest) -> dict[str, Any]:
    provider = req.provider.strip().lower()
    model = req.model.strip()
    if provider == "anthropic":
        model = normalize_arbiter_model(model)
    key = req.apiKey.strip()

    if not key:
        return {"ok": False, "message": "API key is empty.", "provider": provider, "model": model}

    if provider in PROVIDER_CATALOG and model not in PROVIDER_CATALOG[provider]:
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not valid for provider '{provider}'")
    if provider == "anthropic" and model not in ARBITER_MODEL_CATALOG:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not valid for anthropic arbiter. Allowed: {', '.join(ARBITER_MODEL_CATALOG)}",
        )

    if provider == "openai":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if resp.status_code == 200:
                return {"ok": True, "message": "OpenAI key validated successfully.", "provider": provider, "model": model}
            return {
                "ok": False,
                "message": f"OpenAI validation failed (HTTP {resp.status_code}).",
                "provider": provider,
                "model": model,
            }
        except Exception as exc:
            return {"ok": False, "message": f"OpenAI validation error: {exc}", "provider": provider, "model": model}

    if provider == "deepl":
        endpoints = ["https://api-free.deepl.com/v2/usage", "https://api.deepl.com/v2/usage"]
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                for url in endpoints:
                    resp = await client.get(url, headers={"Authorization": f"DeepL-Auth-Key {key}"})
                    if resp.status_code == 200:
                        return {
                            "ok": True,
                            "message": f"DeepL key validated successfully ({url}).",
                            "provider": provider,
                            "model": model,
                        }
            return {"ok": False, "message": "DeepL validation failed for both Free and Pro endpoints.", "provider": provider, "model": model}
        except Exception as exc:
            return {"ok": False, "message": f"DeepL validation error: {exc}", "provider": provider, "model": model}

    if provider == "anthropic":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                    },
                )
            if resp.status_code == 200:
                return {"ok": True, "message": "Anthropic key validated successfully.", "provider": provider, "model": model}
            return {
                "ok": False,
                "message": f"Anthropic validation failed (HTTP {resp.status_code}).",
                "provider": provider,
                "model": model,
            }
        except Exception as exc:
            return {"ok": False, "message": f"Anthropic validation error: {exc}", "provider": provider, "model": model}

    raise HTTPException(status_code=400, detail=f"Unsupported provider for key validation: {provider}")


@app.post("/api/v1/translate")
async def translate(req: TranslateRequest) -> dict[str, Any]:
    started = time.perf_counter()
    trace: list[TraceStep] = []

    text = req.sourceText.strip()
    if not text:
        raise HTTPException(status_code=400, detail="sourceText cannot be empty")
    if req.sourceLang == req.targetLang:
        raise HTTPException(status_code=400, detail="sourceLang and targetLang must differ")
    source_contains_table = _contains_html_table(_prepare_text_for_table_translation(text))

    selected_provider = req.selectedProvider if req.selectedProvider in PROVIDER_CATALOG else DEFAULT_PRIMARY_PROVIDER
    fallback_provider = req.fallbackProvider if req.fallbackProvider in PROVIDER_CATALOG else DEFAULT_FALLBACK_PROVIDER
    selected_model = req.selectedModel or (PROVIDER_CATALOG[selected_provider][0] if PROVIDER_CATALOG[selected_provider] else None)

    if selected_model and selected_model not in PROVIDER_CATALOG[selected_provider]:
        raise HTTPException(status_code=400, detail=f"Model '{selected_model}' is not valid for provider '{selected_provider}'")

    # keys are accepted for request processing but never returned in responses
    provider_keys = req.providerApiKeys or {}
    model_keys = req.modelApiKeys or {}
    primary_key = str(provider_keys.get(selected_provider, "")).strip() or str(model_keys.get(selected_model or "", "")).strip()
    has_primary_key = bool(primary_key)
    arbiter_config = req.arbiter or {}
    arbiter_enabled = bool(arbiter_config.get("enabled", False))

    fallback_model = PROVIDER_CATALOG[fallback_provider][0] if PROVIDER_CATALOG[fallback_provider] else None
    fallback_key = str(provider_keys.get(fallback_provider, "")).strip() or str(model_keys.get(fallback_model or "", "")).strip()
    has_fallback_key = bool(fallback_key)

    # Step 1: primary translation candidate (A)
    t0 = time.perf_counter()
    translated_text = ""
    primary_translation = ""
    fallback_translation = ""
    used_provider = selected_provider
    used_model = selected_model
    fallback_used = False
    try:
        if SIMULATE_PRIMARY_FAILURE:
            raise RuntimeError("simulated primary provider failure")
        if not primary_key:
            raise RuntimeError(f"Missing API key for provider '{selected_provider}'")
        primary_translation = await translate_with_provider(
            provider=selected_provider,
            text=text,
            source_lang=req.sourceLang,
            target_lang=req.targetLang,
            domain=req.domain,
            model=selected_model,
            api_key=primary_key,
        )
        translated_text = primary_translation
        trace.append(
            TraceStep(
                step="translate_primary",
                status="success",
                provider=selected_provider,
                durationMs=int((time.perf_counter() - t0) * 1000),
                message="Primary provider translation completed",
                metadata={
                    "model": selected_model,
                    "apiKeyPresent": has_primary_key,
                    "sourceContainsHtmlTable": source_contains_table,
                    "outputContainsHtmlTable": _contains_html_table(primary_translation),
                },
            )
        )
    except Exception as exc:
        trace.append(
            TraceStep(
                step="translate_primary",
                status="failed",
                provider=selected_provider,
                durationMs=int((time.perf_counter() - t0) * 1000),
                message=str(exc),
                metadata={"model": selected_model, "apiKeyPresent": has_primary_key},
            )
        )

    # Step 2: fallback translation candidate (B)
    should_fetch_fallback = arbiter_enabled or not primary_translation
    if should_fetch_fallback:
        tf = time.perf_counter()
        try:
            if not fallback_key:
                raise RuntimeError(f"Missing API key for provider '{fallback_provider}'")
            fallback_translation = await translate_with_provider(
                provider=fallback_provider,
                text=text,
                source_lang=req.sourceLang,
                target_lang=req.targetLang,
                domain=req.domain,
                model=fallback_model,
                api_key=fallback_key,
            )
            trace.append(
                TraceStep(
                    step="translate_fallback",
                    status="success",
                    provider=fallback_provider,
                    durationMs=int((time.perf_counter() - tf) * 1000),
                    message="Fallback provider translation completed",
                    metadata={
                        "model": fallback_model,
                        "apiKeyPresent": has_fallback_key,
                        "sourceContainsHtmlTable": source_contains_table,
                        "outputContainsHtmlTable": _contains_html_table(fallback_translation),
                    },
                )
            )
        except Exception as exc:
            trace.append(
                TraceStep(
                    step="translate_fallback",
                    status="failed",
                    provider=fallback_provider,
                    durationMs=int((time.perf_counter() - tf) * 1000),
                    message=str(exc),
                    metadata={"model": fallback_model, "apiKeyPresent": has_fallback_key},
                )
            )

    if not primary_translation and not fallback_translation:
        failure_messages = [
            f"{step.step}: {step.message}"
            for step in trace
            if step.step in {"translate_primary", "translate_fallback"} and step.status == "failed" and step.message
        ]
        detail = "Both primary and fallback translation providers failed."
        if failure_messages:
            detail = f"{detail} {' | '.join(failure_messages[:2])}"
        raise HTTPException(status_code=502, detail=detail)

    # If only one translation is available, use it directly.
    if primary_translation and not fallback_translation:
        translated_text = primary_translation
        used_provider = selected_provider
        used_model = selected_model
        fallback_used = False
    elif fallback_translation and not primary_translation:
        translated_text = fallback_translation
        used_provider = fallback_provider
        used_model = fallback_model
        fallback_used = True

    # Step 3: Arbiter (Claude) chooses between A/B when enabled and both candidates exist.
    arbiter_used = False
    arbiter_fallback_used = False
    arbiter_winner = None
    arbiter_confidence = None
    arbiter_fallback_mode = DEFAULT_ARBITER_FALLBACK_MODE
    if arbiter_enabled:
        requested_arbiter_model = normalize_arbiter_model(str(arbiter_config.get("model") or DEFAULT_ARBITER_MODEL))
        arbiter_model = (
            requested_arbiter_model
            if requested_arbiter_model in ARBITER_MODEL_CATALOG
            else DEFAULT_ARBITER_MODEL
        )
        requested_fallback_mode = str(arbiter_config.get("fallbackMode") or DEFAULT_ARBITER_FALLBACK_MODE)
        arbiter_fallback_mode = normalize_arbiter_fallback_mode(requested_fallback_mode)
        arbiter_key = str(arbiter_config.get("apiKey", "")).strip() or str(provider_keys.get("anthropic", "")).strip()
        has_arbiter_key = bool(arbiter_key)
        ta = time.perf_counter()
        if not primary_translation or not fallback_translation:
            trace.append(
                TraceStep(
                    step="arbiter_judge",
                    status="skipped",
                    provider="anthropic",
                    durationMs=int((time.perf_counter() - ta) * 1000),
                    message="Arbiter requires two candidates; skipped.",
                    metadata={
                        "model": arbiter_model,
                        "requestedModel": requested_arbiter_model,
                        "fallbackMode": arbiter_fallback_mode,
                        "requestedFallbackMode": requested_fallback_mode,
                        "apiKeyPresent": has_arbiter_key,
                    },
                )
            )
        elif not arbiter_key:
            trace.append(
                TraceStep(
                    step="arbiter_judge",
                    status="skipped",
                    provider="anthropic",
                    durationMs=int((time.perf_counter() - ta) * 1000),
                    message="Arbiter API key missing; skipped.",
                    metadata={
                        "model": arbiter_model,
                        "requestedModel": requested_arbiter_model,
                        "fallbackMode": arbiter_fallback_mode,
                        "requestedFallbackMode": requested_fallback_mode,
                        "apiKeyPresent": False,
                    },
                )
            )
        else:
            try:
                decision = await run_claude_arbiter(
                    source_text=text,
                    source_lang=req.sourceLang,
                    target_lang=req.targetLang,
                    domain=req.domain,
                    candidate_a=primary_translation,
                    candidate_b=fallback_translation,
                    fallback_mode=arbiter_fallback_mode,
                    model=arbiter_model,
                    api_key=arbiter_key,
                )
                winner = decision["winner"]
                arbiter_winner = winner
                arbiter_confidence = decision["confidence"]
                arbiter_used = True
                arbiter_fallback_used = bool(decision.get("fallbackUsed", False))

                if winner == "B":
                    translated_text = fallback_translation
                    used_provider = fallback_provider
                    used_model = fallback_model
                    fallback_used = True
                else:
                    translated_text = primary_translation
                    used_provider = selected_provider
                    used_model = selected_model
                    fallback_used = False

                trace.append(
                    TraceStep(
                        step="arbiter_judge",
                        status="success",
                        provider="anthropic",
                        durationMs=int((time.perf_counter() - ta) * 1000),
                        message=(
                            "Arbiter selected winning candidate."
                            if not arbiter_fallback_used
                            else "Arbiter fallback logic selected winning candidate."
                        ),
                        metadata={
                            "model": arbiter_model,
                            "requestedModel": requested_arbiter_model,
                            "apiKeyPresent": True,
                            "winner": winner,
                            "confidence": decision["confidence"],
                            "rationale": decision["rationale"],
                            "fallbackUsed": arbiter_fallback_used,
                            "fallbackMode": decision.get("fallbackMode", arbiter_fallback_mode),
                            "requestedFallbackMode": requested_fallback_mode,
                            "parseMode": decision.get("parseMode", "unknown"),
                            "attempts": decision.get("attempts", 1),
                        },
                    )
                )
            except Exception as exc:
                trace.append(
                    TraceStep(
                        step="arbiter_judge",
                        status="failed",
                        provider="anthropic",
                        durationMs=int((time.perf_counter() - ta) * 1000),
                        message=str(exc),
                        metadata={
                            "model": arbiter_model,
                            "requestedModel": requested_arbiter_model,
                            "fallbackMode": arbiter_fallback_mode,
                            "requestedFallbackMode": requested_fallback_mode,
                            "apiKeyPresent": True,
                        },
                    )
                )

    # Step 3b: enforce table-structure output when source is tabular.
    table_repair_started = time.perf_counter()
    try:
        used_key = primary_key if used_provider == selected_provider else fallback_key
        repaired_text, repaired, repair_reason = await _recover_table_structure_if_needed(
            source_text=text,
            translated_text=translated_text,
            provider=used_provider,
            source_lang=req.sourceLang,
            target_lang=req.targetLang,
            domain=req.domain,
            model=used_model,
            api_key=used_key,
        )
        if repaired:
            translated_text = repaired_text
            trace.append(
                TraceStep(
                    step="table_structure_repair",
                    status="success",
                    provider=used_provider,
                    durationMs=int((time.perf_counter() - table_repair_started) * 1000),
                    message="Recovered HTML table structure in final output.",
                    metadata={
                        "reason": repair_reason,
                        "outputContainsHtmlTable": _contains_html_table(translated_text),
                    },
                )
            )
        elif source_contains_table:
            if repair_reason == "already_table_shape_match":
                repair_status = "skipped"
                repair_message = "Table structure already preserved in output."
            elif repair_reason == "shape_mismatch_missing_api_key":
                repair_status = "failed"
                repair_message = "Table structure mismatched source, but recovery could not run because API key was missing."
            elif repair_reason == "shape_mismatch_recovery_no_match":
                repair_status = "failed"
                repair_message = "Table structure mismatched source and recovery did not restore the original row/column shape."
            elif repair_reason == "missing_api_key":
                repair_status = "failed"
                repair_message = "Table structure recovery skipped because API key was missing for the winning provider."
            elif repair_reason == "recovery_no_table":
                repair_status = "failed"
                repair_message = "Table structure recovery ran but provider response still had no HTML table."
            else:
                repair_status = "failed"
                repair_message = f"Table structure recovery did not run ({repair_reason})."
            trace.append(
                TraceStep(
                    step="table_structure_repair",
                    status=repair_status,
                    provider=used_provider,
                    durationMs=int((time.perf_counter() - table_repair_started) * 1000),
                    message=repair_message,
                    metadata={
                        "reason": repair_reason,
                        "outputContainsHtmlTable": _contains_html_table(translated_text),
                    },
                )
            )
    except Exception as exc:
        trace.append(
            TraceStep(
                step="table_structure_repair",
                status="failed",
                provider=used_provider,
                durationMs=int((time.perf_counter() - table_repair_started) * 1000),
                message=f"Table structure recovery failed: {exc}",
            )
        )

    # Step 4: proofreading
    tp = time.perf_counter()
    proofread_text = proofread_translation(translated_text, req.strictness)
    if source_contains_table:
        translated_has_table = _contains_html_table(translated_text)
        proofread_has_table = _contains_html_table(proofread_text)
        if translated_has_table and not proofread_has_table:
            proofread_text = translated_text
            trace.append(
                TraceStep(
                    step="proofread_table_guard",
                    status="success",
                    provider="proofread-module",
                    durationMs=0,
                    message="Proofread output lost HTML table; restored translated table output.",
                    metadata={
                        "translatedContainsHtmlTable": translated_has_table,
                        "proofreadContainsHtmlTable": proofread_has_table,
                    },
                )
            )
        elif not translated_has_table:
            trace.append(
                TraceStep(
                    step="proofread_table_guard",
                    status="failed",
                    provider="proofread-module",
                    durationMs=0,
                    message="Translated output had no HTML table while source was tabular.",
                    metadata={
                        "translatedContainsHtmlTable": translated_has_table,
                        "proofreadContainsHtmlTable": proofread_has_table,
                    },
                )
            )

    trace.append(
        TraceStep(
            step="proofread",
            status="success",
            provider="proofread-module",
            durationMs=int((time.perf_counter() - tp) * 1000),
            message="Constrained proofreading pass completed",
        )
    )

    # Step 5: checks
    tc = time.perf_counter()
    checks = run_invariant_checks(text, proofread_text)
    trace.append(
        TraceStep(
            step="checks",
            status="success",
            provider="validator",
            durationMs=int((time.perf_counter() - tc) * 1000),
            message="Invariant checks completed",
        )
    )

    total_ms = int((time.perf_counter() - started) * 1000)
    table_output_html = _extract_first_table_html(proofread_text) or _extract_first_table_html(translated_text)

    response: dict[str, Any] = {
        "translation": translated_text,
        "proofreadTranslation": proofread_text,
        "finalText": proofread_text,
        "tableOutputHtml": table_output_html,
        "sourceContainsTable": source_contains_table,
        "providerSummary": {
            "primary": selected_provider,
            "selectedModel": selected_model,
            "fallback": fallback_provider,
            "fallbackModel": fallback_model,
            "used": used_provider,
            "usedModel": used_model,
            "fallbackUsed": fallback_used,
            "arbiterEnabled": arbiter_enabled,
            "arbiterUsed": arbiter_used,
            "arbiterFallbackUsed": arbiter_fallback_used,
            "arbiterFallbackMode": arbiter_fallback_mode,
            "arbiterWinner": arbiter_winner,
            "arbiterConfidence": arbiter_confidence,
        },
        "timings": {"totalMs": total_ms},
    }
    if req.debug:
        response["trace"] = [step.model_dump() for step in trace]
        response["checks"] = checks
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)

