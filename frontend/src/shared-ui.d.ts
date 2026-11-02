declare module '*LandingPage.jsx' {
  import type { ComponentType } from 'react';
  const LandingPage: ComponentType<{ profile?: string }>;
  export default LandingPage;
}
declare module '*ImageStage.jsx' {
  import type { ComponentType, ReactNode } from 'react';
  const ImageStage: ComponentType<{
    src?: string; alt?: string; title?: string; subtitle?: string; width?: number; height?: number;
    overlays?: { id: string; bbox_xyxy: number[]; label?: string; text?: string }[];
    selectedOverlayId?: string; onOverlaySelect?: (id: string) => void; empty?: ReactNode; children?: ReactNode;
  }>;
  export default ImageStage;
}
