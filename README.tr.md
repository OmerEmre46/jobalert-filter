# jobalert-filter

*[English](README.md)*

İş/staj ilanı bildirim mailleri hızla birikir ve çoğu yanlış seviye, yanlış alan ya da
yanlış şehirdir. **jobalert-filter** bu mailleri posta kutundan (salt okunur) okur,
içindeki her ilanı ayıklar, anahtar kelime kuralları ve **yerel** bir LLM ile *senin*
profiline göre süzer ve sana şunları verir:

- **`report.html`**: uygunluğa göre sıralı ilanlar, gerekçeleri ve başvuru linkiyle
- **`tracker.csv`**: başvurularını takip edeceğin tablo (başvuruldu / mülakat / teklif…)
- **günlük özet maili**: yalnızca yeni uygun ilanlar (isteğe bağlı)

Bilgisayarında çalışır ve ücretsizdir. LLM [Ollama](https://ollama.com) ile yerelde
çalıştığı için mailin bilgisayarından çıkmaz.

> LinkedIn ile bağlantılı değildir, LinkedIn tarafından onaylanmamıştır. Yalnızca
> LinkedIn'in sana zaten gönderdiği bildirim maillerini okur.

## Nasıl çalışır

```
posta kutusu (IMAP, salt okuma) ─► ilan maillerini ayrıştır ─► ilan no ile tekilleştir (SQLite)
   ─► kurallar  ("Senior", "5+ yıl", yanlış şehir… elenir; seviye + alan puanı)
   ─► yerel LLM (profilin ve kesin şartlarınla karşılaştırır, nedenini yazar)
   ─► report.html · tracker.csv · günlük özet
```

- Posta kutunda hiçbir şey değişmez: klasörler salt okunur açılır, mailler `BODY.PEEK`
  ile alınır, yani okundu olarak bile işaretlenmez.
- Beş ayrı mailde gelen aynı ilan bir kez görünür.
- LLM isteğe bağlıdır. Ollama yoksa ilanlar yalnızca kural puanı alır.
- LLM cevapları önbelleğe alınır; yalnızca profilin ya da model değişince yenilenir.

## Kurulum

Python 3.11+ gerekir.

```bash
git clone https://github.com/OmerEmre46/jobalert-filter.git
cd jobalert-filter
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -e .
jobalert init                 # config.yaml, profile.yaml ve .env oluşturur
```

Sonra:

1. **Posta kutusu**: `config.yaml` içindeki `mail:` bölümünü doldur (Gmail ve Outlook
   için aşağıya bak).
2. **Profil**: `profile.yaml` dosyasında kendini anlat: özet, beceriler, hedef roller,
   kesin şartlar ve anahtar kelime kuralları. Örnek dosya, Türkiye'de staj arayan bir
   bilgisayar mühendisliği öğrencisi için hazırlandı.
3. **LLM (önerilir)**: [Ollama](https://ollama.com/download)'yı kur, sonra
   ```bash
   ollama pull qwen2.5:7b-instruct
   ```
   8 GB ekran kartında rahat çalışır. Ekran kartı yoksa `qwen2.5:3b-instruct` kullan ve
   `llm.model` ayarını ona göre değiştir.
4. Her şeyi dene:
   ```bash
   jobalert auth     # Outlook'ta giriş yapar / Gmail'de şifreyi dener
   jobalert check
   jobalert run --since 14d --open
   ```

### Gmail

2 Adımlı Doğrulama'yı aç, bir **Uygulama Şifresi** oluştur
(Google Hesabı → Güvenlik → Uygulama şifreleri) ve `.env` dosyasına yaz:

```
JOBALERT_MAIL_PASSWORD=abcd efgh ijkl mnop
```

```yaml
mail:
  provider: gmail
  address: sen@gmail.com
```

Bir filtre yüzünden ilan mailleri gelen kutusuna düşmüyorsa `folders` listesine
`"[Gmail]/All Mail"` ekle.

### Outlook / Hotmail / Live

Microsoft, kişisel hesaplarda IMAP için şifreyle girişi kapattı; bu yüzden OAuth2
kullanılır. Bunun için bir kerelik, ücretsiz bir uygulama kaydı gerekir:

1. [Azure portal → Uygulama kayıtları](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
   sayfasında **Yeni kayıt**'a tıkla. (Azure önce bir dizin ya da ücretsiz hesap
   oluşturmanı isterse onu yap.)
2. Ad: `jobalert-filter`. Desteklenen hesap türleri: **Yalnızca kişisel Microsoft
   hesapları**. Yönlendirme URI'si boş kalsın. Kaydet.
3. **Kimlik doğrulama** → *Genel istemci akışlarına izin ver* → **Evet** → Kaydet.
4. **API izinleri** → İzin ekle → Microsoft Graph → Temsilci →
   `IMAP.AccessAsUser.All`, `SMTP.Send`, `offline_access` → Ekle.
5. **Uygulama (istemci) kimliğini** `config.yaml` dosyasına kopyala:

```yaml
mail:
  provider: outlook
  address: sen@hotmail.com
  ms_client_id: 00000000-0000-0000-0000-000000000000
```

**"Seçili kullanıcı hesabı 'Microsoft Services' kiracısında mevcut değil…"** hatası:
kişisel hesap, kendine ait bir dizini (kiracı) olmadan Azure portalını açamaz. Çözümler:

- [Azure for Students](https://azure.microsoft.com/free/students) (okul e-postası, kredi
  kartı gerekmez) ya da normal [Azure ücretsiz hesabı](https://azure.microsoft.com/free) ile
  kaydol; dizin oluşur, sonra uygulamayı yukarıdaki gibi kaydet; ya da
- uygulamayı, izni olan herhangi bir okul/iş hesabıyla kaydet ve hesap türü olarak
  **Herhangi bir kuruluş dizinindeki hesaplar ve kişisel Microsoft hesapları**'nı seç.
  Uygulamanın, mail kutusuyla aynı hesapta olması gerekmez; ya da
- OAuth'u tamamen atla: Outlook.com'da *Gönderen linkedin.com içeriyorsa → Gmail
  adresine **yeniden yönlendir (Redirect to)*** kuralı ekle ve Gmail kurulumunu kullan.
  *İlet (Forward)* değil *yeniden yönlendir* seç: iletmede gönderen senin adresin olur
  ve `sender_filter` ilan maillerini bulamaz.

`jobalert auth` komutunu çalıştır, verdiği linki açıp kodu gir. Yenileme anahtarı
`.jobalert/` klasöründe (git'e girmez) saklanır; sonraki çalıştırmalar sessizce giriş yapar.

## Günlük kullanım

| Komut | Ne yapar |
| --- | --- |
| `jobalert run [--open]` | mailleri çeker, puanlar, rapor ve tabloyu yeniler, özeti gönderir |
| `jobalert schedule install --times 09:00,18:00` | her gün otomatik çalıştırır (Windows Görev Zamanlayıcı; diğer sistemlerde cron satırı verir) |
| `jobalert status 4012345678 başvuruldu --note "CV gitti"` | bir ilanın durumunu günceller |
| `jobalert score --rescore` | profilde büyük değişiklikten sonra LLM'e tekrar sorar |
| `jobalert digest --dry-run` | özet mailini göndermeden gösterir |
| `jobalert fetch --since 30d --save-samples data/samples` | ayrıştırıcı testleri için ham mailleri saklar |

`tracker.csv` içindeki `status` ve `note` sütunlarını Excel'de de düzenleyebilirsin;
değişikliklerin bir sonraki çalıştırmada içeri alınır. Durumlar: `yeni, başvuruldu,
mülakat, teklif, ret, ilgilenmiyorum` (İngilizceleri de geçerli). `ilgilenmiyorum`
dediğin ilanlar *Elenen* sekmesine geçer.

Özet maili varsayılan olarak kapalı; açmak için `digest.enabled: true` yap. Mail
kendine, günde en fazla bir kez gider ve aynı ilan iki kez gönderilmez.

Windows notu: zamanlanmış görevler yalnızca oturumun açıkken ve bilgisayar uyanıkken
çalışır. Kaçan çalıştırma sorun olmaz; sonraki çalıştırma son seferden bu yana gelen
her şeyi alır.

## Filtreyi ayarlamak

- **Çok fazla alakasız ilan mı var?** `rules.reject_title` listesine terim ekle ya da
  yalnızca staj/junior başlıklarını tutmak için `rules.require_level: true` yap.
- **İyi ilanlar mı eleniyor?** *Elenen* sekmesinde ilanın altındaki gerekçeye bak; hangi
  kuralın ya da LLM'in hangi itirazının elediğini yazar. `min_score` değerini düşür ya
  da o kuralı gevşet.
- Kurallar, başlığın sadeleştirilmiş hâliyle eşleşen regex'lerdir: küçük harf, Türkçe
  harfler normalleştirilmiş (`ı/İ → i`, `ş → s`, …). Yani `kıdemli`, `KIDEMLİ` ile de eşleşir.
- `llm.rule_weight`, kural puanının LLM puanına karşı ağırlığını belirler.
- LLM "uygunluk" puanını kendisi vermez; küçük yerel modeller bunda güvenilir değil.
  Yalnızca ilanı bir alana koyar (`software`, `ai_data`, `web`, `it`,
  `other_technical`, `non_technical` ya da `unspecified`), puan da profilindeki
  `target_categories` listesine göre hesaplanır. Deneyim şartı ve kesin şartlar yalnızca
  ilanın tam metni varsa kontrol edilir; böylece model çıplak bir başlıktan şart uyduramaz.

## İlanın tam metnini okumak (isteğe bağlı, varsayılan kapalı)

İlan mailleri genelde yalnızca başlık, şirket ve konum içerir; asıl şartlar ilan
sayfasındadır. `enrich.enabled: true` yapılırsa, kurallardan geçmiş ilanların **herkese
açık, giriş yapılmamış** sayfası çekilir ve açıklama LLM'e verilir. Hız sınırlıdır
(`delay_seconds`, `max_per_run`) ve ilk retle durur.

**LinkedIn Kullanıcı Sözleşmesi siteye otomatik erişimi kısıtlar.** Bu özellik bu
yüzden varsayılan olarak kapalıdır; yalnızca bu riski kendin kabul ediyorsan aç.

## Gizlilik

`config.yaml`, `profile.yaml`, `.env`, `.jobalert/`, `data/` ve `output/` git'e girmez.
İlan linkleri LinkedIn'in takip parametreleri olmadan saklanır. Hiçbir veri kendi mail
sunucun ve yerel Ollama'n dışında bir yere gönderilmez.

## Katkı

LinkedIn mail şablonlarını zaman zaman değiştirir. Bir alan yanlış çıkıyorsa:

1. `jobalert fetch --since 7d --save-samples data/samples`
2. `python scripts/anonymize_fixture.py data/samples/0001.eml tests/fixtures/ornek.eml`.
   Adını, adresini ve takip linklerini temizler. **Commit'lemeden önce çıktıyı kontrol et.**
3. Beklediğin ilanlarla `tests/fixtures/ornek.expected.json` dosyasını ekle, sonra
   `pytest` geçene kadar `src/jobalert_filter/parse/linkedin.py` dosyasını düzelt.

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

### Yol haritası

- Diğer ilan gönderenleri için ayrıştırıcılar (Kariyer.net, Indeed, Glassdoor)
- İşveren modu: aynı kural + LLM çekirdeğiyle gelen başvuruları iş tanımına göre sıralama

## Lisans

MIT
