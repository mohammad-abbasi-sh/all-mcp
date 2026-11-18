# all-mcp — Hepsi İçin Tek MCP Sunucusu

**Mohammad Abbasi'nin 60 MCP sunucusunun tamamını içeren tek MCP sunucusu — Türkiye (32), Almanya (27), Bonbama (1) — tek yapılandırma girdisiyle 324 araç. Her şeyi sorun; doğru MCP'ye kendisi bağlanır.**

> «Berlin'den Münih'e tren bileti fiyatı ne kadar?»
> → `flixtrain_search_trains` aracını bulur, çağırır ve gerçek seferleri ve fiyatları döndürür.

## Nasıl çalışıyor?

Bu sunucunun içinde sihirli bir tespit ve kendi LLM çağrısı yok (bu, sahip olmadığınız bir API anahtarı gerektirir). Tespiti kendi yapay zekânız yapar — Claude Desktop, Claude Code, Cursor:

1. **`search_tools(query)`** — 324 aracın tamamında dört dilde anahtar kelime araması; konu eşleşmesi, yer eşleşmesi (soruda geçen şehirler/ülkeler) ve kayıtlı konumunuza göre sıralanır.
2. **`call_tool(server, tool, arguments)`** — seçilen aracı doğru alt sunucuda çalıştırır (`uvx` ile anında başlatılır) ve gerçek sonucu döndürür.

Yönlendirici yalnızca 6 araç sunar: `search_tools`, `call_tool`, `ask`, `list_servers`, `set_location`, `meta_health`. Bir arama + bir çağrı — fazladan gidiş-geliş yok. `ask(question)`, tek adımda cevap verir: kendi anahtarınızla isteğe bağlı bir yönlendirme LLM'i (Gemini / OpenAI / Anthropic / OpenRouter / yerel Ollama) kullanır, anahtar yoksa en iyi arama sonucuna geri döner. Web sitesi sohbeti için yerel HTTP köprüsü de vardır: `uvx all-mcp --http --port 8765` (yalnızca localhost; `/api/ask`, `/api/search`, `/api/call`, `/api/health`).

## Kurulum

`uvx` gerekir ([uv kurulumu](https://docs.astral.sh/uv/)). Claude Desktop yapılandırmanıza ekleyin:

```json
{
  "mcpServers": {
    "all-mcp": {
      "command": "uvx",
      "args": ["all-mcp"],
      "env": {
        "USER_COUNTRY": "TR"
      }
    }
  }
}
```

`USER_COUNTRY` isteğe bağlıdır (`DE` / `TR` / `IR`): yer belirtmeyen belirsiz soruların ülkenizin servislerine yönlenmesini sağlar. Alternatif olarak `set_location` aracını kullanın (yalnızca `~/.config/all-mcp/location.json` dosyasına, makinenizde saklanır).

## Sıralama önceliği

1. **Sorudaki açık yer adları kazanır.** «Münih'ten Hamburg'a otobüs» kayıtlı konumunuz Türkiye olsa bile Almanya'ya gider.
2. **Kayıtlı konum beraberliği bozar.** Yersiz «otobüs bileti fiyatı» sorusu `USER_COUNTRY=TR` ile Türk seçenekleri önce getirir.
3. **İpucu yoksa, konum yoksa:** her iki ülkenin seçenekleri de sıralı döner — yapay zekâ tahmin etmek yerine size sorar.

## Gizlilik

Her şey **kendi makinenizde** çalışır: stdio, arka uç yok, hesap yok, API anahtarı yok, telemetri yok. Konumunuz makineden asla ayrılmaz. Yazma eylemleri (sepet, favoriler) açık `confirm=true` gerektirir; ödeme asla otomatikleşmez.

## Henüz PyPI'de olmayan sunucular (6)

`paribu-mcp`, `diyanet-mcp`, `osym-mcp`, `heise-mcp`, `zeit-mcp`, `welt-mcp` — dizinde varlar ama paketleri henüz yayınlanmadı; `call_tool` takılmak yerine açık bir hata döndürür.

Lisans: PolyForm Noncommercial 1.0.0. Mohammad Abbasi — https://almanibama.ir · https://mabbasi.ir
