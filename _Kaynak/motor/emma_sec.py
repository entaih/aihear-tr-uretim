"""Emma (çok dilli edge sesi) ile Türkçe ders: her cümle için en Türkçe okunan öneki seçer.

Kullanım: python -X utf8 emma_sec.py [--hizli] <video_klasoru> [daha fazla klasör ...]
metin.json'da "seslendirici": "emma" olmalı (marka.json > ses > tr_emma).

Her cümle (okunuş sözlüğü uygulanmış hâliyle) sırayla şu öneklerle okutulur: "Türkçe:", "Türkçe olarak:", ...
Ölçütler (Odyoloji Viral/_uretim/thalita_sec.py ile aynı):
  - Whisper (medium) dili tr ve olasılık >= 0.90
  - metne ya da Emel'in (yerli Türkçe ses) okuyuşunun yazıya dökümüne benzerlik >= 0.90 (Whisper'ın terim yazım
    tuhaflıkları elenir; Emel yalnız metin doğrudan %95 tutmazsa okutulur)
  - güven (en düşük ortalama log olasılık) >= -0.45
  - 2 sn'lik pencerelerin en düşük Türkçe olasılığı >= 0.85 (cümle ortasında aksana kayma)
Geçen ilk önek seçilir; hiçbiri geçmezse en yüksek puanlı alınır ve satır "KONTROL" yazılır.
Seçimler <klasör>/_onek.json'a (yalnız varsayılandan farklı olanlar), satırlar <klasör>/_emma_sec.log'a yazılır.
Ses dosyaları <klasör>/_tts önbelleğine düşer; ses.py aynı dosyaları kullanır.

--hizli (toplu üretim, 23.11.2026): önce varsayılan önekle okunan cümle Whisper small ile yalnız dil tespitinden geçer
(tüm cümle tr >= 0.90 ve 4 sn'lik pencerelerin en düşüğü >= 0.85); geçmezse sıradaki önek denenir ("hızlı" satırı).
Hiçbiri geçmezse en iyisi alınır, satır KONTROL yazılır. Medium ve Emel kullanılmaz (755 video için tam seçim çok yavaş). Logda bulunan cümleler yeniden seçilmez (kaldığı yerden sürer).
"""
import asyncio
import difflib
import json
import pathlib
import re
import sys

import numpy as np
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

MOTOR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(MOTOR))
import ses as S  # noqa: E402

ONEKLER = ['Türkçe:', 'Türkçe olarak:', 'Türkçe konuşuyorum:', 'Şimdi Türkçe anlatıyorum:', 'Türkçe söylüyorum:']
ESIK = dict(olas=.90, ref=.90, guven=-.45, pencere=.85)
EMEL = {'motor': 'edge', 'ses': 'tr-TR-EmelNeural', 'hiz': '+0%'}   # yalnız karşılaştırma referansı, videoda kullanılmaz
_modeller = {}


def model_al(ad):
    if ad not in _modeller:
        yol = ad
        if ad == 'small':
            yol = pathlib.Path.home() / '.aihear_akademi' / 'whisper-small'
            if not (yol / 'model.bin').exists():
                from faster_whisper.utils import download_model
                download_model('small', output_dir=str(yol))
            yol = str(yol)
        _modeller[ad] = WhisperModel(yol, device='cpu', compute_type='int8')
    return _modeller[ad]


class _Medium:   # eski kullanım (E.model.detect_language ...) bozulmasın; model ilk kullanımda yüklenir
    def __getattr__(self, ad):
        return getattr(model_al('medium'), ad)


model = _Medium()


def n(s):
    return re.sub(r'[^\w]', '', s.lower().replace('İ', 'i').replace('I', 'ı'))


def benz(a, b):
    return difflib.SequenceMatcher(None, n(a), n(b)).ratio()


def dinle(x16):
    segs, info = model.transcribe(x16, beam_size=5, condition_on_previous_text=False)
    segs = list(segs)
    metin = ' '.join(s.text.strip() for s in segs)
    guven = min((s.avg_logprob for s in segs), default=-9)
    olas = info.language_probability if info.language == 'tr' else 0.0
    return metin, olas, guven, info.language


HIZLI_PENCERE = (64000, 48000, 24000)   # --hizli: 4 sn'lik pencere, 3 sn adım (Whisper her çağrıda 30 sn işler; 2 sn/0,75 sn çok yavaş)


def pencere(x16, mdl=None, olcu=(32000, 12000, 16000)):
    mdl = mdl or model
    W, H, EN_AZ = olcu
    en_dusuk = 1.0
    for b in range(0, max(1, len(x16) - EN_AZ + 1), H):
        p = x16[b:b + W]
        if len(p) < EN_AZ and b > 0:
            break
        _, _, hepsi = mdl.detect_language(p)
        en_dusuk = min(en_dusuk, dict(hepsi).get('tr', 0.0))
    return en_dusuk


def sentez(sesayar, sm, onek, tts):
    mp3, js = S.onbellek_yolu(sesayar, sm, tts, onek=onek)
    for deneme in range(4):   # yarıda kesilen edge sesi (S.EksikSes) silinip yeniden seslendirilir
        if not (mp3.exists() and js.exists()):
            asyncio.run(S.seslendir((onek + ' ' + sm).strip(), sesayar['ses'], sesayar['hiz'], mp3, js))
        try:
            x, _ = S.ses_oku(mp3, js, len(onek.split()))
            if len(x) > S.SR // 10 or deneme == 3:
                break
        except (S.EksikSes, ValueError) as e:
            if deneme == 3:
                raise
            print(f'eksik ses, yeniden: {e}', flush=True)
        mp3.unlink(missing_ok=True); js.unlink(missing_ok=True)
    return resample_poly(x, 1, S.SR // 16000).astype(np.float32)


def gecti(a):
    return a['olas'] >= ESIK['olas'] and a['ref'] >= ESIK['ref'] and a['guven'] >= ESIK['guven'] and a['pencere'] >= ESIK['pencere']


def hizli_gecti(x16):
    """Whisper small ile yalnız dil tespiti: (geçti mi, tüm cümle tr olasılığı, en düşük pencere)."""
    sm_model = model_al('small')
    d, p, _ = sm_model.detect_language(x16)
    tam = p if d == 'tr' else 0.0
    pen = pencere(x16, sm_model, HIZLI_PENCERE) if tam >= ESIK['olas'] else 0.0
    return tam >= ESIK['olas'] and pen >= ESIK['pencere'], tam, pen


def bitenler(log_yolu):
    if not log_yolu.exists():
        return set()
    return set(re.findall(r'\|\| okunan: (.*?)  \|\| ref: ', log_yolu.read_text(encoding='utf-8')))


def main(klasorler, hizli=False):
    for k in klasorler:
        k = pathlib.Path(k).resolve()
        v = json.loads((k / 'metin.json').read_text(encoding='utf-8'))
        sesayar = S.ses_ayari(v)
        assert sesayar.get('onek'), 'metin.json önekli bir ses seçmiyor ("seslendirici": "emma")'
        tts = k / '_tts'; tts.mkdir(exist_ok=True)
        oy = k / '_onek.json'
        secim = json.loads(oy.read_text(encoding='utf-8')) if oy.exists() else {}
        biten = bitenler(k / '_emma_sec.log')
        log = open(k / '_emma_sec.log', 'a', encoding='utf-8')
        i = 0
        for s in v['sahneler']:
            for c in s.get('cumleler', []):
                i += 1
                yazi, sm = S.cumle_al(c)
                sm = S.okunus(sm or yazi, v['dil'])
                if sm in biten:
                    continue
                if hizli:
                    # yalnız Whisper small dil tespiti; öneklerin ilki geçen seçilir, hiçbiri geçmezse en iyisi + KONTROL
                    adaylar = []
                    for onek in ONEKLER:
                        ok, tam, pen = hizli_gecti(sentez(sesayar, sm, onek, tts))
                        adaylar.append((ok, tam + pen, onek, tam, pen))
                        if ok:
                            break
                    ok, _, onek, tam, pen = next((a for a in adaylar if a[0]), max(adaylar, key=lambda a: a[1]))
                    if onek == sesayar['onek']:
                        secim.pop(sm, None)
                    else:
                        secim[sm] = onek
                    oy.write_text(json.dumps(secim, ensure_ascii=False, indent=1), encoding='utf-8')
                    satir = (f"{i:03d} {'hızlı' if ok else 'KONTROL':7s} aday={len(adaylar)} önek=\"{onek}\" dil=tr:{tam:.2f} "
                             f"pencere={pen:.2f} (small)  || okunan: {sm}  || ref: -")
                    print(satir, file=log, flush=True); print(satir, flush=True)
                    biten.add(sm)
                    continue
                ref_metin = None   # Emel okuyuşu yalnız gerektiğinde (metin doğrudan tutmazsa) yazıya dökülür: hız için
                adaylar = []
                for onek in ONEKLER:
                    x = sentez(sesayar, sm, onek, tts)
                    metin, olas, guven, dil = dinle(x)
                    mb = benz(metin, sm)
                    if mb >= .95:
                        ref = mb
                    else:
                        if ref_metin is None:
                            ref_metin, *_ = dinle(sentez(EMEL, sm, '', tts))
                        ref = max(mb, benz(metin, ref_metin))
                    # pencere taraması pahalı: diğer ölçütler zaten tutmuyorsa yapılmaz
                    on = olas >= ESIK['olas'] and guven >= ESIK['guven'] and ref >= ESIK['ref']
                    a = dict(onek=onek, olas=olas, ref=ref, metin_b=mb, guven=guven,
                             dil=dil, metin=metin, pencere=pencere(x) if on else 0.0)
                    a['puan'] = 2 * a['ref'] + a['metin_b'] + olas + .5 * max(-1.0, guven) + 1.5 * a['pencere']
                    adaylar.append(a)
                    if gecti(a):
                        break
                gecen = [a for a in adaylar if gecti(a)]
                en = gecen[0] if gecen else max(adaylar, key=lambda a: a['puan'])
                if en['onek'] == sesayar['onek']:
                    secim.pop(sm, None)
                else:
                    secim[sm] = en['onek']
                oy.write_text(json.dumps(secim, ensure_ascii=False, indent=1), encoding='utf-8')
                satir = (f"{i:03d} {'tamam' if gecen else 'KONTROL':7s} aday={len(adaylar)} önek=\"{en['onek']}\" "
                         f"dil={en['dil']}:{en['olas']:.2f} ref={en['ref']:.2f} metin={en['metin_b']:.2f} "
                         f"güven={en['guven']:.2f} pencere={en['pencere']:.2f} | {en['metin']}  || okunan: {sm}  || ref: {ref_metin or '-'}")
                print(satir, file=log, flush=True)
                print(satir, flush=True)
                biten.add(sm)
        print(f'== {k.name} bitti', file=log, flush=True)


if __name__ == '__main__':
    argv = sys.argv[1:]
    main([a for a in argv if a != '--hizli'], hizli='--hizli' in argv)
