#!/usr/bin/env bash
# GitHub Actions işi: verilen dersleri üretir (ses -> dil kontrolü -> kapak + youtube.txt + video).
# Sonuçlar cikti/<ders>/ altına; durum satırları cikti/durum.txt'ye. yayin.py yerelde, indirildikten sonra çalışır.
# Kullanım: bash uret.sh <ders_klasoru> [<ders_klasoru> ...]   (klasörler Videolar/$AKADEMI_DIL/ altında; varsayılan EN)
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
DIL="${AKADEMI_DIL:-EN}"
# depo kökündeki .klasor iş akışındaki AKADEMI_DIL'i ezer (TR: Emma -> Thalita, 25.11.2026; iş akışı dosyası değişmeden)
[ -f .klasor ] && DIL="$(head -n 1 .klasor | tr -d '[:space:]')"
mkdir -p cikti
for ders in "$@"; do
  k="Videolar/$DIL/$ders"; c="cikti/$ders"; mkdir -p "$c"
  bas=$(date +%s)
  echo "::group::$ders"
  durum=TAMAM
  # Çok dilli edge sesli Türkçe (Emma/Thalita): önce cümle başına önek seçimi (Whisper small, emma_sec.py --hizli).
  # Hiçbir önekle Türkçe okunmayan (KONTROL) tek cümle varsa video üretilmez: yabancı aksan asla kabul edilmez.
  if grep -Eq '"seslendirici": "(emma|thalita)"' "$k/metin.json" && ! python -X utf8 _Kaynak/motor/emma_sec.py --hizli "$k" > "$k/_emma_sec_cikti.log" 2>&1; then
    durum="SECIM HATASI"
  elif [ -f "$k/_emma_sec.log" ] && grep -q ' KONTROL ' "$k/_emma_sec.log"; then
    durum="DIL SORUNU"
  elif ! python -X utf8 _Kaynak/motor/ses.py "$k" > "$k/_ses_log.txt" 2>&1; then
    durum="SES HATASI"
  else
    python -X utf8 _Kaynak/motor/dil_kontrol.py "$k" > "$k/_dil_kontrol.txt" 2>&1
    if ! grep -q "^0 şüpheli" "$k/_dil_kontrol.txt"; then
      durum="DIL SORUNU"
    elif ! python -X utf8 _Kaynak/motor/uret.py "$k" > "$k/_uret_log.txt" 2>&1; then
      durum="URETIM HATASI"
    elif [ "$DIL" = ES ]; then
      # çevirisi olmayan sahne etiketleri ("ES ETİKET EKSİK") için sahne kareleri; durumu etkilemez
      python -X utf8 _Kaynak/motor/uret.py "$k" --kontrol > "$k/_kontrol_log.txt" 2>&1 || true
    fi
  fi
  # üretilen metnin özeti (gh_ortak.metin_ozeti ile aynı): metin sonradan değişirse yerelde yeniden listeye girer
  python -c "import hashlib,json,sys;v=json.load(open(sys.argv[1],encoding='utf-8'));print(hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()[:16])" "$k/metin.json" > "$c/_metin.ozet"
  for f in "$k"/*.mp4 "$k/kapak.png" "$k/youtube.txt" "$k/zamanlama.json" "$k/_ses_log.txt" "$k/_dil_kontrol.txt" "$k/_uret_log.txt" "$k/_kontrol_log.txt" "$k/_onek.json" "$k/_emma_sec.log" "$k/_emma_sec_cikti.log"; do
    [ -f "$f" ] && cp "$f" "$c/"
  done
  echo "$durum $ders $(( ($(date +%s) - bas) / 60 )) dk" | tee -a cikti/durum.txt
  echo "::endgroup::"
done
