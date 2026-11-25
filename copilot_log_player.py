#!/usr/bin/env python3
"""Copilot CLI oturum logundan (events.jsonl) adım adım ilerleyen bir eğitim oynatıcısı üretir.

Kullanım:
  python3 copilot_log_player.py <events.jsonl> -o oynatici.html [--baslik "Eğitim"]

Tek dosyalık HTML üretir; içinde oturumdaki tüm prompt'lar vardır.
←/→ adım, 1–5 seviye, P prompt değiştirir. Her şey yerelde çalışır.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from copilot_log_core import oku, promptlara_bol, temizle, kisalt, sistem_bolumleri  # noqa: E402

MAX_TEXT = 4000  # alt panelde gösterilecek en uzun metin


def kes(text, n=MAX_TEXT):
    text = temizle(text or "")
    return text if len(text) <= n else text[:n] + f"\n… (+{len(text) - n:,} karakter kesildi)".replace(",", ".")


def args_str(a):
    return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False, indent=2)


KREDI_TUR = {"input": "Önbelleksiz girdi", "cache_read": "Önbellekten okuma",
             "cache_write": "Önbelleğe yazma", "output": "Çıktı"}


def kredi(u):
    """Bir LLM çağrısının maliyeti (copilotUsage): token türü başına adet, birim fiyat, tutar.
    Birimler logdaki gibidir: nanoAIU; 1 AIU = 10^9 nanoAIU."""
    cu = u.get("copilotUsage") or {}
    if cu.get("totalNanoAiu") is None:
        return None
    satir = []
    for d in cu.get("tokenDetails") or []:
        birim = d.get("costPerBatch", 0) / max(d.get("batchSize", 1), 1)  # nanoAIU / token
        satir.append({"tur": KREDI_TUR.get(d.get("tokenType"), d.get("tokenType")), "token": d.get("tokenCount", 0),
                      "birim": birim, "tutar": d.get("tokenCount", 0) * birim})
    return {"toplam": cu["totalNanoAiu"], "satirlar": satir}


def uret(events):
    prompts = promptlara_bol(events)
    idx = {id(e): i for i, e in enumerate(events)}
    usage_all = [e for e in events if e["type"] == "session.usage_record"]

    sm = next((e for e in events if e["type"] == "system.message"), None)
    sm_text = sm["data"]["content"] if sm else ""
    first = usage_all[0]["data"]["usage"] if usage_all else {}
    first_user = next((e["data"].get("transformedContent", "") for e in events if e["type"] == "user.message"), "")
    sys_tok_raw = first.get("inputTokens", 0) - first.get("toolTokenCount", 0)
    ratio = (len(sm_text) + len(first_user)) / max(sys_tok_raw, 1) if sm_text else 4.0
    tok = lambda chars: round(chars / ratio)
    sys_sections = [{"ad": n, "token": tok(c)} for n, c in sorted(sistem_bolumleri(sm_text), key=lambda x: -x[1])] if sm_text else []

    model = next((e["data"].get("chosenModel") for e in events if e["type"] == "session.auto_mode_resolved"), "LLM")
    cwd = ""
    for e in events:
        if e["type"] in ("session.start", "session.resume"):
            cwd = (e["data"].get("context") or {}).get("cwd", cwd)

    # Konuşma geçmişi öğeleri (olay sırasına göre), her çağrının girdisini kurmak için
    conv = []  # (olay_index, öğe)
    exec_name = {e["data"]["toolCallId"]: e["data"].get("toolName") for e in events if e["type"] == "tool.execution_start"}
    for e in events:
        d, t = e.get("data", {}), e["type"]
        i = idx[id(e)]
        if t == "user.message":
            conv.append((i, {"rol": "user", "baslik": "Kullanıcı mesajı", "metin": kes(d.get("content", "")),
                             "tam": kes(d.get("transformedContent", "")), "token": tok(len(d.get("transformedContent", "")))}))
        elif t == "assistant.message":
            for r in d.get("toolRequests") or []:
                a = args_str(r.get("arguments"))
                conv.append((i, {"rol": "assistant", "baslik": f"LLM: tool_call → {r['name']}", "metin": kes(a, 1500), "token": tok(len(a))}))
            if d.get("content"):
                conv.append((i, {"rol": "assistant", "baslik": "LLM yanıtı", "metin": kes(d["content"]), "token": tok(len(d["content"]))}))
        elif t == "tool.execution_complete":
            res = (d.get("result") or {}).get("content", "")
            conv.append((i, {"rol": "tool", "baslik": f"Araç sonucu: {exec_name.get(d['toolCallId'], 'araç')}", "metin": kes(res), "token": tok(len(res))}))
        elif t == "skill.invoked":
            c = d.get("content", "")
            conv.append((i, {"rol": "skill", "baslik": f"Skill içeriği: {d.get('name')}", "metin": kes(c), "token": tok(len(c)), "seviye": 4}))

    out = []
    for pn, p in enumerate(prompts, 1):
        evs = p["events"]
        rs = [i for i, e in enumerate(evs) if e["type"] in ("session.start", "session.resume")]
        if rs:
            evs = evs[rs[-1]:]
        user = p["user"]["data"]
        steps = []

        def add(frm, to, seviye, etiket, baslik, aciklama, detay=None, tur="normal"):
            steps.append({"from": frm, "to": to, "seviye": seviye, "etiket": etiket, "baslik": baslik,
                          "aciklama": aciklama, "detay": detay or [], "tur": tur})

        hook_end = {e["data"]["hookInvocationId"]: e["data"] for e in evs if e["type"] == "hook.end"}

        def hook_step(e):
            d = e["data"]
            he = hook_end.get(d["hookInvocationId"], {})
            ht = d["hookType"]
            outp = (he.get("output") or {})
            det = [{"etiket": "Hook'a giden girdi", "metin": kes(json.dumps({k: v for k, v in d.get("input", {}).items() if k not in ("sessionId", "timestamp", "cwd", "transcriptPath")}, ensure_ascii=False, indent=2), 1500)}]
            if outp:
                det.append({"etiket": "Hook'un döndürdüğü", "metin": kes(json.dumps(outp, ensure_ascii=False, indent=2), 1500), "vurgu": True})
            aciklama = {
                "sessionStart": "Oturum açılırken çalışır.",
                "userPromptSubmitted": "Prompt gönderildiğinde çalışır; bağlama ek metin ekleyebilir.",
                "userPromptTransformed": "Prompt'un LLM'e gidecek hali hazırlandığında çalışır.",
                "preToolUse": "Araç çalışmadan önce çalışır; aracı engelleyebilir.",
                "postToolUse": "Araç bittikten sonra çalışır.",
                "preMcpToolCall": "MCP çağrısından hemen önce çalışan ek kapı.",
                "agentStop": "Agent turu bittiğinde çalışır.",
            }.get(ht, "")
            add("cli", "hooks", 5, ht, f"Hook: {ht}",
                f"{aciklama} Hook'lar sizin tanımladığınız deterministik komutlardır; LLM değildir." + (" Bu hook bağlama metin ekledi." if outp.get("additionalContext") else ""),
                det, "hook")

        # 1) prompt
        eklenen = user.get("transformedContent", "").replace(user.get("content", ""), "").strip()
        det = [{"etiket": "Yazdığınız", "metin": kes(user.get("content", "")), "vurgu": True}]
        if eklenen:
            det.append({"etiket": "CLI'ın eklediği (LLM bunları da görecek)", "metin": kes(eklenen), "seviye": 3})
        for e in evs:
            if e["type"] == "hook.start" and e["data"]["hookType"] == "sessionStart":
                hook_step(e)
        add("user", "cli", 1, "prompt", "Geliştirici bir istek yazar",
            f"“{kisalt(user.get('content', ''), 120)}” Copilot CLI'a gider. CLI bir LLM değildir: mesajı alır, LLM'e gönderilecek bağlamı hazırlar.", det, "ana")
        for e in evs:
            if e["type"] == "hook.start" and e["data"]["hookType"] in ("userPromptSubmitted", "userPromptTransformed"):
                hook_step(e)

        # yan model (ör. hayal kırıklığı sınıflandırıcısı)
        snap = next((e for e in evs if e["type"] == "model.messages_snapshot"), None)
        if snap:
            msgs = snap["data"].get("messages", [])
            sm0 = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
            resp = next((e for e in evs if e["type"] == "model.response"), None)
            rtxt = ""
            if resp:
                rtxt = json.dumps(resp["data"].get("response") or resp["data"].get("output") or "", ensure_ascii=False)[:600]
            smodel = next((e["data"].get("model") for e in evs if e["type"] == "model.turn_started"), "küçük model")
            add("cli", "side", 5, f"yan çağrı: {smodel}", f"Yan LLM: {smodel}",
                "Ana modelden ayrı, küçük bir model başka bir görev için çağrılır. Copilot'ta birden fazla LLM çalışır.",
                [{"etiket": "Yan modele verilen görev", "metin": kes(sm0, 1200)}] + ([{"etiket": "Yanıt", "metin": rtxt}] if rtxt else []), "llm")

        # 2) LLM turları
        turn_starts = [e for e in evs if e["type"] == "assistant.turn_start"]
        amsgs = [e for e in evs if e["type"] == "assistant.message"]
        usage = [e for e in evs if e["type"] == "session.usage_record"]
        starts = {e["data"]["toolCallId"]: e["data"] for e in evs if e["type"] == "tool.execution_start"}
        comps = {e["data"]["toolCallId"]: e["data"] for e in evs if e["type"] == "tool.execution_complete"}
        ext = {e["data"].get("toolCallId") for e in evs if e["type"] == "external_tool.requested"}
        skills = [e["data"] for e in evs if e["type"] == "skill.invoked"]
        tool_hooks = {}
        for e in evs:
            if e["type"] == "hook.start" and e["data"]["hookType"] in ("preToolUse", "postToolUse", "preMcpToolCall"):
                inp = e["data"].get("input", {})
                cid = inp.get("toolCallId") or next((c.get("id") for c in inp.get("toolCalls", [])), None)
                tool_hooks.setdefault(cid, []).append(e)
        mcp_ids = [c for c, d in starts.items() if d.get("mcpServerName")]
        for e in tool_hooks.pop(None, []):
            # toolCallId'siz preMcpToolCall: zamana göre en yakın MCP çağrısına bağla
            best = min(mcp_ids, key=lambda c: abs(idx[id(e)] - next(idx[id(x)] for x in evs if x["type"] == "tool.execution_start" and x["data"]["toolCallId"] == c)), default=None)
            if best:
                tool_hooks.setdefault(best, []).append(e)
        prev_start = None
        for ci, tsr in enumerate(turn_starts):
            u = usage[ci]["data"]["usage"] if ci < len(usage) else {}
            cut = idx[id(tsr)]
            since = idx[id(turn_starts[ci - 1])] if ci > 0 else idx[id(p["user"])] - 1
            items = []
            for i, it in conv:
                if i < cut:
                    items.append(dict(it, yeni=i > since))
            tin = u.get("inputTokens", 0)
            tools_tok = u.get("toolTokenCount", 0)
            sys_tok = sum(s["token"] for s in sys_sections)
            hist_tok = max(tin - tools_tok - sys_tok, 0)
            yeni_tok = sum(it["token"] for it in items if it["yeni"])
            baglam = {"tur": "baglam", "toplam": tin, "onbellek": u.get("cacheReadTokens", 0), "araclar": tools_tok,
                      "arac_sayisi": u.get("availableToolCount", 0), "sistem": sys_tok, "sistem_bolumleri": sys_sections,
                      "gecmis": hist_tok, "ogeler": items,
                      # Önbellek: eski loglarda cacheWriteTokens olmayabilir (None → gösterilmez)
                      "yazilan": u.get("cacheWriteTokens"), "onbellek_ttl": u.get("cacheTtlSeconds"),
                      "kredi": kredi(u)}
            n_new = sum(1 for it in items if it["yeni"])
            add("cli", "llm", 1, f"LLM çağrısı {ci + 1}", f"LLM çağrısı {ci + 1}: CLI bağlamı LLM'e gönderir",
                ("LLM'in hafızası yoktur: her çağrıda konuşmanın tamamı yeniden gönderilir. " if ci or pn > 1 else "")
                + (f"Bu çağrıda yeni olan {n_new} öğe sarıyla işaretli." if ci else "Aşağıda LLM'e giden mesajlar var."),
                [baglam], "llm")
            am = amsgs[ci]["data"] if ci < len(amsgs) else {}
            reqs = am.get("toolRequests") or []
            reasoning = am.get("reasoningText") or ""
            out_tok = u.get("outputTokens", 0)
            if reqs:
                for r in reqs:
                    a = args_str(r.get("arguments"))
                    det = [{"etiket": "LLM'in döndürdüğü istek", "metin": json.dumps({"tool": r["name"], "arguments": r.get("arguments")}, ensure_ascii=False, indent=2), "vurgu": True}]
                    if reasoning:
                        det.insert(0, {"etiket": "Modelin düşünce özeti", "metin": kes(reasoning, 1500), "seviye": 3})
                    add("llm", "cli", 2, f"tool_call: {r['name']}", f"LLM bir araç çağırmak istiyor: {r['name']}",
                        "LLM aracı kendisi çalıştıramaz. Sadece 'şu aracı şu argümanlarla çağır' diyen bir istek döndürür; çalıştırmak CLI'ın işidir.",
                        det, "donus")
                    cid = r["toolCallId"]
                    hooks = sorted(tool_hooks.get(cid, []), key=lambda e: idx[id(e)])
                    for h in hooks:
                        if h["data"]["hookType"] != "postToolUse":
                            hook_step(h)
                    sd = starts.get(cid, {})
                    if sd.get("mcpServerName"):
                        tgt, tlabel = "mcp", sd.get("mcpToolName") or r["name"]
                        acik = f"Araç bir MCP sunucusunda ({sd['mcpServerName']}, {sd.get('mcpTransport', '?')}). CLI isteği JSON-RPC ile oraya iletir; sunucu Copilot'tan ayrı bir programdır."
                    elif cid in ext:
                        tgt, tlabel = "host", r["name"]
                        acik = "Bu araç CLI'ın içinde değil, onu çalıştıran VS Code tarafında tanımlı (external_tool)."
                        if r["name"] == "tool_search_tool":
                            acik += " tool_search_tool, baştan bağlamda olmayan araçları (ör. MCP araçları) bulmak için kullanılır."
                    else:
                        tgt, tlabel = "tools", r["name"]
                        acik = "Yerleşik araç: CLI bunu kendi içinde, sizin makinenizde çalıştırır."
                        if r["name"] == "skill":
                            acik = "Skill de bir araç gibi çağrılır: CLI skill dosyasını bulup içeriğini bağlama ekleyecek."
                    add("cli", tgt, 2, tlabel, f"Araç çalışıyor: {tlabel}", acik,
                        [{"etiket": "Argümanlar", "metin": kes(a, 1500)}], "arac")
                    c = comps.get(cid, {})
                    res = (c.get("result") or {}).get("content", "")
                    add(tgt, "cli", 2, "sonuç" if c.get("success", True) else "hata", "Araç sonucu CLI'a döner",
                        "Sonuç metin olarak döner. LLM bunu hemen görmez: bir sonraki LLM çağrısında bağlama eklenecek.",
                        [{"etiket": "Aracın döndürdüğü", "metin": kes(res), "vurgu": True}], "donus")
                    if r["name"] == "skill":
                        sk = next((s for s in skills if s.get("name") == (r.get("arguments") or {}).get("skill")), skills[0] if skills else None) if isinstance(r.get("arguments"), dict) else (skills[0] if skills else None)
                        if sk:
                            path = sk.get("path", "")
                            rel = os.path.relpath(path, cwd) if cwd and path.startswith(cwd) else temizle(path)
                            add("cli", "skill", 4, "SKILL.md oku", f"Skill dosyası okunur: {rel}",
                                "Şimdiye kadar bağlamda bu skill'in sadece adı ve açıklaması vardı. Model seçince tam içeriği yüklenir (kademeli yükleme).",
                                [{"etiket": "Dosya", "metin": rel}], "arac")
                            add("skill", "cli", 4, "içerik → bağlama", "Skill içeriği bağlama eklenir",
                                f"{len(sk.get('content', '')):,} karakter (≈{tok(len(sk.get('content', ''))):,} token) bir sonraki LLM çağrısına eklenecek.".replace(",", "."),
                                [{"etiket": "SKILL.md içeriği", "metin": kes(sk.get("content", ""), 2500)}], "donus")
                    for h in hooks:
                        if h["data"]["hookType"] == "postToolUse":
                            hook_step(h)
            else:
                text = am.get("content", "")
                det = [{"etiket": "LLM'in yanıtı", "metin": kes(text), "vurgu": True}]
                if reasoning:
                    det.insert(0, {"etiket": "Modelin düşünce özeti", "metin": kes(reasoning, 1500), "seviye": 3})
                add("llm", "cli", 1, "yanıt metni", "LLM metinle yanıt verir",
                    f"Araç istemeden düz metin döndürdü ({out_tok} token). Bu, döngünün bittiği anlamına gelir.", det, "donus")
        for e in evs:
            if e["type"] == "hook.start" and e["data"]["hookType"] == "agentStop":
                hook_step(e)
        n_tools = len(comps)
        son_det = [{"etiket": "Ekranda görünen yanıt", "metin": kes(amsgs[-1]["data"].get("content", "") if amsgs else "")}]
        # Prompt'un toplam maliyeti: çağrıların nanoAIU toplamı + premium istek farkı (checkpoint'lerden)
        nano = [((x["data"]["usage"].get("copilotUsage") or {}).get("totalNanoAiu")) for x in usage]
        if any(n is not None for n in nano):
            # Checkpoint, prompt'un kapanış hook'undan SONRA yazılır: prompt'un son olayından sonraki
            # ilk checkpoint bu prompt'a, kullanıcı mesajından önceki son checkpoint bir öncekine aittir.
            cps = [e for e in events if e["type"] == "session.usage_checkpoint"]
            son_i, user_i = idx[id(p["events"][-1])], idx[id(p["user"])]
            sonra = [c for c in cps if idx[id(c)] > son_i]
            once = [c for c in cps if idx[id(c)] < user_i]
            prem = None
            if sonra:
                prem = (sonra[0]["data"].get("totalPremiumRequests") or 0) - ((once[-1]["data"].get("totalPremiumRequests") or 0) if once else 0)
            satirlar = [["LLM çağrısı", str(len(turn_starts))],
                        ["Toplam maliyet", f"{sum(n or 0 for n in nano) / 1e9:.3f} AIU".replace(".", ",")],
                        ["Çağrı başına", " · ".join(f"{(n or 0) / 1e9:.3f}".replace(".", ",") for n in nano) + " AIU"]]
            if prem is not None:
                satirlar.append(["Premium istek", f"{prem} (LLM çağrısı başına değil, prompt başına sayılır)"])
            son_det.append({"tur": "ozet", "etiket": "Bu prompt'un maliyeti", "satirlar": satirlar, "seviye": 3})
        add("cli", "user", 1, "yanıt", "Yanıt geliştiriciye gösterilir",
            f"Bu istek için {len(turn_starts)} LLM çağrısı" + (f" ve {n_tools} araç çağrısı" if n_tools else "") + " yapıldı.",
            son_det, "ana")
        out.append({"no": pn, "metin": kisalt(user.get("content", ""), 60), "adimlar": steps,
                    "ozet": {"llm": len(turn_starts), "arac": n_tools}})
    return {"model": model, "oran": round(ratio, 2), "promptlar": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events")
    ap.add_argument("-o", "--out", default="copilot-oynatici.html")
    ap.add_argument("--baslik", default="Copilot CLI içeriden: bir istek nasıl işlenir?")
    ap.add_argument("--promptlar", help="yalnızca bu prompt'ları al, ör. 2,5,7 veya 3-6 (numaralar --list çıktısındaki gibi)")
    ap.add_argument("--list", action="store_true", help="oturumdaki prompt'ları numaralarıyla listele ve çık")
    a = ap.parse_args()
    events = oku(a.events)
    if a.list:
        for i, p in enumerate(promptlara_bol(events), 1):
            n_llm = sum(1 for e in p["events"] if e["type"] == "assistant.turn_start")
            n_tool = sum(1 for e in p["events"] if e["type"] == "tool.execution_start")
            print(f"{i:>3}. {kisalt(p['user']['data'].get('content', ''), 70)}  ·  {n_llm} LLM, {n_tool} araç")
        return
    data = uret(events)
    if a.promptlar:
        sec = set()
        for part in a.promptlar.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                sec.update(range(int(lo), int(hi) + 1))
            elif part:
                sec.add(int(part))
        data["promptlar"] = [p for p in data["promptlar"] if p["no"] in sec]
        if not data["promptlar"]:
            sys.exit("Seçilen numaralarda prompt yok; --list ile numaralara bakın.")
    data["baslik"] = a.baslik
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "copilot_player_template.html"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__DATA__*/null", payload))
    n = sum(len(p["adimlar"]) for p in data["promptlar"])
    print(f"{a.out} · {len(data['promptlar'])} prompt, {n} adım")


if __name__ == "__main__":
    main()
