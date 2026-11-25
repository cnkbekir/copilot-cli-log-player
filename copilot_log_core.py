"""Copilot CLI oturum logu (events.jsonl) için ortak yardımcılar."""
import json
import os
import re

HOME = os.path.expanduser("~")


def temizle(text):
    """Ev dizinini ~ ile değiştir, satır sonlarını sadeleştir."""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.replace(HOME, "~")
    text = re.sub(r"/var/folders/[^\s\"]+", "<geçici dosya>", text)
    return text


def kisalt(text, limit):
    """Metnin ilk anlamlı satırını alıp limit karaktere kısaltır."""
    text = temizle(text).strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first = lines[0] if lines else ""
    first = re.sub(r"\*\*(.+?)\*\*", r"\1", first)  # markdown kalın
    extra = f" (+{len(lines) - 1} satır)" if len(lines) > 1 else ""
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return first + extra


def sayi(n):
    return f"{n:,}".replace(",", ".")


def oku(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def promptlara_bol(events):
    """Her user.message bir prompt başlatır. Prompt'u tetikleyen hook'lar
    (userPromptSubmitted vb.) user.message'tan önce geldiği için, önceki
    agentStop/sessionEnd sonrasındaki olaylar bir sonraki prompt'a aittir."""
    prompts, current, pending = [], None, []
    for e in events:
        t = e.get("type")
        if t == "user.message":
            current = {"user": e, "events": pending + [e]}
            prompts.append(current)
            pending = []
            continue
        if current is None:
            pending.append(e)
            continue
        current["events"].append(e)
        if t == "hook.end" and e["data"].get("hookType") == "agentStop":
            current = None
    return prompts


TR = {
    "tools": "Araç kullanım talimatları",
    "code_change_instructions": "Kod değiştirme kuralları",
    "environment_limitations": "Ortam kısıtları",
    "file_folder_and_symbol_links": "Link biçimi kuralları",
    "editing_constraints": "Düzenleme kısıtları",
    "autonomy_and_persistence": "Özerklik kuralları",
    "prohibited_actions": "Yasak eylemler",
    "GitHub Copilot Instructions": "copilot-instructions.md",
}


def sistem_bolumleri(content):
    """Sistem prompt'unu üst düzey XML bölümlerine ayırır (iç içe olanları saymadan)."""
    parts, pos = [], 0
    for m in re.finditer(r"^<([a-zA-Z_]+)(?:\s[^>]*)?>", content, re.M):
        if m.start() < pos:
            continue
        tag = m.group(1)
        end = content.find(f"</{tag}>", m.end())
        if end < 0:
            continue
        end += len(tag) + 3
        body = content[m.end():end]
        name = tag
        if tag == "custom_instruction":
            first = body.strip().splitlines()[0].strip("# ").strip() if body.strip() else tag
            name = first
        size = end - m.start()
        sk = re.search(r"<available_skills>.*?</available_skills>", body, re.S)
        if sk:
            n = len(re.findall(r"<skill>", sk.group(0)))
            parts.append((f"Skill listesi ({n} skill: ad + açıklama)", len(sk.group(0))))
            size -= len(sk.group(0))
        parts.append((TR.get(name, name), size))
        pos = end
    covered = sum(c for _, c in parts)
    if len(content) - covered > 0:
        parts.append(("diğer metin", len(content) - covered))
    return parts
