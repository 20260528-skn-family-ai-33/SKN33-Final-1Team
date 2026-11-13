import { useCallback, useMemo, useState } from 'react';
import { readPixels, fitBoxOnPixels, FIT_TUNE, FIT_GENTLE } from './evidenceBoxFit.js';

const TUNES = { model: FIT_TUNE, ocr: FIT_GENTLE };

// 근거 상자를 화면에 그릴 때 그림에 맞춰 다듬는다(표시용. 판정·기록·내보내는 좌표는 그대로다).
// kind: 'model' — 검사 모델이 알려 준 상자. 헐겁거나 어긋날 수 있어 둘레의 글자에 적극적으로 맞춘다.
//       'ocr'   — 글자 인식이 읽은 상자. 이미 정확한 편이라 뚜렷이 어긋난 변만 맞춘다.
//       그 밖   — 맞추지 않는다.
// 돌려주는 값:
//   read(img 요소, src): 그림을 다 불러왔을 때(onLoad) 한 번 부른다. 화소를 읽어 둔다.
//   fit(상자 [x1, y1, x2, y2], 원본 픽셀): { box, changed }. 화소를 아직 못 읽었거나 읽을 수 없는 그림이면 받은 상자 그대로다.
export function useBoxFit(kind, src) {
  const tune = TUNES[kind] || null;
  const [pixels, setPixels] = useState(null);   // { src, value }
  const read = useCallback((element, source) => {
    if (tune && element) setPixels({ src: source, value: readPixels(element) });
  }, [tune]);
  const fit = useMemo(() => {
    const value = tune && pixels?.src === src ? pixels.value : null;
    const cache = new Map();
    return box => {
      if (!value || !Array.isArray(box)) return { box, changed: false };
      const key = box.join(',');
      if (!cache.has(key)) cache.set(key, fitBoxOnPixels(value, box, tune));
      return cache.get(key);
    };
  }, [tune, pixels, src]);
  return { read, fit };
}
