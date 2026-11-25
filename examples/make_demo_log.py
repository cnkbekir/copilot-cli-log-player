#!/usr/bin/env python3
"""Tamamen uydurma bir Copilot CLI oturum logu (events.jsonl) üretir.

README görselleri ve denemeler için: gerçek bir kullanıcıya, repoya veya şirkete
ait hiçbir veri içermez. Olay yapısı Copilot CLI 1.0.x loglarını taklit eder.

  python3 examples/make_demo_log.py            # examples/demo-events.jsonl
  python3 copilot_log_player.py examples/demo-events.jsonl -o demo.html
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

CWD = "/home/demo/projects/todo-app"
MODEL = "gpt-demo"
CHARS_PER_TOKEN = 4.6
TOOL_TOKENS, TOOL_COUNT = 9120, 104

t = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
events = []
conv_chars = 0
prev_tin = 0          # önbellekte duran önceki girdi (prompt cache)
total_nano = 0        # oturum boyunca harcanan nanoAIU
premium = 0           # prompt başına sayılan premium istek
# Demo birim fiyatları (nanoAIU / token); oranlar gerçek loglardaki yapıyı taklit eder
PRICE = {"input": 10_000, "cache_read": 1_000, "cache_write": 12_500, "output": 50_000}


def emit(typ, data, dt_ms=5):
    global t
    t += timedelta(milliseconds=dt_ms)
    events.append({"type": typ, "data": data, "id": str(uuid.uuid4()),
                   "timestamp": t.isoformat(timespec="milliseconds").replace("+00:00", "Z")})


def rules(title, n):
    lines = [f"- {title} kuralı {i + 1}: değişiklikleri küçük tut, mevcut stile uy, gerekmedikçe yeni bağımlılık ekleme." for i in range(n)]
    return "\n".join(lines)


skills = {
    "react-ui": "Projedeki React bileşen kütüphanesiyle arayüz yazma rehberi.",
    "testing": "Vitest ile birim test yazma ve çalıştırma kuralları.",
    "api-client": "Backend API istemcisini kullanma ve hata yönetimi.",
    "git-workflow": "Dal, commit mesajı ve PR açma kuralları.",
    "i18n": "Çeviri anahtarları ekleme ve dil dosyalarını güncelleme.",
}
skill_list = "\n".join(f"<skill>\n<name>{k}</name>\n<description>{v}</description>\n</skill>" for k, v in skills.items())
SYSTEM = f"""You are an AI assistant using Copilot CLI. You help users with software engineering tasks.

<code_change_instructions>
{rules("Kod değişikliği", 40)}
</code_change_instructions>

<environment_limitations>
{rules("Ortam", 12)}
</environment_limitations>

<tools>
<bash>
{rules("bash aracı", 60)}
</bash>
<view>
{rules("view aracı", 10)}
</view>
<skill>
Skill'leri `skill` aracıyla yükle. Yalnızca adı ve açıklaması aşağıda; içeriği gerektiğinde yüklenir.
</skill>
<available_skills>
{skill_list}
{rules("Skill seçimi", 25)}
</available_skills>
{rules("Genel araç", 120)}
</tools>

<custom_instruction>
# GitHub Copilot Instructions
Bu bir demo todo uygulamasıdır (React + TypeScript + Vitest).
{rules("Proje", 150)}
</custom_instruction>

<custom_instruction>
# AGENTS.md
{rules("Agent", 45)}
</custom_instruction>

<editing_constraints>
{rules("Düzenleme", 15)}
</editing_constraints>
"""
SYS_TOKENS = round(len(SYSTEM) / CHARS_PER_TOKEN)


def hook(htype, inp, output=None, dur=3):
    hid = str(uuid.uuid4())
    emit("hook.start", {"hookInvocationId": hid, "hookType": htype, "input": dict(inp, cwd=CWD)}, 2)
    end = {"hookInvocationId": hid, "hookType": htype, "success": True}
    if output:
        end["output"] = output
    emit("hook.end", end, dur)


def llm_call(turn, content="", tool_requests=(), reasoning="", out_tokens=60, think_ms=2800, cache=True):
    global conv_chars, prev_tin, total_nano
    emit("assistant.turn_start", {"turnId": str(turn)}, 3)
    tin = SYS_TOKENS + TOOL_TOKENS + round(conv_chars / CHARS_PER_TOKEN)
    read = min(prev_tin, tin - 3) if cache else 0   # önceki çağrının girdisi önbellekte
    write = tin - read - 3                          # yeni eklenen kısım önbelleğe yazılır
    prev_tin = tin
    counts = {"input": 3, "cache_read": read, "cache_write": write, "output": out_tokens}
    nano = sum(counts[k] * PRICE[k] for k in counts)
    total_nano += nano
    emit("session.usage_record", {"usage": {
        "model": MODEL, "inputTokens": tin, "outputTokens": out_tokens,
        "cacheReadTokens": read, "cacheWriteTokens": write, "cacheTtlSeconds": 1800, "reasoningTokens": 40,
        "toolTokenCount": TOOL_TOKENS, "availableToolCount": TOOL_COUNT,
        "copilotUsage": {"totalNanoAiu": nano, "tokenDetails": [
            {"tokenType": k, "tokenCount": v, "batchSize": 1_000_000, "costPerBatch": PRICE[k] * 1_000_000, "model": MODEL}
            for k, v in counts.items()]},
        "finishReason": "tool_calls" if tool_requests else "stop"}}, think_ms)
    reqs = []
    for name, args in tool_requests:
        reqs.append({"toolCallId": "call_" + uuid.uuid4().hex[:24], "name": name, "arguments": args, "type": "function"})
        conv_chars += len(json.dumps(args))
    conv_chars += len(content)
    emit("assistant.message", {"messageId": str(uuid.uuid4()), "content": content, "toolRequests": reqs,
                               "reasoningText": reasoning, "model": MODEL}, 2)
    return reqs


def tool(req, result, external=False, mcp=None, dur=120, pre_mcp=True):
    global conv_chars
    cid, name = req["toolCallId"], req["name"]
    start = {"toolCallId": cid, "toolName": name, "arguments": req["arguments"]}
    if mcp:
        start.update({"mcpServerName": mcp, "mcpToolName": name.split(f"{mcp}-", 1)[-1], "mcpTransport": "http"})
    emit("tool.execution_start", start, 2)
    hook("preToolUse", {"toolCalls": [{"id": cid, "name": name, "args": req["arguments"]}]})
    if mcp and pre_mcp:
        hook("preMcpToolCall", {"serverName": mcp, "toolName": start["mcpToolName"]}, dur=1)
    if external:
        emit("external_tool.requested", {"toolCallId": cid, "toolName": name, "arguments": req["arguments"]}, 2)
        emit("external_tool.completed", {}, dur)
    else:
        t_pad(dur)
    hook("postToolUse", {"toolCallId": cid, "toolName": name}, dur=1)
    emit("tool.execution_complete", {"toolCallId": cid, "success": True, "result": {"content": result}}, 2)
    conv_chars += len(result)


def t_pad(ms):
    global t
    t += timedelta(milliseconds=ms)


def user(text, extra=""):
    global conv_chars
    hook("userPromptSubmitted", {"prompt": text}, {"additionalContext": extra} if extra else None)
    tr = f"<current_datetime>{t.isoformat(timespec='seconds')}</current_datetime>\n\n{text}" + (f"\n\n<system_reminder>\n{extra}\n</system_reminder>" if extra else "")
    hook("userPromptTransformed", {"prompt": text, "transformedPrompt": tr})
    emit("user.message", {"content": text, "transformedContent": tr}, 3)
    conv_chars += len(tr)


def stop():
    global premium
    hook("agentStop", {"stopReason": "end_turn"})
    premium += 1  # premium istek prompt başına sayılır, LLM çağrısı başına değil
    emit("session.usage_checkpoint", {"totalNanoAiu": total_nano, "totalPremiumRequests": premium,
                                      "modelCacheState": [{"modelId": MODEL, "cacheTtlSeconds": 1800}]})


# ── Oturum ────────────────────────────────────────────────────────────────
emit("session.start", {"sessionId": "demo-session", "copilotVersion": "1.0.114-demo", "selectedModel": "auto", "context": {"cwd": CWD}}, 0)
hook("sessionStart", {"source": "new"})
emit("session.auto_mode_resolved", {"chosenModel": MODEL, "selectionReason": "Demo: otomatik model seçimi."})
emit("system.message", {"role": "system", "content": SYSTEM})

# 1) merhaba
user("merhaba")
llm_call(0, "Merhaba! todo-app projesinde nasıl yardımcı olabilirim?", out_tokens=22, think_ms=1600, cache=False)
emit("assistant.turn_end", {"turnId": "0"})
stop()

# 2) testleri çalıştır
t_pad(9000)
user("testleri çalıştır ve sonucu özetle")
emit("model.turn_started", {"kind": "turn_started", "model": "mini-classifier"})
emit("model.messages_snapshot", {"kind": "messages_snapshot", "messages": [
    {"role": "system", "content": "Task: Detect whether the CURRENT MESSAGE expresses frustration with an AI coding assistant. Answer yes or no."},
    {"role": "user", "content": "testleri çalıştır ve sonucu özetle"}, {"role": "assistant", "content": "no"}]})
emit("model.response", {"kind": "response", "response": "no"})
r = llm_call(0, tool_requests=[("bash", {"command": "npm test -- --run", "description": "Run unit tests"})],
             reasoning="Kullanıcı testleri istiyor. package.json'daki test betiğini tek seferlik modda çalıştırmalıyım.", out_tokens=70)
tool(r[0], "> todo-app@1.0.0 test\n> vitest --run\n\n ✓ src/store.test.ts (6 tests) 12ms\n ✓ src/utils/date.test.ts (4 tests) 5ms\n ✗ src/components/TodoItem.test.tsx (3 tests | 1 failed)\n   × siler butonuna basınca onDelete çağrılır\n     → expected \"onDelete\" to be called 1 time, but got 0 times\n\n Test Files  1 failed | 2 passed (3)\n      Tests  1 failed | 12 passed (13)", dur=2400)
emit("assistant.turn_end", {"turnId": "0"})
r = llm_call(1, tool_requests=[("view", {"path": "src/components/TodoItem.tsx"})],
             reasoning="Başarısız test sil butonuyla ilgili. Bileşene bakıp nedenini bulmalıyım.", out_tokens=45)
tool(r[0], "1 export function TodoItem({ todo, onToggle, onDelete }: Props) {\n2   return (\n3     <li>\n4       <input type=\"checkbox\" checked={todo.done} onChange={() => onToggle(todo.id)} />\n5       <span>{todo.title}</span>\n6       <button aria-label=\"Sil\">×</button>\n7     </li>\n8   );\n9 }", dur=30)
emit("assistant.turn_end", {"turnId": "1"})
llm_call(2, "Testler çalıştı: **13 testten 12'si geçti, 1'i başarısız.**\n\nBaşarısız olan: `TodoItem` → \"siler butonuna basınca onDelete çağrılır\". Nedeni: `TodoItem.tsx` satır 6'daki sil butonunda `onClick` yok, bu yüzden `onDelete` hiç çağrılmıyor.\n\nDüzeltme: `<button aria-label=\"Sil\" onClick={() => onDelete(todo.id)}>×</button>`. İstersen uygulayayım.",
         reasoning="Nedeni buldum: butonda onClick eksik. Özetleyip düzeltme önermeliyim.", out_tokens=190, think_ms=3400)
emit("assistant.turn_end", {"turnId": "2"})
stop()

# 3) skill + MCP
t_pad(15000)
user("react-ui skill'ini kullanarak ui-docs MCP'sinden uygun bir onay diyaloğu bileşeni bul")
r = llm_call(0, tool_requests=[("skill", {"skill": "react-ui"})], out_tokens=18, think_ms=2100)
tool(r[0], 'Skill "react-ui" loaded successfully. Follow the instructions in the skill context.', dur=15)
SKILL_BODY = "# React UI\n\nBu projede arayüz bileşenleri `@demo/ui` kütüphanesinden gelir.\n\n## Kurallar\n" + rules("Bileşen", 30) + "\n\n## Bileşen arama\nBileşen belgelerini `ui-docs` MCP sunucusundaki `search-components` aracıyla ara. Sonuç çok büyükse `search` parametresiyle daralt."
emit("skill.invoked", {"name": "react-ui", "path": f"{CWD}/.github/skills/react-ui/SKILL.md", "content": SKILL_BODY}, 2)
conv_chars += len(SKILL_BODY)
emit("assistant.turn_end", {"turnId": "0"})
r = llm_call(1, tool_requests=[("tool_search_tool", {"query": "ui-docs MCP search components"})],
             reasoning="Skill, ui-docs MCP'sindeki search-components aracını öneriyor. Önce aracı bulmalıyım; henüz bağlamımda yok.", out_tokens=60, think_ms=2600)
tool(r[0], '["ui-docs-search-components","ui-docs-get-component","ui-docs-get-example"]', external=True, dur=600)
emit("assistant.turn_end", {"turnId": "1"})
r = llm_call(2, tool_requests=[("ui-docs-search-components", {})], reasoning="Aracı buldum. Önce tüm bileşenleri listeleyeyim.", out_tokens=30, think_ms=3100)
tool(r[0], "Output too large to read at once (48.2 KB). Saved to: /tmp/copilot-tool-output-demo.txt\nConsider narrowing the request with the `search` parameter.", mcp="ui-docs", dur=540)
emit("assistant.turn_end", {"turnId": "2"})
r = llm_call(3, tool_requests=[("ui-docs-search-components", {"search": "confirm dialog"})],
             reasoning="Sonuç çok büyük döndü. Skill'in dediği gibi search parametresiyle daraltmalıyım.", out_tokens=40, think_ms=2700)
tool(r[0], json.dumps({"components": [
    {"name": "ConfirmDialog", "description": "Onay/iptal butonlu modal diyalog.", "props": ["title", "message", "onConfirm", "onCancel", "danger"]},
    {"name": "AlertDialog", "description": "Tek butonlu bilgilendirme diyaloğu.", "props": ["title", "message", "onClose"]}]}, ensure_ascii=False, indent=2),
     mcp="ui-docs", dur=210)
emit("assistant.turn_end", {"turnId": "3"})
llm_call(4, "`ui-docs` MCP'sinde aradım ve **`ConfirmDialog`** bileşenini buldum: onay/iptal butonlu modal, silme gibi işlemler için `danger` özelliği var.\n\n```tsx\n<ConfirmDialog title=\"Silinsin mi?\" message={todo.title} danger onConfirm={() => onDelete(todo.id)} onCancel={close} />\n```",
         out_tokens=150, think_ms=2900)
emit("assistant.turn_end", {"turnId": "4"})
stop()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo-events.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
print(f"{out} · {len(events)} olay")
