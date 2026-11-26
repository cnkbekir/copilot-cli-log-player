# AGENTS.md: bu repoda çalışmak için rehber

Kullanıcıya yönelik tanıtım `README.md`'de. Bu dosya, kodu değiştirecek bir geliştirici ya da kodlama agent'ı içindir.

## Ne yapıyor

GitHub Copilot CLI'ın oturum logunu (`~/.copilot/session-state/<id>/events.jsonl`, satır başına bir JSON olay) okuyup **tek dosyalık, bağımlılıksız bir HTML eğitim oynatıcısı** üretir. Hedef kullanım: bir eğitmenin projeksiyonda ←/→ ile adım adım anlatması.

- Python 3.9+, yalnızca standart kütüphane. Üretilen HTML internet gerektirmez (CDN, font, kütüphane yok).
- Arayüz ve açıklama metinleri **Türkçe**.

## Dosyalar ve veri akışı

```
events.jsonl
   │  copilot_log_core.oku()            satırları JSON olaylara çevirir
   │  copilot_log_core.promptlara_bol() olayları kullanıcı prompt'larına böler
   ▼
copilot_log_player.uret()             her prompt için "adım" listesi kurar
   │  json → copilot_player_template.html içindeki /*__DATA__*/null yerine
   ▼
tek dosya HTML (veri + CSS + vanilla JS)
```

| Dosya | Görev |
|---|---|
| `copilot_log_core.py` | Ortak: `oku`, `promptlara_bol`, `temizle` (ev dizini → `~`), `kisalt`, `sayi`, `sistem_bolumleri` (sistem prompt'unu üst düzey XML bölümlerine ayırır). |
| `copilot_log_player.py` | Ana giriş. `uret(events)` → `{model, oran, promptlar:[{no, metin, ozet, adimlar:[...]}]}`. `kredi(u)` maliyet tablosu. CLI: `--list`, `--promptlar`, `--baslik`, `-o`. |
| `copilot_player_template.html` | Oynatıcının tamamı. Veri `const DATA = /*__DATA__*/null;` satırına gömülür; bu yer tutucuyu değiştirmeyin. |
| `archify/` | İsteğe bağlı: aynı logdan [Archify](https://github.com/tt-a1i/archify) JSON'u (sequence, waterfall, tree). Ana oynatıcı bunlara bağlı değildir. |
| `examples/make_demo_log.py` | Tamamen uydurma demo log üretir (`examples/demo-events.jsonl`). README görselleri bundan. |

### Adım (step) yapısı

```json
{"from": "cli", "to": "llm", "seviye": 1, "etiket": "LLM çağrısı 2", "baslik": "...", "aciklama": "...",
 "tur": "llm|donus|arac|hook|ana|normal", "detay": [ blok, ... ]}
```

- Sütunlar (katılımcılar): `user, cli, hooks, llm, side, tools, skill, host, mcp, agent`. `host` = VS Code tarafındaki dış araçlar (`external_tool.*`), `side` = ana modelden ayrı küçük model (`model.*` olayları), `agent` = alt agent.
- `seviye` (1–5), adımın en erken hangi seviyede görüneceğidir. Şablondaki `lane()`, seviye 4'ün altında `skill/host/mcp/agent` sütunlarını `tools`'a katlar; akış hiçbir seviyede kopmaz.
- **Sıralama:** her adım üretilirken bir olay sırası anahtarı alır (`_k`) ve prompt'un adımları sonunda buna göre (kararlı) sıralanır. Bir LLM turunda üretilen adımlar o turun `assistant.turn_start` sırasını paylaşır (iç sıraları korunur); alt agent adımları, hata ve kalan hook'lar kendi olay sıralarında yer alır; son "yanıt" adımı her zaman sondadır.
- `detay` blokları: düz metin `{etiket, metin, vurgu?, seviye?}`, LLM girdisi `{tur:"baglam", ...}`, özet tablo `{tur:"ozet", etiket, satirlar:[[k,v]], seviye}`.

### LLM'e giden mesaj listesi nasıl kuruluyor

Logda "LLM'e giden istek" tek bir olay olarak **yoktur**. Her `assistant.turn_start` için, ondan önceki tüm `user.message` (`transformedContent`), `assistant.message` (metin + `toolRequests`), `tool.execution_complete`, `skill.invoked` ve `system.notification` olayları sırayla toplanır. Alt agent'a ait olaylar (`agentId` taşıyanlar) ana konuşmaya girmez; alt agent'ın çağrıları kendi olaylarından ayrıca kurulur. Bir önceki çağrıdan sonra eklenenler `yeni: true` olur.

### Token sayıları: ölçülen ve tahmin edilen

| Değer | Kaynak |
|---|---|
| Çağrı girdisi/çıktısı, önbellekten okunan/yazılan | `session.usage_record.usage` (ölçülmüş) |
| Araç tanımları | `toolTokenCount`, `availableToolCount` (ölçülmüş) |
| Sistem prompt'u | İlk çağrıdaki `inputTokens − toolTokenCount` (o anda geçmiş yok) |
| Karakter/token oranı | `(len(system) + len(ilk transformedContent)) / sistem_token` |
| Bölüm ve mesaj token'ları | Karakter ÷ oran (**tahmin**; arayüzde `≈` ile gösterilir) |
| Konuşmanın görünmeyen kısmı | Ölçülen konuşma (`girdi − araç tanımları − sistem`) − görünen mesajların tahmini. `olcekle()` bunu ayrı bir `rol:"gizli"` satırı yapar; tahmin ölçülenden büyükse tahminleri orantılı küçültür. Böylece mesajların toplamı her çağrıda ölçülen değere eşittir. |
| Maliyet | `copilotUsage.tokenDetails`: `tokenCount × costPerBatch / batchSize` = nanoAIU. `totalNanoAiu` ile tutarlı olduğu doğrulandı. |
| AI kredisi / dolar | nanoAIU ÷ 10⁹ = AI kredisi (GitHub Copilot SDK belgesindeki örnek; belge "faturayla doğrulayın" der), 1 kredi = 0,01 $ (fatura belgesi). `--aylik-kredi` verilirse aylık hakka oran. Bu dönüşümleri değiştirirseniz kaynağı arayüzdeki nota da yazın. |

## Logdaki tuzaklar (bunlar daha önce hata çıkardı)

1. **Prompt'u başlatan hook'lar `user.message`'tan önce gelir** (`userPromptSubmitted`, `userPromptTransformed`). `promptlara_bol` bu yüzden bir prompt'u `agentStop` hook'unun `hook.end` olayında kapatır ve aradaki olayları bir sonraki prompt'a ekler.
2. **`session.usage_checkpoint`, prompt'u kapatan `agentStop`'tan SONRA yazılır.** Bu yüzden bölme kuralı onu bir sonraki prompt'a atar. Premium istek farkı için prompt'un son olayından sonraki **ilk** checkpoint ile kullanıcı mesajından önceki **son** checkpoint karşılaştırılır.
3. **Oturum yeniden açılabilir** (`session.shutdown` → `session.resume`). Bir prompt'un olaylarında `session.resume` varsa öncesi (ör. `sessionEnd` hook'u) o prompt'a ait değildir.
4. **`preMcpToolCall` hook'unun girdisinde `toolCallId` yoktur.** Zamanca en yakın MCP `tool.execution_start` olayına bağlanır. Paralel MCP çağrılarında yanlış eşleşebilir (henüz örnekle test edilmedi).
5. **MCP araçları** `tool.execution_start` içinde `mcpServerName`, `mcpToolName`, `mcpTransport` ile tanınır. VS Code tarafı araçları ise aynı `toolCallId` ile bir `external_tool.requested` olayı olmasından tanınır.
6. **Büyük araç sonuçları** logda yalnızca "Output too large… Saved to: /var/folders/…" olarak durur. `temizle()` bu yolu `<geçici dosya>` yapar.
7. **Eski CLI sürümlerinde** (2026 baharı öncesi) hook, skill, MCP ve `cacheWriteTokens`/`copilotUsage` alanları yoktur. Kod bu alanlar yoksa ilgili bölümleri göstermez; **çökmemelidir**.
8. **Şifreli düşünce blokları:** `assistant.message.encryptedContent` (yanıt başına 6–18 bin karakter) sonraki çağrılara geri gönderilir ama okunamaz. Ölçülen konuşma token'ı ile görünen mesajlar arasındaki büyük farkın (gerçek logda ~%60) ana kaynağı budur.
9. **`ask_user` aracı:** LLM kullanıcıya soru sorar; cevap `human_response.recorded` olayı olarak gelir (aynı `toolCallId`). Oynatıcı bunu Geliştirici sütununa giden "soru" ve geri dönen "cevap" okları olarak gösterir.
10. **Başlamayan / bitmeyen araçlar:** `toolRequests`'te olup `tool.execution_start` olmayan çağrılar "(çalışmadı)" (reddedilmiş, hook engellemiş ya da iptal), başlayıp `tool.execution_complete` olmayanlar "sonuç yok", `success: false` olanlar "hata" olarak gösterilir.
11. **Bir LLM turu birden fazla `assistant.message` içerebilir** (ör. önce boş içerikli düşünce parçaları, sonra metin). Turlar `assistant.turn_start`/`turn_end` arasındaki mesajlar toplanarak kurulur (`turlar()`, `birlesik()`); mesajları turlarla sırayla eşlemek yanlış sonuç verir.
12. **Alt agent olayları üst düzeyde `agentId` taşır.** Alt agent'ın görevi de bir `user.message`'tır (`source: "agent-…"`); yeni prompt açmamalıdır. `promptlara_bol` bu olayları, `subagent.started` olayının geldiği prompt'a ekler (ana agent'ın turu kapandıktan sonra gelseler bile).
13. **Alt agent arka planda çalışabilir.** `task` aracı hemen "Agent started in background" sonucuyla döner; ana agent turunu bitirir (`agentStop`). Alt agent bitince CLI `system.notification` (`kind.agentId`) gönderir, sonra **LLM çağrısı olmadan** kendisi bir `read_agent` çağrısı ekler (turn dışında yazılan `assistant.message`, `toolCallId` UUID biçiminde) ve ana agent için yeni bir tur açar. Bildirim, alt agent'ı başlatan prompt'u yeniden açar.
14. **Alt agent başka bir modelde çalışabilir** (`session.usage_record.usage.model`, otomatik modda ayarlardaki modelden farklı olabilir). Karakter/token oranı bu yüzden alt agent'ın kendi ilk çağrısından ayrıca hesaplanır.
15. **Hatalar:** `session.error` (ör. `400 model_not_supported`) ve `subagent.failed`. Başarısız alt agent'ın turu için `session.usage_record` yoktur.
16. `events.jsonl` biçimi GitHub tarafından belgelenmemiştir. Copilot CLI `1.0.114` ile test edildi.

## Şablon (HTML/JS) kuralları

- **Durum:** `st = {p, lvl, i}` (örnek, seviye, adım), `ui = {theme, zoom, panelGizli, panelH, tumuAcik}`. localStorage anahtarları `copilot-player`, `copilot-player-ui`; her erişim `try/catch` içinde.
- **URL ile açılış** (`#ornek=&seviye=&adim=&panel=&aciklama=&tema=&sunum=`) localStorage'dan sonra uygulanır ve onu ezer. README görselleri bununla alınır.
- `render()` her adımda satırları **yeniden oluşturur**. Odak bilgisi satırlar silinmeden **önce** okunmalı (`hadRowFocus`), yoksa klavye odağı kaybolur.
- **Erişilebilirlik (ui-ux-pro-max incelemesiyle oturtuldu, bozmayın):**
  - Renk token'ları (`--c-*`) metin olarak da kullanılır; her iki temada zemine karşı en az 5:1. Yeni renk eklerken kontrastı hesaplayın.
  - Seçili düğmelerde yazı rengi `--on-accent`, renkli hap ve çubuklarda `--on-c` (koyu temada koyu yazı).
  - Satırlarda roving tabindex: yalnızca geçerli satır `tabindex=0`, `aria-current="step"`. Enter/Space ile seçilir.
  - Global Space/Enter işleyicisi, odak bir düğme/özet/`[role=button]`/`[role=separator]` üzerindeyken devreye girmez.
  - Ekran okuyucuya yalnızca `#live` bölgesi bildirir ("Adım n / m: başlık"); paneli `aria-live` yapmayın.
  - `prefers-reduced-motion` desteklenir; animasyon eklerseniz ona uyun.
  - İkonlar satır içi SVG, tek ikon ailesi (Phosphor çizgi stili), emoji yok. İkon düğmelerinde `aria-label` ve gerekiyorsa `aria-pressed` zorunlu.
- **Türkçe metin:** Sayıya göre değişen eklerden kaçının (`%99'i` / `%99'u` yerine "%99 kadarı"). Sayılar `toLocaleString("tr-TR")` ile.
- Başlık 1100 px üzerinde tek satıra sığmalı. `body` ızgarasındaki `grid-template-columns: minmax(0,1fr)` bunun için gerekli; kaldırırsanız sayfa yatay taşar.

## Test yöntemi (otomatik test yok)

1. **Demo logu yeniden üretin ve oynatıcıyı oluşturun:**
   ```bash
   python3 examples/make_demo_log.py
   python3 copilot_log_player.py examples/demo-events.jsonl -o /tmp/demo.html
   ```
2. **Yeniden düzenleme yaptıysanız çıktıyı karşılaştırın:** değişiklikten önce ve sonra aynı logla üretilen HTML'ler `cmp` ile birebir aynı olmalı.
3. **Tarayıcıda kontrol edin:** konsol hatası yok; ←/→, 1–5, A, H, T, F tuşları; Tab ile satıra gelip Enter; seviye 3'te LLM çağrısı adımında token/önbellek/maliyet bölümleri; 1100, 1280 ve 1440 px genişlikte başlık taşması yok; açık ve koyu tema.
   - Uyarı: Yalnızca `#…` kısmı değişen bir adrese gitmek sayfayı yeniden yüklemez. Yeni üretilmiş dosyayı test ederken sayfayı yenileyin ya da adrese `?v=n` ekleyin.
4. **Gerçek bir logla da deneyin**, ama çıktısını **commit etmeyin**.
5. README görsellerini değiştirdiyseniz yalnızca demo logdan alın (komutlar README ve üst klasör notlarında).

## Gizlilik ve commit kuralları

- **Gerçek oturum loglarını ve onlardan üretilen HTML'leri asla commit etmeyin.** `.gitignore`, `*.jsonl` ve `*.html` dosyalarını dışarıda tutar; istisnalar yalnızca `examples/demo-events.jsonl` ve `copilot_player_template.html`.
- Demo veride gerçek kullanıcı, repo, şirket veya yol adı kullanmayın (`/home/demo/projects/todo-app` gibi uydurma değerler kullanın).
- Commit yazarı `cnkbekir` ve GitHub noreply e-postası olmalı. Kişisel e-posta adresi hiçbir commit'te görünmemeli. Yeni klonda: `git config user.email 8302235+cnkbekir@users.noreply.github.com`. Push'tan önce `git log --format='%an <%ae>'` ile kontrol edin.
- Commit mesajları Türkçe, özellik başına ayrı commit.

## Yeni bir olay türü eklemek

`copilot_log_player.py`'deki `BILINEN_OLAYLAR` (ve `BILINEN_ONEKLER`) dışında kalan her olay türü, üretim sırasında terminale **uyarı** olarak basılır ve prompt'un son adımında (Seviye 5) "Oynatıcının göstermediği olaylar" tablosunda listelenir. Yeni bir türle karşılaşınca:

1. Gerçek bir logda olayın yapısına bakın; içeriği değil, anahtarları ve türleri yazdırın. Gösterilmeye değmeyen bir olaysa (oturum yönetimi, telemetri) yalnızca `BILINEN_OLAYLAR`'a ekleyin.
2. `uret()` içinde uygun yerde `add(from, to, seviye, etiket, baslik, aciklama, detay, tur)` çağırın. Seviyesini öğretim sırasına göre seçin (hook ve yan model 5, skill ve MCP 4, bağlam ayrıntısı 3).
3. Yeni bir sütun gerekiyorsa şablondaki `LANES` ve `ORDER`'a ekleyin; düşük seviyelerde nereye katlanacağını `lane()` içinde belirleyin.
4. `examples/make_demo_log.py`'ye aynı olayı ekleyip demo üzerinde test edin.
