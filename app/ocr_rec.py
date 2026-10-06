"""Use a dedicated English GPU recognizer for Latin-only text crops.

Korean PP-OCRv4's English alphabet coverage is weaker than the CPU v5 model.
Routing uses observed script only, never contract words or expected answers.
"""
from copy import deepcopy
import re


class KoreanEnglishRecognizer:
    def __init__(self, korean, english):
        self.korean, self.english = korean, english
        self.session = korean.session

    def __call__(self, args):
        from rapidocr.ch_ppocr_rec import TextRecInput
        result = self.korean(args)
        indices = [i for i, text in enumerate(result.txts or ())
                   if text.isascii() and re.search('[a-zA-Z]', text)]
        if not indices:
            return result
        english = self.english(TextRecInput(img=[result.imgs[i] for i in indices],
                                           return_word_box=args.return_word_box))
        texts, scores, words = list(result.txts), list(result.scores), list(result.word_results)
        for j, i in enumerate(indices):
            text = english.txts[j]
            if text.isascii() and re.search('[a-zA-Z]', text) and english.scores[j] >= .5:
                texts[i], scores[i], words[i] = text, english.scores[j], english.word_results[j]
        result.txts, result.scores, result.word_results = tuple(texts), scores, tuple(words)
        result.elapse = (result.elapse or 0) + (english.elapse or 0)
        return result


def add_english_recognizer(engine, paths):
    from rapidocr import LangRec
    from rapidocr.ch_ppocr_rec import TextRecognizer
    config = deepcopy(engine.cfg.Rec)
    config.model_path = str(paths['en_rec'])
    config.rec_keys_path = str(paths['en_dict'])
    config.lang_type = LangRec.EN
    english = TextRecognizer(config)
    if english.session.device.type != engine.text_rec.session.device.type:
        raise RuntimeError('한국어·영어 OCR 장치가 일치하지 않습니다.')
    engine.text_rec = KoreanEnglishRecognizer(engine.text_rec, english)
