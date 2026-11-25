#!/usr/bin/env python3
"""GitHub Copilot CLI oturum logunu (events.jsonl) Archify Sequence diyagramına çevirir.

Kullanım:
  python3 copilot_log_to_archify.py <events.jsonl> --list
  python3 copilot_log_to_archify.py <events.jsonl> --prompt 3 --out <klasör>

Çıktı: <klasör>/candidate.json (Archify sequence) ve <klasör>/story-brief.json.
Ardından:
  node <archify>/bin/archify.mjs finalize sequence <klasör>/candidate.json <klasör>/<ad>.html --quality showcase --json
  node <archify>/bin/archify.mjs story attach <klasör>/<ad>.html <klasör>/story-brief.json <klasör>/<ad>-story.html --json

Her şey yerelde çalışır; log hiçbir yere gönderilmez.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from copilot_log_core import HOME, temizle, kisalt, sayi, oku, promptlara_bol  # noqa: E402


# ── Özetleme kuralları ──────────────────────────────────────────────────────
# Diyagramda logdan ne kadarının görüneceğini bu iki fonksiyon belirler.
# Not satırı (note) diyagramın üzerinde görünür; payload ise bir mesaja
# tıklayınca açılan paneldedir.


def sonucu_ozetle(tool_name, result_text):
    """Araç sonucunun diyagramda görünecek kısa hali (not satırı)."""
    return kisalt(result_text, 70)


# ── Log okuma ───────────────────────────────────────────────────────────────


def sistem_baglami(events):
    """Sistem prompt'unun bölümlerini ve boyutlarını çıkarır (içeriği değil)."""
    sm = next((e for e in events if e["type"] == "system.message"), None)
    if not sm:
        return None
    c = sm["data"].get("content", "")
    info = {"toplam": len(c), "instructions": [], "skills": 0, "skills_chars": 0}
    for m in re.finditer(r"<custom_instruction[^>]*>\s*(.+?)\n", c):
        end = c.find("</custom_instruction>", m.start())
        info["instructions"].append((m.group(1).strip("# ").strip(), end - m.start()))
    m = re.search(r"<available_skills>(.*?)</available_skills>", c, re.S)
    if m:
        info["skills"] = len(re.findall(r"<skill>", m.group(1)))
        info["skills_chars"] = len(m.group(0))
    m = re.search(r"<tools>(.*?)</tools>", c, re.S)
    info["tools_chars"] = len(m.group(0)) if m else 0
    return info


# ── Diyagram üretimi ───────────────────────────────────────────────────────

class Builder:
    STEP = 34       # notsuz mesaj aralığı
    STEP_NOTE = 80  # notlu mesaj aralığı (3 satırlık not payı)
    NOTE_MAX = 44   # nottaki en fazla karakter

    def __init__(self):
        self.y = 180
        self.messages = []
        self.segments = []
        self.used = set()
        self.story = []  # (sahne başlığı, [noktalar])

    def msg(self, frm, to, label, variant="default", note=None, payload=None, caption=None, title=None, text=None):
        mid = f"m{len(self.messages) + 1}"
        m = {"id": mid, "from": frm, "to": to, "y": self.y, "label": label, "variant": variant}
        if note:
            m["note"] = note if len(note) <= self.NOTE_MAX else note[: self.NOTE_MAX - 1].rstrip() + "…"
        if payload:
            m["payload"] = {"summary": temizle(payload)[:240]}
        self.messages.append(m)
        self.used.update([frm, to])
        if self.story:
            self.story[-1][1].append({
                "anchor": to, "via": mid,
                "title": title or label,
                "text": text or (note or label),
                "caption": caption or label,
            })
        self.y += self.STEP_NOTE if note else self.STEP
        return mid

    def sahne(self, title):
        self.story.append((title, []))

    def segment_start(self):
        return self.y - 14

    def segment_end(self, start, label):
        end = self.y - 18
        if end - start > 20:
            self.segments.append({"from": start, "to": end, "label": label})


def uret(events, prompt_no, out_rel):
    prompts = promptlara_bol(events)
    if not 1 <= prompt_no <= len(prompts):
        sys.exit(f"Prompt {prompt_no} yok; bu logda {len(prompts)} prompt var.")
    p = prompts[prompt_no - 1]
    evs = p["events"]
    user = p["user"]["data"]
    ctx = sistem_baglami(events)
    b = Builder()

    model_name = "LLM"
    cwd = ""
    for e in events:
        if e["type"] in ("session.start", "session.resume"):
            cwd = (e["data"].get("context") or {}).get("cwd", cwd)
    for e in events:
        if e["type"] == "session.auto_mode_resolved":
            model_name = e["data"].get("chosenModel", model_name)

    exec_info = {}  # toolCallId -> tool.execution_start data
    for e in evs:
        if e["type"] == "tool.execution_start":
            exec_info[e["data"]["toolCallId"]] = e["data"]

    def hedef(tool_call_id, tool_name):
        d = exec_info.get(tool_call_id, {})
        if d.get("mcpServerName"):
            return "mcp"
        if any(x["type"] == "external_tool.requested" and x["data"].get("toolCallId") == tool_call_id for x in evs):
            return "host"
        return "tools"

    mcp_server = next((d.get("mcpServerName") + " · " + d.get("mcpTransport", "") for d in exec_info.values() if d.get("mcpServerName")), "")

    # 1) İstek ve prompt öncesi hook'lar
    b.sahne("İstek gelir")
    seg = b.segment_start()
    content = user.get("content", "")
    transformed = user.get("transformedContent", "")
    eklenen = transformed.replace(content, "").strip()
    eklenen_tags = re.findall(r"<([a-z_]+)>", eklenen)
    b.msg("user", "cli", "prompt", "emphasis",
          note=kisalt(content, 60),
          payload=content,
          caption=f"Geliştirici yazıyor: “{kisalt(content, 80)}”",
          title="Kullanıcının prompt'u",
          text=f"Yazılan metin: “{kisalt(content, 200)}”.")
    for e in evs:
        if e["type"] == "hook.start" and e["data"]["hookType"] in ("sessionStart", "userPromptSubmitted", "userPromptTransformed"):
            ht = e["data"]["hookType"]
            end = next((x for x in evs if x["type"] == "hook.end" and x["data"].get("hookInvocationId") == e["data"]["hookInvocationId"]), None)
            out = (end or {}).get("data", {}).get("output") or {}
            add = out.get("additionalContext")
            b.msg("cli", "hooks", ht, "security",
                  caption=f"CLI, {ht} hook'unu çalıştırır.",
                  title=f"Hook: {ht}",
                  text="Sizin tanımladığınız deterministik bir komut; LLM değildir." + (" Bağlama ek bilgi döndürdü." if add else " Bir şey döndürmedi."))
            if add:
                b.msg("hooks", "cli", "additionalContext", "return",
                      note=kisalt(add, 60), payload=add,
                      caption="Hook, LLM'e gidecek mesaja bir hatırlatma ekler.",
                      title="Hook bağlam ekledi",
                      text=f"Eklenen metin: “{kisalt(add, 200)}”. Model bunu sizin mesajınızın bir parçası olarak görecek.")
    b.segment_end(seg, "İstek + hook'lar")

    # 2) LLM turları
    usage = [e for e in evs if e["type"] == "session.usage_record"]
    amsgs = [e for e in evs if e["type"] == "assistant.message"]
    completes = {e["data"]["toolCallId"]: e["data"] for e in evs if e["type"] == "tool.execution_complete"}
    skills = [e["data"] for e in evs if e["type"] == "skill.invoked"]
    hooks_by_call = {}
    for e in evs:
        if e["type"] == "hook.start" and e["data"]["hookType"] in ("preToolUse", "postToolUse", "preMcpToolCall"):
            inp = e["data"].get("input", {})
            cid = inp.get("toolCallId") or next((c.get("id") for c in inp.get("toolCalls", [])), None)
            hooks_by_call.setdefault(cid, []).append(e["data"]["hookType"])
    # preMcpToolCall girdisinde toolCallId olmayabilir: sırasına göre MCP çağrısına bağla
    mcp_calls = [cid for cid, d in exec_info.items() if d.get("mcpServerName")]
    if None in hooks_by_call:
        for cid in mcp_calls:
            if "preMcpToolCall" not in hooks_by_call.get(cid, []):
                hooks_by_call.setdefault(cid, []).insert(1, "preMcpToolCall")
        hooks_by_call.pop(None)

    prev_in = None
    for i, am in enumerate(amsgs):
        d = am["data"]
        u = usage[i]["data"]["usage"] if i < len(usage) else {}
        tin, cache = u.get("inputTokens", 0), u.get("cacheReadTokens", 0)
        delta = f" (+{sayi(tin - prev_in)})" if prev_in is not None and tin - prev_in > 300 else ""
        prev_in = tin
        b.sahne(f"LLM çağrısı {i + 1}")
        seg = b.segment_start()
        b.msg("cli", "llm", f"çağrı {i + 1}: {sayi(tin)} token", "emphasis",
              note=f"{sayi(cache)} önbellekten{delta}" if cache or delta else None,
              caption=f"CLI tüm bağlamı ({sayi(tin)} token) yeniden LLM'e gönderir.",
              title=f"LLM çağrısı {i + 1}",
              text=f"Girdi {sayi(tin)} token; bunun {sayi(cache)} kadarı önbellekten okundu." + (f" Önceki çağrıya göre{delta.strip(' ()')} token arttı: araya giren sonuçlar bağlama eklendi." if delta else "") + " LLM'in hafızası yok; her çağrıda bağlam baştan gönderilir.")
        reasoning = d.get("reasoningText") or ""
        reqs = d.get("toolRequests") or []
        if reqs:
            for r in reqs:
                args = r.get("arguments")
                args_s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                b.msg("llm", "cli", f"tool_call: {r['name']}", "return",
                      note=(kisalt(reasoning, 60) if reasoning else kisalt(args_s, 60)),
                      payload=f"argümanlar: {args_s}" + (f" | düşünce: {reasoning}" if reasoning else ""),
                      caption=f"LLM bir araç çağırmak istiyor: {r['name']}.",
                      title=f"LLM'in kararı: {r['name']}",
                      text=(f"Modelin düşünce özeti: “{kisalt(reasoning, 160)}”. " if reasoning else "") + f"Argümanlar: {kisalt(args_s, 160)}. LLM aracı kendisi çalıştıramaz; yalnızca istek döndürür.")
                cid = r["toolCallId"]
                for hk in hooks_by_call.get(cid, []):
                    if hk == "postToolUse":
                        continue
                    b.msg("cli", "hooks", hk, "security",
                          caption=f"Araç çalışmadan önce {hk} hook'u çalışır.",
                          title=f"Kapı: {hk}",
                          text="Hook aracı engelleyebilir ya da izin verebilir. Burada izin verdi." if hk == "preToolUse" else "MCP çağrılarına özel ek kapı.")
                tgt = hedef(cid, r["name"])
                ex = exec_info.get(cid, {})
                tool_label = ex.get("mcpToolName") or r["name"]
                b.msg("cli", tgt, tool_label, "default",
                      note=kisalt(args_s, 60) if args_s not in ("{}", "") else None,
                      payload=args_s,
                      caption={"mcp": f"İstek MCP sunucusuna ({mcp_server}) gider.",
                               "host": "Araç VS Code tarafında çalışır (external_tool).",
                               "tools": "CLI aracı yerelde kendisi çalıştırır."}[tgt],
                      title=f"Araç çalışıyor: {tool_label}",
                      text={"mcp": f"MCP sunucusu Copilot'tan ayrı bir süreçtir. Sunucu: {mcp_server}.",
                            "host": "Bu araç CLI'ın içinde değil, onu barındıran VS Code tarafında tanımlı.",
                            "tools": "Yerleşik araç; CLI süreci içinde çalışır."}[tgt])
                comp = completes.get(cid, {})
                res = (comp.get("result") or {}).get("content", "")
                ok = comp.get("success")
                b.msg(tgt, "cli", "sonuç" if ok else "hata", "return",
                      note=sonucu_ozetle(r["name"], res), payload=res,
                      caption="Araç sonucu CLI'a döner; bir sonraki LLM çağrısında bağlama girecek.",
                      title="Aracın döndürdüğü",
                      text=f"“{kisalt(res, 220)}”")
                if r["name"] == "skill":
                    sk = next((s for s in skills), None)
                    if sk:
                        spath = sk.get("path", "")
                        rel = os.path.relpath(spath, cwd) if cwd and spath.startswith(cwd) else temizle(spath)
                        b.msg("cli", "skill", "SKILL.md oku", "default",
                              note=rel[-60:],
                              caption="CLI skill dosyasını okur.",
                              title=f"Skill: {sk.get('name')}",
                              text=f"Dosya: {temizle(sk.get('path', ''))}. Şimdiye kadar bağlamda bu skill'in yalnızca adı ve açıklaması vardı.")
                        b.msg("skill", "cli", "<skill-context>", "return",
                              note=f"{sayi(len(sk.get('content', '')))} karakter bağlama eklenir",
                              payload=sk.get("content", "")[:240],
                              caption="Skill'in tam talimatları bir sonraki çağrıda LLM'e gidecek.",
                              title="Skill içeriği bağlama girer",
                              text="Kademeli yükleme: gövde yalnızca model skill'i seçtiğinde eklenir.")
                if "postToolUse" in hooks_by_call.get(cid, []):
                    b.msg("cli", "hooks", "postToolUse", "security",
                          caption="Araç bittikten sonra postToolUse hook'u çalışır.",
                          title="Kapı: postToolUse",
                          text="Sonucu inceleyip kaydedebilir veya engelleyebilir.")
        else:
            text = d.get("content", "")
            b.msg("llm", "cli", "yanıt (stop)", "return",
                  note=kisalt(text, 60), payload=text,
                  caption="LLM araç istemeden metinle bitirir; döngü durur.",
                  title="Son yanıt",
                  text=f"“{kisalt(text, 220)}”")
        b.segment_end(seg, f"Çağrı {i + 1}")

    if amsgs and not (amsgs[-1]["data"].get("toolRequests")):
        b.sahne("Yanıt")
        b.msg("cli", "user", "yanıt", "return",
              caption="Yanıt geliştiriciye gösterilir.",
              title="Kullanıcı yanıtı görür",
              text=f"Bu prompt için {len(amsgs)} LLM çağrısı ve {len(completes)} araç çağrısı yapıldı.")

    # Katılımcılar (yalnızca kullanılanlar)
    all_p = [
        ("user", "frontend", "Geliştirici", "siz"),
        ("cli", "backend", "Copilot CLI", "LLM değil"),
        ("hooks", "security", "Hooks", "komutlar"),
        ("llm", "cloud", "LLM", model_name),
        ("tools", "messagebus", "Araçlar", "yerleşik"),
        ("skill", "database", "SKILL.md", "dosya"),
        ("host", "external", "VS Code", "external_tool"),
        ("mcp", "external", "MCP", mcp_server or "sunucu"),
    ]
    participants = [{"id": i, "type": t, "label": l, "sublabel": s} for i, t, l, s in all_p if i in b.used]

    cards = []
    if ctx:
        items = [f"Sistem prompt'u: {sayi(ctx['toplam'])} karakter (her çağrıda gider)"]
        items += [f"{n}: {sayi(c)} karakter" for n, c in ctx["instructions"]]
        if ctx["skills"]:
            items.append(f"{ctx['skills']} skill'in ad + açıklaması: {sayi(ctx['skills_chars'])} karakter")
        u0 = usage[0]["data"]["usage"] if usage else {}
        if u0.get("availableToolCount"):
            items.append(f"{u0['availableToolCount']} araç tanımı: {sayi(u0.get('toolTokenCount', 0))} token")
        cards.append({"dot": "violet", "title": "Her çağrıda bağlama girenler", "items": items})
    if eklenen_tags:
        cards.append({"dot": "amber", "title": "Prompt'unuza eklenenler", "items": [f"<{t}>" for t in dict.fromkeys(eklenen_tags)] + ["(transformedContent: LLM'in gördüğü hali)"]})
    cards.append({"dot": "cyan", "title": "Bu prompt'un özeti", "items": [
        f"{len(amsgs)} LLM çağrısı, {len(completes)} araç çağrısı",
        f"Girdi token: {sayi(usage[0]['data']['usage']['inputTokens'])} → {sayi(usage[-1]['data']['usage']['inputTokens'])}" if usage else "token bilgisi yok",
        f"Çıktı token toplamı: {sayi(sum(x['data']['usage'].get('outputTokens', 0) for x in usage))}",
    ]})

    title_txt = kisalt(content, 40)
    candidate = {
        "schema_version": 1,
        "diagram_type": "sequence",
        "meta": {
            "title": f"Copilot CLI · prompt {prompt_no}: “{title_txt}”",
            "locale": "tr",
            "output": out_rel,
            "quality_profile": "showcase",
            "legend": {"entries": {
                "emphasis": {"label": "Ana istek / LLM çağrısı"},
                "default": {"label": "Araç çağrısı"},
                "security": {"label": "Hook (kapı)"},
                "return": {"label": "Dönen yanıt / sonuç"},
            }},
        },
        "participants": participants,
        "segments": b.segments,
        "messages": b.messages,
        "cards": cards,
    }
    brief = {
        "version": 2,
        "title": f"Copilot CLI: “{title_txt}”",
        "intro": "Bu anlatım gerçek bir Copilot CLI oturumunun logundan üretildi. Her ok logdaki bir olaya karşılık gelir.",
        "outro": f"Toplam {len(amsgs)} LLM çağrısı ve {len(completes)} araç çağrısı. Kararları LLM verdi; çalıştıran her zaman LLM dışındaki koddu.",
        "scenes": [{"title": t, "points": pts} for t, pts in b.story if pts],
    }
    return candidate, brief


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events")
    ap.add_argument("--list", action="store_true", help="prompt'ları listele")
    ap.add_argument("--prompt", type=int)
    ap.add_argument("--out", help="çıktı klasörü (çalışma dizinine göre)")
    ap.add_argument("--name", default="copilot-prompt")
    a = ap.parse_args()
    events = oku(a.events)
    prompts = promptlara_bol(events)
    if a.list or not a.prompt:
        for i, p in enumerate(prompts, 1):
            n_llm = sum(1 for e in p["events"] if e["type"] == "assistant.message")
            n_tool = sum(1 for e in p["events"] if e["type"] == "tool.execution_start")
            print(f"{i}: “{kisalt(p['user']['data'].get('content', ''), 70)}” · {n_llm} LLM çağrısı, {n_tool} araç")
        return
    out = a.out or f".archify/sequence-{a.name}-{a.prompt}"
    os.makedirs(out, exist_ok=True)
    cand, brief = uret(events, a.prompt, f"{out}/{a.name}.html")
    with open(f"{out}/candidate.json", "w", encoding="utf-8") as f:
        json.dump(cand, f, ensure_ascii=False, indent=2)
    with open(f"{out}/story-brief.json", "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)
    print(f"{out}/candidate.json · {len(cand['messages'])} mesaj, {len(cand['participants'])} katılımcı, {len(brief['scenes'])} sahne")


if __name__ == "__main__":
    main()
