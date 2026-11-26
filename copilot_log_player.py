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


# Oynatıcının işlediği ya da bilerek göstermediği olay türleri. Bunların dışındakiler son adımda
# (Seviye 5) "gösterilmeyen olaylar" olarak listelenir ve üretim sırasında uyarı basılır.
BILINEN_OLAYLAR = {
    # gösterilenler
    "user.message", "assistant.turn_start", "assistant.turn_end", "assistant.message",
    "tool.execution_start", "tool.execution_complete", "external_tool.requested", "external_tool.completed",
    "hook.start", "hook.end", "skill.invoked", "subagent.started", "subagent.completed", "human_response.recorded",
    "session.usage_record", "session.usage_checkpoint", "system.message", "session.auto_mode_resolved",
    # bilerek gösterilmeyenler (oturum yönetimi, telemetri, kopya bilgi)
    "subagent.failed", "session.error", "system.notification",
    "session.start", "session.resume", "session.shutdown", "session.permissions_changed",
    "instruction.update", "skill.context_delivered_ref", "subagent.deselected",
    # alt agent ayarları: "görev" adımında özetlenir
    "subagent.configured", "subagent.selected", "session.model_change",
}
BILINEN_ONEKLER = ("model.",)  # yan model olayları (model.turn_started, model.messages_snapshot, …)


def olcekle(items, hedef, sifreli=0):
    """Mesajların karakterden tahmin edilen token toplamını, ölçülen konuşma toplamıyla (hedef) eşitler.

    - Tahmin < ölçülen (gerçek loglarda olağan): tahminlere dokunulmaz; aradaki fark ayrı bir
      "görünmeyen kısım" satırı olur. Fark çoğunlukla modele geri gönderilen şifreli düşünce
      bloklarından (assistant.message.encryptedContent) ve mesaj çerçevelerinden gelir.
    - Tahmin > ölçülen: tahminler orantılı küçültülür (en büyük kalan yöntemiyle yuvarlanır).
    Dönüş: "fark", "olcek" ya da None (eşitlenemedi)."""
    toplam = sum(it["token"] for it in items)
    if hedef <= 0 or toplam <= 0:
        return None
    if toplam <= hedef:
        if hedef > toplam:
            aciklama = ("Ölçülen konuşma toplamı ile görünen mesajların tahmini arasındaki fark. "
                        "Bu kısım logda okunamaz: modelin önceki yanıtlarıyla birlikte geri gönderilen şifreli "
                        "düşünce blokları, mesaj/araç çağrısı çerçeveleri ve CLI'ın eklediği sistem hatırlatmaları.")
            if sifreli:
                aciklama += f" Bu çağrıya kadar {sifreli} yanıtta şifreli düşünce bloğu var."
            items.append({"rol": "gizli", "baslik": "Logda içeriği görünmeyen kısım", "metin": aciklama,
                          "token": hedef - toplam, "yeni": False})
        return "fark"
    ham = [it["token"] * hedef / toplam for it in items]
    taban = [int(x) for x in ham]
    kalan = hedef - sum(taban)
    for j in sorted(range(len(items)), key=lambda j: ham[j] - taban[j], reverse=True)[:kalan]:
        taban[j] += 1
    for it, t in zip(items, taban):
        it["tahmin"] = it["token"]
        it["token"] = t
    return "olcek"


KREDI_USD = 0.01  # 1 AI kredisi = 0,01 $ (GitHub Copilot fatura belgesi)


def tr_sayi(x, basamak=3):
    return f"{x:,.{basamak}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def dolar(kredi_):
    """Küçük tutarlar için 2 anlamlı basamak: 0,315 kredi → 0,0032 $."""
    usd = kredi_ * KREDI_USD
    if usd == 0:
        return "0 $"
    basamak = max(2, -int(f"{usd:e}".split("e")[1]) + 1)
    return tr_sayi(usd, basamak) + " $"


HOOK_ACIKLAMA = {
    "sessionStart": "Oturum açılırken çalışır.",
    "userPromptSubmitted": "Prompt gönderildiğinde çalışır; bağlama ek metin ekleyebilir.",
    "userPromptTransformed": "Prompt'un LLM'e gidecek hali hazırlandığında çalışır.",
    "preToolUse": "Araç çalışmadan önce çalışır; aracı engelleyebilir.",
    "postToolUse": "Araç bittikten sonra çalışır.",
    "postToolUseFailure": "Araç hata ile bittiğinde çalışır.",
    "preMcpToolCall": "MCP çağrısından hemen önce çalışan ek kapı.",
    "agentStop": "Agent turu bittiğinde çalışır.",
    "subagentStart": "Alt agent başlarken çalışır.",
    "subagentStop": "Alt agent bittiğinde çalışır.",
    "errorOccurred": "Bir hata oluştuğunda çalışır.",
}

MODEL_SECIMI = {
    "agent_definition_default": "alt agent türünün kendi varsayılan modeli istendi",
    "session_inheritance": "oturumun modeli devralındı",
}


def turlar(evs):
    """Bir agent'ın LLM turları: her assistant.turn_start için o turun mesajları (bir tur birden
    fazla assistant.message içerebilir) ve kapanış sırası. Dönüş: [(turn_start, [mesajlar], son_index)]."""
    sira = [e for e in evs if e["type"] in ("assistant.turn_start", "assistant.turn_end", "assistant.message")]
    out = []
    for e in sira:
        if e["type"] == "assistant.turn_start":
            out.append([e, [], None])
        elif e["type"] == "assistant.message" and out and out[-1][2] is None:
            out[-1][1].append(e)
        elif e["type"] == "assistant.turn_end" and out and out[-1][2] is None:
            out[-1][2] = e
    return out


def birlesik(msgs):
    """Bir turun mesajlarını tek yanıtta toplar: tüm araç istekleri, son metin, düşünce özetleri."""
    reqs = [r for m in msgs for r in (m["data"].get("toolRequests") or [])]
    metin = next((m["data"]["content"] for m in reversed(msgs) if m["data"].get("content")), "")
    dusunce = "\n\n".join(m["data"]["reasoningText"] for m in msgs if m["data"].get("reasoningText"))
    return reqs, metin, dusunce


def uret(events, aylik_kredi=None):
    prompts = promptlara_bol(events)
    idx = {id(e): i for i, e in enumerate(events)}
    ana_tum = [e for e in events if not e.get("agentId")]  # ana agent'ın olayları
    usage_all = [e for e in ana_tum if e["type"] == "session.usage_record"]

    sm = next((e for e in ana_tum if e["type"] == "system.message"), None)
    sm_text = sm["data"]["content"] if sm else ""
    first = usage_all[0]["data"]["usage"] if usage_all else {}
    first_user = next((e["data"].get("transformedContent", "") for e in ana_tum if e["type"] == "user.message"), "")
    sys_tok_raw = first.get("inputTokens", 0) - first.get("toolTokenCount", 0)
    ratio = (len(sm_text) + len(first_user)) / max(sys_tok_raw, 1) if sm_text else 4.0
    tok = lambda chars: round(chars / ratio)
    bolumler = lambda txt: [{"ad": n, "token": tok(c)} for n, c in sorted(sistem_bolumleri(txt), key=lambda x: -x[1])] if txt else []
    sys_sections = bolumler(sm_text)

    model = next((e["data"].get("chosenModel") for e in events if e["type"] == "session.auto_mode_resolved"), "LLM")
    cwd = ""
    for e in events:
        if e["type"] in ("session.start", "session.resume"):
            cwd = (e["data"].get("context") or {}).get("cwd", cwd)

    # CLI'ın kendisinin eklediği araç çağrıları: alt agent bitince gelen bildirimden sonra, bir LLM turu
    # açılmadan yazılan assistant.message (ör. read_agent). Bunları LLM istemedi.
    sentetik = set()
    acik, bildirim_var = False, False
    for e in ana_tum:
        t = e["type"]
        if t == "assistant.turn_start":
            acik, bildirim_var = True, False
        elif t == "assistant.turn_end":
            acik = False
        elif t == "system.notification":
            bildirim_var = True
        elif t == "assistant.message" and not acik and bildirim_var:
            sentetik.add(id(e))

    exec_name = {e["data"]["toolCallId"]: e["data"].get("toolName") for e in events if e["type"] == "tool.execution_start"}

    def konusma(evs_, tok=tok):
        """Konuşma geçmişi öğeleri (olay sırasına göre), her çağrının girdisini kurmak için."""
        conv = []  # (olay_index, öğe)
        for e in evs_:
            d, t = e.get("data", {}), e["type"]
            i = idx[id(e)]
            if t == "user.message":
                conv.append((i, {"rol": "user", "baslik": "Görev (ana LLM'in yazdığı)" if e.get("agentId") else "Kullanıcı mesajı",
                                 "metin": kes(d.get("content", "")),
                                 "tam": kes(d.get("transformedContent", "")), "token": tok(len(d.get("transformedContent", "") or d.get("content", "")))}))
            elif t == "assistant.message":
                kim = "CLI (otomatik)" if id(e) in sentetik else "LLM"
                for r in d.get("toolRequests") or []:
                    a = args_str(r.get("arguments"))
                    conv.append((i, {"rol": "assistant", "baslik": f"{kim}: tool_call → {r['name']}", "metin": kes(a, 1500), "token": tok(len(a))}))
                if d.get("content"):
                    conv.append((i, {"rol": "assistant", "baslik": "LLM yanıtı", "metin": kes(d["content"]), "token": tok(len(d["content"]))}))
            elif t == "tool.execution_complete":
                res = (d.get("result") or {}).get("content", "")
                conv.append((i, {"rol": "tool", "baslik": f"Araç sonucu: {exec_name.get(d['toolCallId'], 'araç')}", "metin": kes(res), "token": tok(len(res))}))
            elif t == "skill.invoked":
                c = d.get("content", "")
                conv.append((i, {"rol": "skill", "baslik": f"Skill içeriği: {d.get('name')}", "metin": kes(c), "token": tok(len(c)), "seviye": 4}))
            elif t == "system.notification":
                c = d.get("content", "")
                conv.append((i, {"rol": "user", "baslik": "CLI bildirimi (kullanıcı yazmadı)", "metin": kes(c), "token": tok(len(c))}))
        return conv

    conv_ana = konusma(ana_tum)
    sifreli_ana = [idx[id(e)] for e in ana_tum if e["type"] == "assistant.message" and e["data"].get("encryptedContent")]

    def baglam_kur(u, conv, sifreli_idx, cut, since, sistem_tok, sistem_bol):
        items = [dict(it, yeni=i > since) for i, it in conv if i < cut]
        tin = u.get("inputTokens", 0)
        tools_tok = u.get("toolTokenCount", 0)
        hist_tok = max(tin - tools_tok - sistem_tok, 0)
        olcekli = olcekle(items, hist_tok, sum(1 for i in sifreli_idx if i < cut))
        return {"tur": "baglam", "toplam": tin, "onbellek": u.get("cacheReadTokens", 0), "araclar": tools_tok,
                "arac_sayisi": u.get("availableToolCount", 0), "sistem": sistem_tok, "sistem_bolumleri": sistem_bol,
                "gecmis": hist_tok, "ogeler": items, "olcekli": olcekli,
                # Önbellek: eski loglarda cacheWriteTokens olmayabilir (None → gösterilmez)
                "yazilan": u.get("cacheWriteTokens"), "onbellek_ttl": u.get("cacheTtlSeconds"),
                "kredi": kredi(u)}

    out = []
    uyarilar = []  # (prompt no, olay türü, adet)
    for pn, p in enumerate(prompts, 1):
        evs = p["events"]
        rs = [i for i, e in enumerate(evs) if e["type"] in ("session.start", "session.resume") and not e.get("agentId")]
        if rs:
            evs = evs[rs[-1]:]
        ana = [e for e in evs if not e.get("agentId")]
        alt_ev = {}
        for e in evs:
            if e.get("agentId"):
                alt_ev.setdefault(e["agentId"], []).append(e)
        user = p["user"]["data"]
        user_i = idx[id(p["user"])]
        steps = []
        kur = [user_i]  # adımların sıralama anahtarı (olay sırası); aynı anahtarlılar eklenme sırasını korur

        def add(frm, to, seviye, etiket, baslik, aciklama, detay=None, tur="normal", k=None):
            steps.append({"from": frm, "to": to, "seviye": seviye, "etiket": etiket, "baslik": baslik,
                          "aciklama": aciklama, "detay": detay or [], "tur": tur, "_k": kur[0] if k is None else k})

        hook_end = {e["data"]["hookInvocationId"]: e["data"] for e in evs if e["type"] == "hook.end"}
        gosterilen_hook = set()

        def hook_step(e, k=None):
            d = e["data"]
            gosterilen_hook.add(d["hookInvocationId"])
            he = hook_end.get(d["hookInvocationId"], {})
            ht = d["hookType"]
            outp = (he.get("output") or {})
            det = [{"etiket": "Hook'a giden girdi", "metin": kes(json.dumps({k_: v for k_, v in d.get("input", {}).items() if k_ not in ("sessionId", "timestamp", "cwd", "transcriptPath")}, ensure_ascii=False, indent=2), 1500)}]
            if outp:
                det.append({"etiket": "Hook'un döndürdüğü", "metin": kes(json.dumps(outp, ensure_ascii=False, indent=2), 1500), "vurgu": True})
            alt = " (alt agent)" if e.get("agentId") else ""
            add("cli", "hooks", 5, ht + alt, f"Hook{alt}: {ht}",
                f"{HOOK_ACIKLAMA.get(ht, '')} Hook'lar sizin tanımladığınız deterministik komutlardır; LLM değildir." + (" Bu hook bağlama metin ekledi." if outp.get("additionalContext") else ""),
                det, "hook", k)

        def kendi_ya_da_prompt(e):
            # prompt'tan önce gelen hook'lar prompt adımının hemen ardından; sonra gelenler (ör. bildirimle
            # açılan ikinci tur) kendi yerinde gösterilir
            return max(idx[id(e)], user_i)

        # 1) prompt
        eklenen = user.get("transformedContent", "").replace(user.get("content", ""), "").strip()
        det = [{"etiket": "Yazdığınız", "metin": kes(user.get("content", "")), "vurgu": True}]
        if eklenen:
            det.append({"etiket": "CLI'ın eklediği (LLM bunları da görecek)", "metin": kes(eklenen), "seviye": 3})
        for e in ana:
            if e["type"] == "hook.start" and e["data"]["hookType"] == "sessionStart":
                hook_step(e, kendi_ya_da_prompt(e) if idx[id(e)] > user_i else user_i - 0.5)
        add("user", "cli", 1, "prompt", "Geliştirici bir istek yazar",
            f"“{kisalt(user.get('content', ''), 120)}” Copilot CLI'a gider. CLI bir LLM değildir: mesajı alır, LLM'e gönderilecek bağlamı hazırlar.", det, "ana")
        for e in ana:
            if e["type"] == "hook.start" and e["data"]["hookType"] in ("userPromptSubmitted", "userPromptTransformed"):
                hook_step(e, kendi_ya_da_prompt(e))

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

        # 2) LLM turları (ana agent)
        tur_listesi = turlar(ana)
        usage = [e for e in ana if e["type"] == "session.usage_record"]
        starts = {e["data"]["toolCallId"]: e["data"] for e in evs if e["type"] == "tool.execution_start"}
        comps = {e["data"]["toolCallId"]: e for e in evs if e["type"] == "tool.execution_complete"}
        ext = {e["data"].get("toolCallId") for e in ana if e["type"] == "external_tool.requested"}
        alt_agent = {e["data"].get("toolCallId"): e for e in evs if e["type"] == "subagent.started"}
        insan_cevap = {e["data"].get("toolCallId"): e["data"] for e in ana if e["type"] == "human_response.recorded"}
        skills = [e["data"] for e in ana if e["type"] == "skill.invoked"]
        agent_stop = [idx[id(e)] for e in ana if e["type"] == "hook.start" and e["data"]["hookType"] == "agentStop"]
        tool_hooks = {}
        for e in ana:
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

        def arac_adimlari(r, kaynak="cli", sev=2, cli_istedi=False):
            """Bir araç isteğinin çalışması ve sonucu. cli_istedi: çağrıyı LLM değil CLI kendisi ekledi."""
            a = args_str(r.get("arguments"))
            cid = r["toolCallId"]
            hooks = sorted(tool_hooks.get(cid, []), key=lambda e: idx[id(e)])
            for h in hooks:
                if h["data"]["hookType"] != "postToolUse":
                    hook_step(h)
            sd = starts.get(cid, {})
            sub = alt_agent.get(cid)
            soru = cid in insan_cevap or r["name"] == "ask_user"
            arka_plan = False
            if soru:
                # LLM kullanıcıya soru soruyor: araç çağrısı geliştiriciye gider, cevabı araç sonucu olarak döner
                tgt, tlabel = "user", "soru (ask_user)"
                acik = ("LLM ilerlemek için size bir soru soruyor. Bu da bir araç çağrısıdır: CLI soruyu ekranda gösterir, "
                        "verdiğiniz cevap araç sonucu olarak bir sonraki LLM çağrısında bağlama girer.")
            elif sub:
                sd_ = sub["data"]
                tgt, tlabel = "agent", sd_.get("agentDisplayName") or sd_.get("agentName") or r["name"]
                acik = (f"CLI işi ayrı bir alt agent'a ({tlabel}, tür: {sd_.get('agentName', '?')}) devreder. Alt agent ayrı bir LLM oturumudur: "
                        "ana konuşmayı görmez, yalnızca ana LLM'in yazdığı görev metnini alır. Kendi LLM döngüsünü çalıştırır; "
                        "ana LLM onun ara adımlarını değil, yalnızca sonucunu görür.")
                if sd_.get("agentDescription"):
                    acik += f" Görevi: {kisalt(sd_['agentDescription'], 160)}"
            elif r["name"] in ("read_agent", "write_agent", "list_agents"):
                tgt, tlabel = "agent", r["name"]
                acik = {"read_agent": "Alt agent'ın durumunu ve ürettiği sonucu okur. Alt agent'ın sonucu ana bağlama kendiliğinden girmez; bu araçla okunur.",
                        "write_agent": "Çalışan bir alt agent'a ek mesaj gönderir (aynı görevin devamı).",
                        "list_agents": "Çalışan alt agent'ları listeler."}[r["name"]]
            elif sd.get("mcpServerName"):
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
            if cid not in starts:
                # Araç hiç başlamadı: kullanıcı reddetmiş, hook engellemiş ya da tur iptal edilmiş olabilir
                add(kaynak, tgt, sev, f"{tlabel} (çalışmadı)", f"Araç çalıştırılmadı: {tlabel}",
                    "LLM bu aracı istedi ama logda aracın başladığına dair olay yok. Kullanıcı onay vermemiş, "
                    "bir hook engellemiş ya da tur iptal edilmiş olabilir.",
                    [{"etiket": "İstenen argümanlar", "metin": kes(a, 1500)}], "arac")
                return
            if cli_istedi:
                tlabel = f"{tlabel} (CLI)"
                acik = ("Bu çağrıyı LLM istemedi; öncesinde LLM çağrısı yok. Alt agent bitince CLI sonucunu kendisi okur, "
                        "çağrıyı ve sonucunu konuşmaya ekler. Ana LLM bir sonraki çağrıda ikisini de görür.")
            add(kaynak, tgt, sev, tlabel, ("CLI kendisi bir araç çağırır: " if cli_istedi else "Araç çalışıyor: ") + tlabel, acik,
                [{"etiket": "Argümanlar", "metin": kes(a, 1500)}], "arac")
            c = comps.get(cid)
            if c is None:
                add(tgt, kaynak, sev, "sonuç yok", "Araç sonucu logda yok",
                    "Araç başladı ama bittiğine dair olay yok: yarıda kesilmiş ya da oturum kapanmış olabilir.",
                    [], "donus")
            else:
                c = c["data"]
                res = (c.get("result") or {}).get("content", "")
                hata = c.get("success") is False
                arka_plan = bool(sub) and res.startswith("Agent started in background")
                if arka_plan:
                    add(tgt, kaynak, sev, "arka planda başladı", "Alt agent arka planda başladı",
                        "task aracı alt agent'ı başlatıp hemen döner; dönen şey sonuç değil, alt agent'ın kimliği. "
                        "Ana LLM beklemez: kullanıcıya bekleneceğini söyleyip turunu bitirir. Alt agent bitince CLI haber verir.",
                        [{"etiket": "Aracın döndürdüğü", "metin": kes(res), "vurgu": True}], "donus")
                else:
                    add(tgt, kaynak, sev, "hata" if hata else ("cevap" if soru else "sonuç"),
                        "Araç hata döndürdü" if hata else ("Geliştiricinin cevabı CLI'a döner" if soru else "Araç sonucu CLI'a döner"),
                        ("Araç başarısız oldu; hata mesajı da bir sonuç gibi bir sonraki LLM çağrısında bağlama eklenir ve "
                         "model ona göre yeni bir yol dener." if hata else
                         "Sonuç metin olarak döner. LLM bunu hemen görmez: bir sonraki LLM çağrısında bağlama eklenecek."),
                        [{"etiket": "Aracın döndürdüğü", "metin": kes(res), "vurgu": True}], "donus")
            if r["name"] == "skill":
                sk = next((s_ for s_ in skills if s_.get("name") == (r.get("arguments") or {}).get("skill")), skills[0] if skills else None) if isinstance(r.get("arguments"), dict) else (skills[0] if skills else None)
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

        son_metin = ""
        for ci, (tsr, msgs, tend) in enumerate(tur_listesi):
            kur[0] = idx[id(tsr)]
            u = usage[ci]["data"]["usage"] if ci < len(usage) else {}
            since = idx[id(tur_listesi[ci - 1][0])] if ci > 0 else user_i - 1
            baglam = baglam_kur(u, conv_ana, sifreli_ana, idx[id(tsr)], since, sum(s_["token"] for s_ in sys_sections), sys_sections)
            n_new = sum(1 for it in baglam["ogeler"] if it["yeni"])
            add("cli", "llm", 1, f"LLM çağrısı {ci + 1}", f"LLM çağrısı {ci + 1}: CLI bağlamı LLM'e gönderir",
                ("LLM'in hafızası yoktur: her çağrıda konuşmanın tamamı yeniden gönderilir. " if ci or pn > 1 else "")
                + (f"Bu çağrıda yeni olan {n_new} öğe sarıyla işaretli." if ci else "Aşağıda LLM'e giden mesajlar var."),
                [baglam], "llm")
            reqs, text, reasoning = birlesik(msgs)
            out_tok = u.get("outputTokens", 0)
            if reqs:
                for r in reqs:
                    det = [{"etiket": "LLM'in döndürdüğü istek", "metin": json.dumps({"tool": r["name"], "arguments": r.get("arguments")}, ensure_ascii=False, indent=2), "vurgu": True}]
                    if reasoning:
                        det.insert(0, {"etiket": "Modelin düşünce özeti", "metin": kes(reasoning, 1500), "seviye": 3})
                    add("llm", "cli", 2, f"tool_call: {r['name']}", f"LLM bir araç çağırmak istiyor: {r['name']}",
                        "LLM aracı kendisi çalıştıramaz. Sadece 'şu aracı şu argümanlarla çağır' diyen bir istek döndürür; çalıştırmak CLI'ın işidir.",
                        det, "donus")
                    arac_adimlari(r)
            else:
                son_metin = text or son_metin
                det = [{"etiket": "LLM'in yanıtı", "metin": kes(text), "vurgu": True}]
                if reasoning:
                    det.insert(0, {"etiket": "Modelin düşünce özeti", "metin": kes(reasoning, 1500), "seviye": 3})
                add("llm", "cli", 1, "yanıt metni", "LLM metinle yanıt verir",
                    f"Araç istemeden düz metin döndürdü ({out_tok} token). Bu, döngünün bittiği anlamına gelir.", det, "donus")
                # Ana agent turunu bitirdi ama prompt sürüyor (ör. arka plandaki alt agent'ı bekliyor): ara yanıt
                bitis = idx[id(tend)] if tend else idx[id(tsr)]
                sonraki = idx[id(tur_listesi[ci + 1][0])] if ci + 1 < len(tur_listesi) else None
                if sonraki is not None and any(bitis <= a_ < sonraki for a_ in agent_stop):
                    add("cli", "user", 1, "ara yanıt", "Ara yanıt geliştiriciye gösterilir",
                        "Ana agent turunu bitirdi; ekrandaki yanıt bu. Ama iş bitmedi: arka plandaki alt agent bitince CLI, "
                        "siz bir şey yazmadan ana agent'ı yeniden çalıştıracak.",
                        [{"etiket": "Ekranda görünen yanıt", "metin": kes(text)}], "ana")

        # 3) Alt agent'ların kendi içi (Seviye 4) ve sonuçları (Seviye 2), olay sırasındaki yerlerinde
        n_alt_llm = 0
        for aid, aev in alt_ev.items():
            bas = next((e for e in aev if e["type"] == "subagent.started"), None)
            bd = bas["data"] if bas else {}
            ad = bd.get("agentDisplayName") or bd.get("agentName") or "alt agent"
            ayar = {}
            for e in aev:
                if e["type"] in ("subagent.configured", "subagent.selected", "session.model_change"):
                    ayar.update(e["data"])
            gorev = next((e for e in aev if e["type"] == "user.message"), None)
            a_sm = next((e["data"].get("content", "") for e in aev if e["type"] == "system.message"), "")
            a_usage = [e for e in aev if e["type"] == "session.usage_record"]
            # Alt agent başka bir modelde (başka tokenizer) çalışabilir: karakter/token oranını kendi ilk
            # çağrısından hesapla (ilk çağrıda konuşma yalnızca görevden ibarettir)
            a_ratio = ratio
            if a_usage and a_sm:
                u0 = a_usage[0]["data"]["usage"]
                g_len = len((gorev or {}).get("data", {}).get("transformedContent", "") or (gorev or {}).get("data", {}).get("content", "")) if gorev else 0
                a_ratio = (len(a_sm) + g_len) / max(u0.get("inputTokens", 0) - u0.get("toolTokenCount", 0), 1)
            a_tok = lambda chars, r_=a_ratio: round(chars / r_)
            a_bol = [{"ad": n, "token": a_tok(c)} for n, c in sorted(sistem_bolumleri(a_sm), key=lambda x: -x[1])] if a_sm else []
            a_conv = konusma(aev, a_tok)
            a_sifreli = [idx[id(e)] for e in aev if e["type"] == "assistant.message" and e["data"].get("encryptedContent")]
            if gorev:
                gercek = next((e["data"]["usage"].get("modelDisplayName") or e["data"]["usage"].get("model") for e in a_usage if e["data"].get("usage")), None)
                istenen = ayar.get("model") or bd.get("model", "?")
                satir = [["Tür", bd.get("agentName", "?")], ["Model (istenen)", istenen]]
                if gercek and gercek != istenen:
                    satir.append(["Model (gerçekte çalışan)", gercek])
                if ayar.get("reasoningEffort"):
                    satir.append(["Düşünme düzeyi", ayar["reasoningEffort"]])
                if bd.get("modelSelectionSource"):
                    satir.append(["Model seçimi", f"{bd['modelSelectionSource']} ({MODEL_SECIMI.get(bd['modelSelectionSource'], '?')})"])
                add("cli", "agent", 4, "görev", f"Alt agent görevini alır: {ad}",
                    "Alt agent sıfırdan başlar: kendi sistem prompt'u ve bu görev metni dışında hiçbir şey bilmez. "
                    "Ana konuşmayı, sizin yazdıklarınızı görmez; ana LLM gereken her şeyi görev metnine yazmak zorundadır.",
                    [{"etiket": "Görev metni (ana LLM yazdı)", "metin": kes(gorev["data"].get("content", "")), "vurgu": True},
                     {"tur": "ozet", "etiket": "Alt agent ayarları", "satirlar": satir}], "arac", idx[id(gorev)])
            a_tur = turlar(aev)
            hatalar = [e for e in aev if e["type"] == "session.error"]
            for ci, (tsr, msgs, tend) in enumerate(a_tur):
                n_alt_llm += 1
                ti = idx[id(tsr)]
                u = a_usage[ci]["data"]["usage"] if ci < len(a_usage) else None
                det = []
                if u:
                    since = idx[id(a_tur[ci - 1][0])] if ci > 0 else (idx[id(gorev)] - 1 if gorev else -1)
                    det = [baglam_kur(u, a_conv, a_sifreli, ti, since, a_tok(len(a_sm)), a_bol)]
                mdl = (u or {}).get("modelDisplayName") or (u or {}).get("model")
                add("agent", "llm", 4, f"alt agent LLM çağrısı {ci + 1}", f"Alt agent kendi LLM çağrısını yapar ({ci + 1})",
                    "Bu, ana konuşmadan bağımsız ayrı bir bağlam: alt agent'ın sistem prompt'u, araçları ve görevi. "
                    + (f"Model: {mdl} (ana agent'ınkinden farklı olabilir). " if mdl else "")
                    + ("Aşağıda bu çağrıda LLM'e giden mesajlar var." if u else "Bu çağrı için logda kullanım kaydı yok; çağrı başarısız olmuş olabilir."),
                    det, "llm", ti)
                bitis = idx[id(tend)] if tend else ti
                for h in hatalar:
                    if ti < idx[id(h)] <= bitis + 2:
                        hd = h["data"]
                        add("llm", "agent", 4, f"hata {hd.get('statusCode', '')}".strip(), "LLM çağrısı hata döndürdü",
                            f"Model çağrısı başarısız oldu ({hd.get('errorCode', hd.get('errorType', '?'))}). Alt agent bu hatayla duruyor.",
                            [{"etiket": "Hata", "metin": kes(hd.get("message", "")), "vurgu": True}], "donus", ti + 0.1)
                reqs, text, reasoning = birlesik(msgs)
                for r in reqs:
                    add("llm", "agent", 4, f"tool_call: {r['name']}", f"Alt agent'ın LLM'i bir araç istiyor: {r['name']}",
                        "Alt agent da aynı döngüyle çalışır: LLM araç ister, alt agent çalıştırır, sonuç bir sonraki çağrıya eklenir.",
                        [{"etiket": "İstek", "metin": json.dumps({"tool": r["name"], "arguments": r.get("arguments")}, ensure_ascii=False, indent=2)}], "donus", ti + 0.2)
                    c = comps.get(r["toolCallId"])
                    if c is not None:
                        res = (c["data"].get("result") or {}).get("content", "")
                        add("agent", "tools", 4, r["name"], f"Alt agent aracı çalıştırır: {r['name']}",
                            "Alt agent'ın araç sonuçları yalnızca onun bağlamına girer, ana konuşmaya girmez.",
                            [{"etiket": "Aracın döndürdüğü", "metin": kes(res, 1500)}], "arac", ti + 0.3)
                if text:
                    add("llm", "agent", 4, "yanıt metni", "Alt agent'ın LLM'i metinle yanıt verir",
                        "Alt agent'ın son yanıtı onun sonucudur. Ana LLM bunu doğrudan görmez; sonuç ana bağlama ayrıca aktarılır.",
                        [{"etiket": "Yanıt", "metin": kes(text), "vurgu": True}], "donus", ti + 0.4)
            for e in aev:
                if e["type"] == "subagent.failed":
                    fd = e["data"]
                    neden = MODEL_SECIMI.get(fd.get("modelSelectionSource"), "")
                    add("agent", "cli", 2, "başarısız", f"Alt agent başarısız oldu: {ad}",
                        "Alt agent işini bitiremedi. Ana LLM bunu kendiliğinden öğrenmez; ancak alt agent'ın durumunu okuyunca (read_agent) görür."
                        + (f" Model: {fd.get('model', '?')} ({neden})." if neden else ""),
                        [{"etiket": "Hata", "metin": kes(fd.get("error", "")), "vurgu": True}], "donus", idx[id(e)])
                elif e["type"] == "subagent.completed":
                    cd = e["data"]
                    satir = [["Süre", f"{tr_sayi(cd.get('durationMs', 0) / 1000, 1)} sn"],
                             ["Toplam token", f"{cd.get('totalTokens', 0):,}".replace(",", ".")],
                             ["Araç çağrısı", str(cd.get("totalToolCalls", 0))]]
                    bildirim = next((x for x in ana if x["type"] == "system.notification"
                                     and ((x["data"].get("kind") or {}).get("agentId") == aid)), None)
                    det = [{"tur": "ozet", "etiket": "Alt agent özeti", "satirlar": satir}]
                    acik = "Alt agent işini bitirdi. Sonucu ana LLM'e kendiliğinden gitmez."
                    if bildirim:
                        det.append({"etiket": "CLI'ın ana agent'a gönderdiği bildirim", "metin": kes(bildirim["data"].get("content", "")), "vurgu": True})
                        acik += (" CLI ana agent'a bir bildirim gönderir ve siz bir şey yazmadan ana agent'ı yeniden çalıştırır; "
                                 "bu bildirim bir sonraki ana LLM çağrısına mesaj olarak eklenir.")
                    add("agent", "cli", 2, "bitti", f"Alt agent bitti: {ad}", acik, det, "donus", idx[id(e)])

        # CLI'ın kendisinin eklediği araç çağrıları (bildirimden sonra, LLM çağrısı olmadan)
        for e in ana:
            if id(e) in sentetik:
                kur[0] = idx[id(e)]
                for r in e["data"].get("toolRequests") or []:
                    arac_adimlari(r, cli_istedi=True)

        # Ana akıştaki hatalar (alt agent'ınkiler yukarıda)
        for e in ana:
            if e["type"] == "session.error":
                hd = e["data"]
                add("llm", "cli", 2, f"hata {hd.get('statusCode', '')}".strip(), "LLM çağrısı hata döndürdü",
                    f"Model çağrısı başarısız oldu ({hd.get('errorCode', hd.get('errorType', '?'))}).",
                    [{"etiket": "Hata", "metin": kes(hd.get("message", "")), "vurgu": True}], "donus", idx[id(e)])

        # Kalan hook'lar (agentStop, subagentStart/Stop, alt agent'ın hook'ları…) kendi yerlerinde
        for e in evs:
            if e["type"] == "hook.start" and e["data"]["hookInvocationId"] not in gosterilen_hook:
                hook_step(e, idx[id(e)])

        n_tools = sum(1 for c in comps.values() if not c.get("agentId"))
        n_llm = len(tur_listesi)
        son = son_metin or next((m["data"]["content"] for _, ms, _ in reversed(tur_listesi) for m in reversed(ms) if m["data"].get("content")), "")
        son_det = [{"etiket": "Ekranda görünen yanıt", "metin": kes(son)}]
        # Oynatıcının tanımadığı olay türleri: sessizce atlanmasın, eğitmen görsün
        taninmayan = {}
        for e in evs:
            t = e.get("type", "?")
            if t not in BILINEN_OLAYLAR and not any(t.startswith(o) for o in BILINEN_ONEKLER):
                taninmayan.setdefault(t, []).append(e)
        if taninmayan:
            uyarilar.extend((pn, t, len(v)) for t, v in taninmayan.items())
            son_det.append({"tur": "ozet", "seviye": 5, "etiket": "Oynatıcının göstermediği olaylar (logda var)",
                            "satirlar": [[t, f"{len(v)} kez · {kisalt(json.dumps(v[0].get('data', {}), ensure_ascii=False), 90)}"]
                                         for t, v in sorted(taninmayan.items())]})
        # Prompt'un toplam maliyeti: çağrıların (alt agent'lar dahil) nanoAIU toplamı + premium istek farkı
        tum_usage = [e for e in evs if e["type"] == "session.usage_record"]
        nano = [((x["data"]["usage"].get("copilotUsage") or {}).get("totalNanoAiu")) for x in tum_usage]
        if any(n is not None for n in nano):
            # Checkpoint, prompt'un kapanış hook'undan SONRA yazılır: prompt'un son olayından sonraki
            # ilk checkpoint bu prompt'a, kullanıcı mesajından önceki son checkpoint bir öncekine aittir.
            # Alt agent olayları (agentId) checkpoint'ten sonra da gelebilir; onlar sayılmaz, yoksa
            # bir sonraki prompt'un checkpoint'i alınır ve premium istek iki prompt'luk çıkar.
            cps = [e for e in events if e["type"] == "session.usage_checkpoint"]
            son_i = max((idx[id(e)] for e in p["events"] if not e.get("agentId")), default=user_i)
            sonra = [c for c in cps if idx[id(c)] > son_i]
            once = [c for c in cps if idx[id(c)] < user_i]
            prem = None
            if sonra:
                prem = (sonra[0]["data"].get("totalPremiumRequests") or 0) - ((once[-1]["data"].get("totalPremiumRequests") or 0) if once else 0)
            # nanoAIU ÷ 10⁹ = AI kredisi (GitHub Copilot SDK belgesindeki dönüşüm)
            toplam_kredi = sum(n or 0 for n in nano) / 1e9
            satirlar = [["LLM çağrısı", str(n_llm) + (f" (+{n_alt_llm} alt agent)" if n_alt_llm else "")],
                        ["Toplam maliyet", f"{tr_sayi(toplam_kredi)} AI kredisi (≈ {dolar(toplam_kredi)})" + (" · alt agent dahil" if n_alt_llm else "")],
                        ["Çağrı başına", " · ".join(tr_sayi((n or 0) / 1e9) + (" (alt)" if x.get("agentId") else "") for n, x in zip(nano, tum_usage)) + " AI kredisi"]]
            if aylik_kredi:
                satirlar.append(["Aylık haktan", f"%{tr_sayi(100 * toplam_kredi / aylik_kredi, 3)} kadarı ({tr_sayi(aylik_kredi, 0)} kredilik aylık hak)"])
            if prem is not None:
                satirlar.append(["Premium istek", f"{prem} (LLM çağrısı başına değil, prompt başına sayılır)"])
            son_det.append({"tur": "ozet", "etiket": "Bu prompt'un maliyeti", "satirlar": satirlar, "seviye": 3})
        add("cli", "user", 1, "yanıt", "Yanıt geliştiriciye gösterilir",
            f"Bu istek için {n_llm} LLM çağrısı" + (f" (alt agent'larda {n_alt_llm} tane daha)" if n_alt_llm else "")
            + (f" ve {n_tools} araç çağrısı" if n_tools else "") + " yapıldı.",
            son_det, "ana", float("inf"))
        steps.sort(key=lambda s_: s_["_k"])
        for s_ in steps:
            del s_["_k"]
        out.append({"no": pn, "metin": kisalt(user.get("content", ""), 60), "adimlar": steps,
                    "ozet": {"llm": n_llm, "arac": n_tools}})
    return {"model": model, "oran": round(ratio, 2), "promptlar": out, "uyarilar": uyarilar,
            "aylik_kredi": aylik_kredi, "kredi_usd": KREDI_USD}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events")
    ap.add_argument("-o", "--out", default="copilot-oynatici.html")
    ap.add_argument("--baslik", default="Copilot CLI içeriden: bir istek nasıl işlenir?")
    ap.add_argument("--promptlar", help="yalnızca bu prompt'ları al, ör. 2,5,7 veya 3-6 (numaralar --list çıktısındaki gibi)")
    ap.add_argument("--list", action="store_true", help="oturumdaki prompt'ları numaralarıyla listele ve çık")
    ap.add_argument("--aylik-kredi", type=float, help="planınızın aylık AI kredisi hakkı (ör. Pro 1500, Pro+ 7000); verilirse maliyetin bu hakkın ne kadarı olduğu da gösterilir")
    a = ap.parse_args()
    events = oku(a.events)
    if a.list:
        for i, p in enumerate(promptlara_bol(events), 1):
            ana = [e for e in p["events"] if not e.get("agentId")]
            n_llm = sum(1 for e in ana if e["type"] == "assistant.turn_start")
            n_tool = sum(1 for e in ana if e["type"] == "tool.execution_start")
            n_alt = sum(1 for e in p["events"] if e.get("agentId") and e["type"] == "assistant.turn_start")
            print(f"{i:>3}. {kisalt(p['user']['data'].get('content', ''), 70)}  ·  {n_llm} LLM, {n_tool} araç"
                  + (f" · alt agent: {n_alt} LLM" if n_alt else ""))
        return
    data = uret(events, a.aylik_kredi)
    for pn, t, n in data.pop("uyarilar", []):
        print(f"uyarı: prompt {pn}: oynatıcının tanımadığı olay '{t}' ({n} kez) gösterilmedi; Seviye 5'te son adımda listelenir.", file=sys.stderr)
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
