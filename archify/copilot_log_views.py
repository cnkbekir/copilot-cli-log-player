#!/usr/bin/env python3
"""Copilot CLI logundan iki ek Archify görünümü üretir.

  waterfall : bir prompt'un tüm LLM çağrıları, araçları ve hook'ları zaman ekseninde
  tree      : tek bir LLM çağrısının girdisi neyden oluşuyor (token dökümü)

Kullanım:
  python3 copilot_log_views.py <events.jsonl> waterfall --prompt 3 --out <klasör>
  python3 copilot_log_views.py <events.jsonl> tree --prompt 3 --call 2 --out <klasör>
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from copilot_log_core import oku, promptlara_bol, kisalt, sayi, temizle, sistem_bolumleri, TR  # noqa: E402


def ts(e):
    return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))


# ── Waterfall ───────────────────────────────────────────────────────────────

def waterfall(events, prompt_no, out_rel):
    p = promptlara_bol(events)[prompt_no - 1]
    evs = p["events"]
    # oturum yeniden açıldıysa öncesini at (sessionEnd vb. bu prompt'a ait değil)
    rs = [i for i, e in enumerate(evs) if e["type"] in ("session.start", "session.resume")]
    if rs:
        evs = evs[rs[-1]:]
    evs = [e for e in evs if not (e["type"] == "hook.start" and e["data"].get("hookType") == "sessionEnd")]
    t0 = ts(evs[0])
    ms = lambda e: round((ts(e) - t0).total_seconds() * 1000)
    spans = []
    hook_end = {e["data"]["hookInvocationId"]: e for e in evs if e["type"] == "hook.end"}
    content = p["user"]["data"].get("content", "")
    end_all = ms(evs[-1])
    spans.append({"id": "prompt", "name": f"Prompt: “{kisalt(content, 40)}”", "service": "Geliştirici",
                  "start": 0, "end": max(end_all, 1), "detail": f"Tam metin: {kisalt(content, 300)}"})

    def hook_span(e, parent):
        d = e["data"]
        he = hook_end.get(d["hookInvocationId"])
        s, en = ms(e), ms(he) if he else ms(e)
        out = ((he or {}).get("data") or {}).get("output") or {}
        det = "Deterministik komut, LLM değil."
        if out.get("additionalContext"):
            det += " Bağlama ekledi: " + kisalt(out["additionalContext"], 200)
        spans.append({"id": f"h{len(spans)}", "name": f"hook: {d['hookType']}", "service": "Hooks",
                      "start": s, "end": max(en, s), "parent": parent, "detail": det,
                      "status": "ok" if ((he or {}).get("data") or {}).get("success", True) else "error"})

    turn_starts = [e for e in evs if e["type"] == "assistant.turn_start"]
    # prompt öncesi hook'lar
    first_turn = ms(turn_starts[0]) if turn_starts else end_all
    for e in evs:
        if e["type"] == "hook.start" and ms(e) <= first_turn and e["data"]["hookType"] not in ("preToolUse", "postToolUse", "preMcpToolCall"):
            hook_span(e, "prompt")

    usage = [e for e in evs if e["type"] == "session.usage_record"]
    amsgs = [e for e in evs if e["type"] == "assistant.message"]
    turn_ends = [e for e in evs if e["type"] == "assistant.turn_end"]
    starts = {e["data"]["toolCallId"]: e for e in evs if e["type"] == "tool.execution_start"}
    comps = {e["data"]["toolCallId"]: e for e in evs if e["type"] == "tool.execution_complete"}
    for i, tsr in enumerate(turn_starts):
        tid = f"t{i + 1}"
        te = turn_ends[i] if i < len(turn_ends) else evs[-1]
        am = amsgs[i] if i < len(amsgs) else te
        u = usage[i]["data"]["usage"] if i < len(usage) else {}
        reqs = am["data"].get("toolRequests") or [] if am is not te else []
        spans.append({"id": tid, "name": f"Tur {i + 1}", "service": "Copilot CLI",
                      "start": ms(tsr), "end": max(ms(te), ms(tsr)), "parent": "prompt",
                      "detail": "Bir tur = bir LLM çağrısı + (varsa) istediği araçlar."})
        reasoning = am["data"].get("reasoningText") or ""
        what = ("tool_call: " + ", ".join(r["name"] for r in reqs)) if reqs else "son yanıt: " + kisalt(am["data"].get("content", ""), 120)
        spans.append({"id": f"llm{i + 1}", "name": f"LLM · {sayi(u.get('inputTokens', 0))} token girdi",
                      "service": "LLM", "start": ms(tsr), "end": max(ms(am), ms(tsr)), "parent": tid,
                      "emphasis": True,
                      "detail": f"{sayi(u.get('inputTokens', 0))} token girdi ({sayi(u.get('cacheReadTokens', 0))} önbellekten), "
                                f"{sayi(u.get('outputTokens', 0))} token çıktı. Karar → {what}."
                                + (f" Düşünce: {kisalt(reasoning, 160)}" if reasoning else "")})
        for r in reqs:
            cid = r["toolCallId"]
            s, c = starts.get(cid), comps.get(cid)
            if not s:
                continue
            d = s["data"]
            svc = f"MCP · {d['mcpServerName']}" if d.get("mcpServerName") else (
                "VS Code" if any(x["type"] == "external_tool.requested" and x["data"].get("toolCallId") == cid for x in evs) else "Yerleşik araç")
            res = ((c or {}).get("data", {}).get("result") or {}).get("content", "")
            args = r.get("arguments")
            args_s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            sid = f"x{len(spans)}"
            spans.append({"id": sid, "name": f"{d.get('mcpToolName') or r['name']}", "service": svc,
                          "start": ms(s), "end": max(ms(c) if c else ms(s), ms(s)), "parent": tid,
                          "status": "ok" if (c and c["data"].get("success")) else "error",
                          "detail": f"Argüman: {kisalt(args_s, 140)} → Sonuç: {kisalt(res, 160)}"})
            for e in evs:
                if e["type"] == "hook.start" and e["data"]["hookType"] in ("preToolUse", "postToolUse", "preMcpToolCall"):
                    inp = e["data"].get("input", {})
                    hid = inp.get("toolCallId") or next((x.get("id") for x in inp.get("toolCalls", [])), None)
                    if hid == cid or (hid is None and d.get("mcpServerName") and ms(s) <= ms(e) <= ms(c or s)):
                        hook_span(e, sid)
    for e in evs:
        if e["type"] == "hook.start" and e["data"]["hookType"] == "agentStop":
            hook_span(e, "prompt")

    llm_ms = sum(sp["end"] - sp["start"] for sp in spans if sp["service"] == "LLM")
    tool_ms = sum(sp["end"] - sp["start"] for sp in spans if sp.get("parent", "").startswith("t") and sp["service"] not in ("LLM",))
    return {
        "schema_version": 1,
        "diagram_type": "waterfall",
        "meta": {
            "title": f"Prompt {prompt_no} zaman çizelgesi: LLM mi, araç mı?",
            "locale": "tr",
            "output": out_rel,
            "evidence": "measured",
            "unit": "ms",
            "quality_profile": "showcase",
        },
        "spans": spans,
        "cards": [
            {"dot": "violet", "title": "Süre nereye gitti?", "items": [
                f"Toplam: {sayi(end_all)} ms",
                f"LLM'in düşünmesi: {sayi(llm_ms)} ms ({round(100 * llm_ms / max(end_all, 1))}%)",
                f"Araçlar + hook'lar: {sayi(tool_ms)} ms",
            ]},
            {"dot": "cyan", "title": "Nasıl okunur?", "items": [
                "Her satır logdaki bir olay; çubuk uzunluğu gerçek süre",
                "Tur = 1 LLM çağrısı + istediği araçlar",
                "Bir satıra tıklayınca token, argüman ve sonuç görünür",
            ]},
        ],
    }


# ── Tree: bir LLM çağrısının girdisi ────────────────────────────────────────


def tree(events, prompt_no, call_no, out_rel):
    prompts = promptlara_bol(events)
    p = prompts[prompt_no - 1]
    all_usage = [e for e in events if e["type"] == "session.usage_record"]
    p_usage = [e for e in p["events"] if e["type"] == "session.usage_record"]
    target = p_usage[call_no - 1]
    u = target["data"]["usage"]
    tin, tools_tok = u["inputTokens"], u.get("toolTokenCount", 0)

    sm = next(e for e in events if e["type"] == "system.message")["data"]["content"]
    # Sistem prompt'unun token karşılığı: ilk çağrıda (geçmiş yokken) araç dışı girdi
    first = all_usage[0]["data"]["usage"]
    first_user = next(e for e in events if e["type"] == "user.message")["data"].get("transformedContent", "")
    sys_tok = first["inputTokens"] - first.get("toolTokenCount", 0)
    ratio = (len(sm) + len(first_user)) / max(sys_tok, 1)  # karakter / token
    sys_tok = round(len(sm) / ratio)

    # Bu çağrıya kadar konuşmaya girenler
    cutoff = events.index(target)
    items = []  # (grup, ad, karakter)
    for pi, pr in enumerate(prompts, 1):
        grp = "Bu prompt" if pi == prompt_no else f"Önceki prompt {pi}"
        for e in pr["events"]:
            if events.index(e) >= cutoff:
                break
            d = e["data"]
            if e["type"] == "user.message":
                items.append((grp, f"prompt: “{kisalt(d.get('content', ''), 18)}”", len(d.get("transformedContent", ""))))
            elif e["type"] == "assistant.message":
                for r in d.get("toolRequests") or []:
                    a = r.get("arguments")
                    items.append((grp, f"tool_call: {r['name']}", len(a if isinstance(a, str) else json.dumps(a))))
                if d.get("content"):
                    items.append((grp, "LLM yanıt metni", len(d["content"])))
            elif e["type"] == "tool.execution_complete":
                name = next((x["data"]["toolName"] for x in pr["events"] if x["type"] == "tool.execution_start" and x["data"]["toolCallId"] == d["toolCallId"]), "araç")
                items.append((grp, f"sonuç: {name}", len((d.get("result") or {}).get("content", ""))))
            elif e["type"] == "skill.invoked":
                items.append((grp, f"skill içeriği: {d.get('name')}", len(d.get("content", ""))))
    conv_tok = max(tin - tools_tok - sys_tok, 0)
    conv_chars = sum(c for *_, c in items) or 1
    est = lambda c: round(conv_tok * c / conv_chars)

    nodes = [{"id": "root", "label": f"LLM çağrısı {call_no} girdisi",
              "sublabel": f"{sayi(tin)} token (ölçülen)"}]
    nodes.append({"id": "tools", "parent": "root", "label": "Araç tanımları",
                  "sublabel": f"{sayi(tools_tok)} token · {u.get('availableToolCount', '?')} araç (ölçülen)"})
    nodes.append({"id": "sys", "parent": "root", "label": "Sistem prompt'u",
                  "sublabel": f"≈{sayi(sys_tok)} token · her çağrıda aynı"})
    for i, (name, chars) in enumerate(sorted(sistem_bolumleri(sm), key=lambda x: -x[1])[:8]):
        nodes.append({"id": f"s{i}", "parent": "sys", "label": name[:40], "sublabel": f"≈{sayi(round(chars / ratio))} token"})
    nodes.append({"id": "conv", "parent": "root", "label": "Konuşma geçmişi",
                  "sublabel": f"≈{sayi(conv_tok)} token · her turda büyür", "emphasis": True})
    groups = {}
    for grp, name, chars in items:
        groups.setdefault(grp, []).append((name, chars))
    for gi, (grp, lst) in enumerate(groups.items()):
        gid = f"g{gi}"
        nodes.append({"id": gid, "parent": "conv", "label": grp, "sublabel": f"≈{sayi(est(sum(c for _, c in lst)))} token",
                      "emphasis": grp == "Bu prompt"})
        merged = {}
        for name, chars in lst:
            merged[name] = merged.get(name, 0) + chars
        for ii, (name, chars) in enumerate(sorted(merged.items(), key=lambda x: -x[1])[:6]):
            nodes.append({"id": f"{gid}i{ii}", "parent": gid, "label": name[:36], "sublabel": f"≈{sayi(est(chars))} token"})

    user_now = next((c for g, n, c in items if g == "Bu prompt" and n.startswith("prompt:")), 0)
    return {
        "schema_version": 1,
        "diagram_type": "tree",
        "meta": {"title": f"Prompt {prompt_no} · LLM çağrısı {call_no}: {sayi(tin)} token neyden oluşuyor?",
                 "locale": "tr", "output": out_rel, "quality_profile": "showcase"},
        "layout": {"direction": "right"},
        "nodes": nodes,
        "cards": [
            {"dot": "violet", "title": "Ölçülen ve tahmin edilen", "items": [
                f"Toplam girdi ({sayi(tin)}) ve araç tanımları ({sayi(tools_tok)}) logda ölçülmüş",
                "Sistem prompt'u: ilk çağrıdaki araç dışı girdiden hesaplandı",
                f"Alt kırılımlar karakter oranıyla tahmin (≈1 token / {ratio:.1f} karakter)",
            ]},
            {"dot": "amber", "title": "Esas soru ne kadar yer kaplıyor?", "items": [
                f"Yazdığınız prompt ≈{sayi(round(user_now / ratio))} token",
                f"Bu, girdinin %{100 * user_now / ratio / tin:.2f}'i",
                f"Önbellekten okunan: {sayi(u.get('cacheReadTokens', 0))} token",
            ]},
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events")
    ap.add_argument("view", choices=["waterfall", "tree"])
    ap.add_argument("--prompt", type=int, required=True)
    ap.add_argument("--call", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name")
    a = ap.parse_args()
    events = oku(a.events)
    os.makedirs(a.out, exist_ok=True)
    name = a.name or (f"p{a.prompt}-waterfall" if a.view == "waterfall" else f"p{a.prompt}-call{a.call}-tree")
    doc = waterfall(events, a.prompt, f"{a.out}/{name}.html") if a.view == "waterfall" else tree(events, a.prompt, a.call, f"{a.out}/{name}.html")
    with open(f"{a.out}/{name}.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"{a.out}/{name}.json")


if __name__ == "__main__":
    main()
