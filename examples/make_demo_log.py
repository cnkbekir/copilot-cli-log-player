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

# 4) alt agent (arka planda). Olay sırası Copilot CLI 1.0.114 gerçek logundaki yapıyı taklit eder:
#    task aracı hemen döner → ana agent turunu bitirir → alt agent kendi bağlamıyla (başka modelde) çalışır
#    → bitince CLI bildirim gönderir, LLM'e sormadan read_agent çağırır ve ana agent için yeni tur açar.
SUB_ID, SUB_MODEL, SUB_NAME = "todo-audit", "demo-flash", "todo-audit"
SUB_SYSTEM = "You are a general-purpose sub-agent of Copilot CLI. Complete the task you are given and report back.\n\n" + rules("Alt agent", 70)
sub_conv, sub_prev = 0, 0


def sub_emit(typ, data, dt_ms=5):
    emit(typ, data, dt_ms)
    events[-1]["agentId"] = SUB_ID


def sub_llm(turn, content="", tool_requests=(), out_tokens=80, think_ms=2400):
    """Alt agent'ın LLM çağrısı: ana konuşmayı görmez, kendi sistem prompt'u ve modeli var, önbelleği sıfırdan."""
    global sub_conv, sub_prev, total_nano
    sub_emit("assistant.turn_start", {"turnId": str(turn), "parentToolCallId": task_id}, 3)
    tin = round(len(SUB_SYSTEM) / CHARS_PER_TOKEN) + TOOL_TOKENS + round(sub_conv / CHARS_PER_TOKEN)
    read = min(sub_prev, tin - 3)
    write = tin - read - 3
    sub_prev = tin
    counts = {"input": 3, "cache_read": read, "cache_write": write, "output": out_tokens}
    nano = sum(counts[k] * PRICE[k] for k in counts) // 4   # küçük model: daha ucuz
    total_nano += nano
    sub_emit("session.usage_record", {"usage": {
        "model": SUB_MODEL, "inputTokens": tin, "outputTokens": out_tokens,
        "cacheReadTokens": read, "cacheWriteTokens": write, "cacheTtlSeconds": 1800, "reasoningTokens": 30,
        "toolTokenCount": TOOL_TOKENS, "availableToolCount": TOOL_COUNT,
        "copilotUsage": {"totalNanoAiu": nano, "tokenDetails": [
            {"tokenType": k, "tokenCount": v, "batchSize": 1_000_000, "costPerBatch": PRICE[k] * 250_000, "model": SUB_MODEL}
            for k, v in counts.items()]},
        "finishReason": "tool_calls" if tool_requests else "stop"}}, think_ms)
    reqs = [{"toolCallId": "call_" + uuid.uuid4().hex[:24], "name": n, "arguments": a, "type": "function"} for n, a in tool_requests]
    sub_conv += len(content) + sum(len(json.dumps(a)) for _, a in tool_requests)
    sub_emit("assistant.message", {"messageId": str(uuid.uuid4()), "content": content, "toolRequests": reqs, "model": SUB_MODEL}, 2)
    return reqs


t_pad(20000)
user("TODO yorumlarını tarayıp bir rapor hazırlaması için bir alt agent başlat")
TASK = ("src/ altındaki tüm TODO ve FIXME yorumlarını bul. Her biri için dosya, satır ve kısa açıklama ver; "
        "önem sırasına göre grupla. Kod değiştirme, yalnızca rapor yaz.")
r = llm_call(0, tool_requests=[("task", {"description": "TODO raporu", "prompt": TASK, "agent_type": "general-purpose",
                                         "name": SUB_NAME, "mode": "background"})],
             reasoning="Kullanıcı işi bir alt agent'a vermemi istiyor. Görevi tek başına anlaşılır yazmalıyım; alt agent bu konuşmayı görmeyecek.",
             out_tokens=120, think_ms=3000)
task_id = r[0]["toolCallId"]
emit("tool.execution_start", {"toolCallId": task_id, "toolName": "task", "arguments": r[0]["arguments"]}, 2)
hook("preToolUse", {"toolCalls": [{"id": task_id, "name": "task", "args": r[0]["arguments"]}]})
emit("subagent.started", {"toolCallId": task_id, "agentName": "general-purpose", "agentDisplayName": SUB_NAME,
                          "agentDescription": "TODO raporu", "model": MODEL, "agentType": "general-purpose",
                          "executionMode": "background"}, 4)
events[-1]["agentId"] = SUB_ID
STARTED = f"Agent started in background with agent_id: {SUB_ID}. You will be notified when it completes."
hook("postToolUse", {"toolCallId": task_id, "toolName": "task"}, dur=1)
emit("tool.execution_complete", {"toolCallId": task_id, "success": True, "result": {"content": STARTED}}, 2)
conv_chars += len(STARTED)
emit("assistant.turn_end", {"turnId": "0"})

# Alt agent başlar (ana agent'ın 2. turuyla aynı anda)
emit("assistant.turn_start", {"turnId": "1"}, 3)
sub_emit("subagent.configured", {"model": SUB_MODEL, "contextTier": "default", "multiTurn": False})
hook("subagentStart", {"agentId": SUB_ID, "agentType": "general-purpose", "agentName": "general-purpose"})
sub_tr = f"<current_datetime>{t.isoformat(timespec='seconds')}</current_datetime>\n\n{TASK}"
sub_emit("user.message", {"content": TASK, "transformedContent": sub_tr, "source": f"agent-{SUB_ID}", "parentAgentTaskId": task_id}, 3)
sub_emit("system.message", {"role": "system", "content": SUB_SYSTEM})
sub_conv += len(sub_tr)
# Ana agent kısa bir yanıtla turu bitirir; alt agent arka planda çalışmaya devam eder
# (llm_call turn_start'ı kendisi yazdığı için bu tur elle kuruldu: turn_start alt agent olaylarından önce gelmeli)
tin = SYS_TOKENS + TOOL_TOKENS + round(conv_chars / CHARS_PER_TOKEN)
read = min(prev_tin, tin - 3)
write = tin - read - 3
prev_tin = tin
counts = {"input": 3, "cache_read": read, "cache_write": write, "output": 35}
nano = sum(counts[k] * PRICE[k] for k in counts)
total_nano += nano
emit("session.usage_record", {"usage": {
    "model": MODEL, "inputTokens": tin, "outputTokens": 35, "cacheReadTokens": read, "cacheWriteTokens": write,
    "cacheTtlSeconds": 1800, "reasoningTokens": 10, "toolTokenCount": TOOL_TOKENS, "availableToolCount": TOOL_COUNT,
    "copilotUsage": {"totalNanoAiu": nano, "tokenDetails": [
        {"tokenType": k, "tokenCount": v, "batchSize": 1_000_000, "costPerBatch": PRICE[k] * 1_000_000, "model": MODEL}
        for k, v in counts.items()]}, "finishReason": "stop"}}, 1500)
WAIT = f"`{SUB_NAME}` alt agent'ını arka planda başlattım. Bitince sonucu özetleyeceğim."
emit("assistant.message", {"messageId": str(uuid.uuid4()), "content": WAIT, "toolRequests": [], "model": MODEL}, 2)
conv_chars += len(WAIT)
emit("assistant.turn_end", {"turnId": "1"})
hook("agentStop", {"stopReason": "end_turn"})

# Alt agent'ın kendi döngüsü: bir araç, sonra rapor
r = sub_llm(0, tool_requests=[("bash", {"command": "grep -rn 'TODO\\|FIXME' src/", "description": "Find TODO comments"})], out_tokens=60)
GREP = ("src/store.ts:41:  // TODO: localStorage hatasını kullanıcıya göster\n"
        "src/api/client.ts:17:  // FIXME: zaman aşımı sabit 30 sn, ayarlanabilir olmalı\n"
        "src/components/TodoList.tsx:88:  // TODO: boş liste için ayrı bir görünüm\n"
        "src/utils/date.ts:9:  // TODO: saat dilimi testleri eksik")
sub_emit("tool.execution_start", {"toolCallId": r[0]["toolCallId"], "toolName": "bash", "arguments": r[0]["arguments"]}, 2)
hook("preToolUse", {"toolCalls": [{"id": r[0]["toolCallId"], "name": "bash", "args": r[0]["arguments"]}]})
events[-2]["agentId"] = events[-1]["agentId"] = SUB_ID
t_pad(400)
hook("postToolUse", {"toolCallId": r[0]["toolCallId"], "toolName": "bash"}, dur=1)
events[-2]["agentId"] = events[-1]["agentId"] = SUB_ID
sub_emit("tool.execution_complete", {"toolCallId": r[0]["toolCallId"], "success": True, "result": {"content": GREP}}, 2)
sub_conv += len(GREP)
sub_emit("assistant.turn_end", {"turnId": "0", "parentToolCallId": task_id})
REPORT = ("## TODO raporu (4 kayıt)\n\n**Önemli**\n- `src/api/client.ts:17`: zaman aşımı sabit (FIXME)\n- `src/store.ts:41`: localStorage hatası kullanıcıya gösterilmiyor\n\n"
          "**Sonra**\n- `src/utils/date.ts:9`: saat dilimi testleri eksik\n- `src/components/TodoList.tsx:88`: boş liste görünümü")
sub_llm(1, REPORT, out_tokens=160, think_ms=3200)
sub_emit("assistant.turn_end", {"turnId": "1", "parentToolCallId": task_id})
sub_emit("subagent.completed", {"toolCallId": task_id, "agentName": "general-purpose", "agentDisplayName": SUB_NAME,
                                "model": MODEL, "firstDispatchedModel": SUB_MODEL, "totalToolCalls": 1, "durationMs": 6500})
emit("session.usage_checkpoint", {"totalNanoAiu": total_nano, "totalPremiumRequests": premium,
                                  "modelCacheState": [{"modelId": MODEL, "cacheTtlSeconds": 1800}]})
hook("subagentStop", {"agentId": SUB_ID, "agentName": "general-purpose", "agentType": "general-purpose", "stopReason": "end_turn"})

# CLI ana agent'ı kendisi uyandırır: bildirim + LLM'e sormadan read_agent
hook("userPromptSubmitted", {"prompt": ""})
NOTE = f"<system_notification>Agent \"{SUB_NAME}\" (general-purpose) has completed. Use read_agent to get the result.</system_notification>"
emit("system.notification", {"content": NOTE, "kind": {"type": "agent_idle", "agentId": SUB_ID, "agentType": "general-purpose",
                                                        "displayName": SUB_NAME, "description": "TODO raporu"}}, 4)
conv_chars += len(NOTE)
ra = {"toolCallId": str(uuid.uuid4()), "name": "read_agent", "arguments": {"agent_id": SUB_ID, "since_turn": 0}}
emit("assistant.message", {"messageId": str(uuid.uuid4()), "content": "", "toolRequests": [ra]}, 2)
emit("tool.execution_start", {"toolCallId": ra["toolCallId"], "toolName": "read_agent", "arguments": ra["arguments"]}, 2)
emit("tool.execution_complete", {"toolCallId": ra["toolCallId"], "success": True, "result": {"content": REPORT}}, 3)
conv_chars += len(REPORT)
llm_call(0, "Alt agent raporu bitirdi: **4 TODO/FIXME** var. En önemlisi `src/api/client.ts:17`'deki sabit zaman aşımı. "
            "İstersen önce onu düzelteyim.", out_tokens=70, think_ms=2200)
emit("assistant.turn_end", {"turnId": "0"})
hook("agentStop", {"stopReason": "end_turn"})
emit("session.usage_checkpoint", {"totalNanoAiu": total_nano, "totalPremiumRequests": premium + 1,
                                  "modelCacheState": [{"modelId": MODEL, "cacheTtlSeconds": 1800}]})

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo-events.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
print(f"{out} · {len(events)} olay")
